"""数据源基类"""
import asyncio
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any
from datetime import datetime


class BaseDataSource(ABC):
    """基础数据源类"""

    def __init__(self, name: str):
        self.name = name
        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time: Optional[datetime] = None

    @property
    def failure_rate(self) -> float:
        """计算失败率"""
        total = self.failure_count + self.success_count
        if total == 0:
            return 0.0
        return self.failure_count / total

    @abstractmethod
    async def fetch(self, stock_code: str) -> Optional[Dict[str, Any]]:
        """获取数据"""
        pass

    @abstractmethod
    def is_available(self) -> bool:
        """数据源是否可用"""
        pass

    def record_success(self):
        """记录成功"""
        self.success_count += 1
        self.failure_count = max(0, self.failure_count - 1)

    def record_failure(self):
        """记录失败"""
        self.failure_count += 1
        self.last_failure_time = datetime.now()

    async def fetch_with_retry(self, stock_code: str, max_retries: int = 2) -> Optional[Dict[str, Any]]:
        """带重试的数据获取"""
        for attempt in range(max_retries + 1):
            try:
                result = await self.fetch(stock_code)
                if result:
                    self.record_success()
                    return result
            except Exception:
                pass

            if attempt < max_retries:
                await asyncio.sleep(0.1 * (attempt + 1))  # 指数退避

        self.record_failure()
        return None
