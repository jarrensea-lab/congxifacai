"""报告引擎调度器 — 串联模板+渲染+渠道输出"""
from app.report_engine.templates.premarket import build_premarket_report_data
from app.report_engine.templates.closing import build_closing_report_data
from app.report_engine.templates.midday import build_midday_report_data
from app.report_engine.templates.afternoon_risk import build_afternoon_risk_data
from app.report_engine.renderers.markdown_card import (
    build_premarket_card, build_closing_card, build_midday_card, build_afternoon_risk_card
)
from app.report_engine.renderers.feishu_doc import create_doc_from_markdown
from app.report_engine.renderers.bitable_writer import bitable_writer
from app.utils.logger import logger


class ReportEngine:
    """报告引擎 — 统一调度入口"""

    def __init__(self):
        from app.config import settings
        self.webhook_url = getattr(settings, "FEISHU_WEBHOOK_URL", "")

    async def push_premarket(self, date: str, decision: dict, positions: list[dict],
                             risk_level: int) -> bool:
        """盘前策略全渠道推送"""
        try:
            data = build_premarket_report_data(date, decision, positions, risk_level)

            # 1. 飞书消息卡片
            card_md = build_premarket_card(data)
            self._webhook_push(f"🐕 旺财V7 盘前策略 [R{risk_level}]", card_md)


            # 2. 写入多维表格
            if bitable_writer._available():
                bitable_writer.write_strategy_overview(
                    date=date, risk_level=risk_level,
                    direction=data.market_direction,
                    confidence=data.confidence,
                    position_advice=f"R{risk_level}",
                    top_sectors=data.top_sectors,
                )
                rec_dicts = [r.dict() for r in data.recommendations]
                bitable_writer.write_stock_pool(rec_dicts)
                pos_dicts = [{
                    "code": p.code, "name": p.name, "quantity": p.quantity,
                    "cost_price": p.cost_price, "current_price": p.current_price,
                    "profit_pct": p.profit_pct, "market_value": p.market_value,
                    "risk_level": p.risk_level,
                } for p in data.positions]
                bitable_writer.write_position_monitor(pos_dicts)

            logger.info(f"盘前策略全渠道推送完成 R{risk_level}")
            return True
        except Exception as e:
            logger.error(f"盘前策略推送异常: {e}", exc_info=True)
            return False

    async def push_closing(self, date: str, positions: list[dict], alerts: list[dict],
                           performance: dict, market_summary: str, system_health: dict,
                           preview: str = "") -> bool:
        """收盘全景全渠道推送"""
        try:
            data = build_closing_report_data(date, positions, alerts, performance,
                                             market_summary, system_health, preview)

            # 1. 飞书消息卡片
            card_md = build_closing_card(data)
            self._webhook_push("📊 旺财V7 收盘全景报告", card_md)

            # 2. 生成飞书云文档
            doc_md = f"""# 收盘全景报告 - {date}

## 今日交易回顾
- 日盈亏: ¥{performance.get('daily_pnl', 0):+,.2f}
- 累计盈亏: ¥{performance.get('cumulative_pnl', 0):+,.2f}
- 持仓数: {performance.get('position_count', 0)}
- 总资产: ¥{performance.get('total_assets', 0):,.2f}

## 持仓表现
"""
            for p in positions[:10]:
                cost = p.get("cost", p.get("cost_price", 0)) or 0
                curr = p.get("current_price", p.get("market_price", 0)) or 0
                pnl = ((curr - cost) / max(cost, 1)) * 100
                doc_md += f"- {p.get('name', '')}({p.get('code', '')}): {pnl:+.2f}%\n"

            doc_md += f"\n## 明日预告\n{preview or '—'}\n"
            doc_url = create_doc_from_markdown(f"收盘全景_{date}", doc_md)
            if doc_url:
                logger.info(f"收盘文档已创建: {doc_url}")

            # 2. 写入多维表格
            if bitable_writer._available():
                bitable_writer.write_performance(performance)

            logger.info("收盘全景全渠道推送完成")
            return True
        except Exception as e:
            logger.error(f"收盘全景推送异常: {e}", exc_info=True)
            return False

    async def push_midday(self, date: str, market_summary: str, positions: list[dict],
                          afternoon_tip: str = "") -> bool:
        """午盘快报推送"""
        try:
            data = build_midday_report_data(date, market_summary, positions, afternoon_tip)
            card_md = build_midday_card(data)
            self._webhook_push("🌤️ 旺财V7 午盘快报", card_md)

            if bitable_writer._available():
                pos_dicts = [{
                    "code": p.code, "name": p.name, "quantity": p.quantity,
                    "cost_price": p.cost_price, "current_price": p.current_price,
                    "profit_pct": p.profit_pct, "market_value": p.market_value,
                    "risk_level": p.risk_level,
                } for p in data.positions]
                bitable_writer.write_position_monitor(pos_dicts)
            return True
        except Exception as e:
            logger.error(f"午盘推送异常: {e}")
            return False

    async def push_afternoon_risk(self, date: str, positions: list[dict], alerts: list[dict],
                                  performance: dict) -> bool:
        """午后风控推送（仅预警时推送）"""
        try:
            data = build_afternoon_risk_data(date, positions, alerts, performance)
            has_alerts = any(a.level in ("high", "mid") for a in data.alerts)

            if has_alerts:
                card_md = build_afternoon_risk_card(data)
                self._webhook_push("🛡️ 旺财V7 午后风控告警", card_md)

            if bitable_writer._available():
                for a in data.alerts:
                    bitable_writer.write_risk_alert({
                        "stock_code": a.stock_code, "stock_name": a.stock_name,
                        "alert_type": a.alert_type, "level": a.level,
                        "message": a.message, "suggestion": a.suggestion,
                    })
            return True
        except Exception as e:
            logger.error(f"午后风控推送异常: {e}")
            return False

    def _webhook_push(self, title: str, content_md: str):
        """同步飞书webhook推送"""
        if not self.webhook_url or "YOUR_WEBHOOK" in self.webhook_url:
            return
        try:
            import requests
            payload = {
                "msg_type": "interactive",
                "card": {
                    "header": {
                        "title": {"tag": "plain_text", "content": title},
                        "template": "red" if "风控" in title or "告警" in title else "blue",
                    },
                    "elements": [{"tag": "markdown", "content": content_md[:3000]}],
                },
            }
            resp = requests.post(self.webhook_url, json=payload, timeout=15)
            if resp.status_code == 200:
                logger.info(f"Webhook OK: {title}")
            else:
                logger.warning(f"Webhook FAIL: {resp.status_code} - {title}")
        except Exception as e:
            logger.warning(f"Webhook异常: {e}")


# 全局单例
report_engine = ReportEngine()
