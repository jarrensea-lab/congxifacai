"""数据源路由器 — 多源容错 + 熔断保护"""
import asyncio
import time
from typing import Optional, Dict, Any, List
from app.data_sources.tencent_client import TencentDataSource
from app.utils.logger import logger


class DataSourceRouter:
    """多数据源智能路由 — Tencent(富数据)→Sina(基础价格)→熔断绕过

    注意: Eastmoney 已从行情路由链移除 (push2 API 不可达).
    Sina 仅提供基础价格, 降级时 PE/PB/市值/换手率等字段不可用.
    """

    def __init__(self):
        self.sources: List = [
            TencentDataSource(),
        ]
        self.circuit_breaker = {}  # source_name -> (fail_count, last_fail_time, open_until)

    def _circuit_ok(self, source_name: str) -> bool:
        entry = self.circuit_breaker.get(source_name)
        if not entry:
            return True
        fail_count, last_fail, open_until = entry
        if time.time() < open_until:
            return False
        return True

    def _record_failure(self, source_name: str):
        entry = self.circuit_breaker.get(source_name, (0, 0, 0))
        fail_count = entry[0] + 1
        cooldown = min(30 * (2 ** (fail_count - 1)), 300)  # 指数退避, 最长5分钟
        self.circuit_breaker[source_name] = (fail_count, time.time(), time.time() + cooldown)
        logger.warning(f"数据源 {source_name} 第{fail_count}次失败，熔断{cooldown}秒")

    def _record_success(self, source_name: str):
        if source_name in self.circuit_breaker:
            entry = self.circuit_breaker[source_name]
            self.circuit_breaker[source_name] = (max(0, entry[0] - 1), entry[1], entry[2])

    async def fetch(self, stock_code: str, priority: str = "balanced") -> Optional[Dict[str, Any]]:
        for source in self.sources:
            if not self._circuit_ok(source.name):
                logger.debug(f"数据源 {source.name} 熔断中，跳过")
                continue

            try:
                result = await asyncio.wait_for(source.fetch(stock_code), timeout=8)
                if result and result.get("price", 0) > 0:
                    self._record_success(source.name)
                    return result
                else:
                    self._record_failure(source.name)
            except Exception as e:
                logger.debug(f"数据源 {source.name} 异常: {e}")
                self._record_failure(source.name)

        # 全部失败，尝试忽略熔断
        for source in self.sources:
            try:
                result = await asyncio.wait_for(source.fetch(stock_code), timeout=5)
                if result:
                    logger.info(f"熔断绕过: 使用 {source.name}")
                    return result
            except Exception:
                continue

        return None
