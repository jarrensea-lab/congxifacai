"""飞书多维表格写入模块 — 封装 lark-cli base 操作"""
import subprocess
import json
from datetime import datetime
from typing import Optional
from app.config import settings
from app.utils.logger import logger


class BitableWriter:
    """多维表格写入器，管理6张维度的数据写入"""

    def __init__(self):
        self.lark_cli = getattr(settings, "LARK_CLI_PATH", "/Users/zhuchenyuan/.npm-global/bin/lark-cli")
        self.app_token = getattr(settings, "FEISHU_BITABLE_APP_TOKEN", "")
        self._tables = {
            "strategy": getattr(settings, "FEISHU_TABLE_STRATEGY", ""),
            "stock_pool": getattr(settings, "FEISHU_TABLE_STOCK_POOL", ""),
            "positions": getattr(settings, "FEISHU_TABLE_POSITIONS", ""),
            "indices": getattr(settings, "FEISHU_TABLE_INDICES", ""),
            "risk": getattr(settings, "FEISHU_TABLE_RISK", ""),
            "performance": getattr(settings, "FEISHU_TABLE_PERFORMANCE", ""),
        }

    def _available(self) -> bool:
        return bool(self.app_token) and any(self._tables.values())

    def _create_record(self, table_key: str, fields: dict) -> bool:
        """向指定表新增一条记录"""
        table_id = self._tables.get(table_key)
        if not table_id or not self.app_token:
            logger.warning(f"多维表格 {table_key} 未配置，跳过")
            return False
        fields_json = json.dumps(fields, ensure_ascii=False)
        try:
            result = subprocess.run(
                [self.lark_cli, "base", "+record-create",
                 "--app-token", self.app_token,
                 "--table-id", table_id,
                 "--fields", fields_json],
                capture_output=True, text=True, timeout=15
            )
            ok = result.returncode == 0
            if ok:
                logger.info(f"Bitable写入成功: {table_key}")
            else:
                logger.warning(f"Bitable写入失败: {table_key} - {result.stderr[:200]}")
            return ok
        except Exception as e:
            logger.error(f"Bitable异常: {table_key} - {e}")
            return False

    def write_strategy_overview(self, date: str, risk_level: int, direction: str,
                                confidence: int, position_advice: str,
                                top_sectors: Optional[list[str]] = None,
                                status: str = "进行中") -> bool:
        """写入策略总览"""
        return self._create_record("strategy", {
            "日期": date,
            "风险等级": f"R{risk_level}",
            "市场方向": direction,
            "置信度": confidence,
            "仓位建议": position_advice,
            "看好板块": ", ".join(top_sectors or []),
            "状态": status,
        })

    def write_stock_pool(self, recommendations: list[dict]) -> bool:
        """写入标的池"""
        ok = True
        for rec in recommendations:
            fields = {
                "代码": rec.get("code", ""),
                "名称": rec.get("name", ""),
                "策略类型": "短线" if rec.get("strategy_type") == "short_term" else "中线",
                "买入区间": rec.get("buy_range", ""),
                "止损": rec.get("stop_loss", ""),
                "目标": rec.get("target", ""),
                "趋势评分": rec.get("trend_score", 5),
                "题材标签": ", ".join(rec.get("concept_tags", [])),
                "技术面信号": rec.get("technical_signals", ""),
                "AI理由": rec.get("reason", ""),
                "适合人群": rec.get("beginner_guide", ""),
                "推荐日期": rec.get("recommend_date", ""),
            }
            if not self._create_record("stock_pool", fields):
                ok = False
        return ok

    def write_position_monitor(self, positions: list[dict]) -> bool:
        """写入持仓监控"""
        ok = True
        now = datetime.now().strftime("%H:%M")
        for pos in positions:
            fields = {
                "代码": pos.get("code", ""),
                "名称": pos.get("name", ""),
                "持仓量": pos.get("quantity", 0),
                "成本": pos.get("cost_price", 0),
                "现价": pos.get("current_price", 0),
                "盈亏%": f"{pos.get('profit_pct', 0):+.2f}%",
                "市值": pos.get("market_value", 0),
                "风控状态": pos.get("risk_level", "normal"),
                "更新时间": now,
            }
            if not self._create_record("positions", fields):
                ok = False
        return ok

    def write_indices(self, indices: dict) -> bool:
        """写入市场指数"""
        ok = True
        now = datetime.now().strftime("%H:%M")
        name_map = {"sh000001": "上证指数", "sz399001": "深证成指", "sz399006": "创业板指"}
        for code, data in indices.items():
            fields = {
                "指数名称": name_map.get(code, code),
                "当前点位": data.get("price", 0),
                "涨跌幅": f"{data.get('change_pct', 0):+.2f}%",
                "更新时间": now,
            }
            if not self._create_record("indices", fields):
                ok = False
        return ok

    def write_risk_alert(self, alert: dict) -> bool:
        """写入风险预警"""
        return self._create_record("risk", {
            "时间": datetime.now().strftime("%H:%M"),
            "标的": f"{alert.get('stock_name', '')}({alert.get('stock_code', '')})",
            "预警类型": alert.get("alert_type", ""),
            "级别": alert.get("level", "low"),
            "消息": alert.get("message", ""),
            "建议": alert.get("suggestion", ""),
            "处理状态": "待处理",
        })

    def write_performance(self, perf: dict) -> bool:
        """写入绩效追踪"""
        return self._create_record("performance", {
            "日期": datetime.now().strftime("%Y-%m-%d"),
            "日盈亏": perf.get("daily_pnl", 0),
            "日盈亏%": f"{perf.get('daily_pnl_pct', 0):+.2f}%",
            "累计盈亏": perf.get("cumulative_pnl", 0),
            "胜率": f"{perf.get('win_rate', 0):.1f}%",
            "持仓数": perf.get("position_count", 0),
            "总资产": perf.get("total_assets", 0),
            "可用现金": perf.get("available_cash", 0),
        })


# 全局单例
bitable_writer = BitableWriter()
