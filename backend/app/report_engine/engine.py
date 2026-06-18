"""报告引擎调度器 — 串联模板+渲染+渠道输出 + 推送状态追踪 + 指数退避重试"""
import asyncio
import random
from datetime import date as date_func
from typing import Optional

from app.report_engine.templates.premarket import build_premarket_report_data
from app.report_engine.templates.closing import build_closing_report_data
from app.report_engine.templates.midday import build_midday_report_data
from app.report_engine.templates.afternoon_risk import build_afternoon_risk_data
from app.report_engine.renderers.markdown_card import (
    build_premarket_card, build_closing_card,
    build_midday_card, build_afternoon_risk_card,
)
from app.report_engine.renderers.feishu_doc import create_doc_from_markdown
from app.report_engine.renderers.bitable_writer import bitable_writer
from app.services.push_tracker import push_tracker, compute_retry_delay
from app.utils.logger import logger


class ReportEngine:
    """报告引擎 — 统一调度入口 + 推送状态追踪"""

    # 飞书 Webhook 重试上限
    WEBHOOK_MAX_RETRIES = 3
    WEBHOOK_BASE_DELAY = 10  # 秒

    def __init__(self):
        from app.config import settings
        self.webhook_url = getattr(settings, "FEISHU_WEBHOOK_URL", "")

    async def push_premarket(self, date: str, decision: dict, positions: list[dict],
                             risk_level: int) -> bool:
        """盘前策略全渠道推送"""
        record_id = push_tracker.record("premarket", date, status="pending")
        try:
            data = build_premarket_report_data(date, decision, positions, risk_level)

            # 1. 飞书消息卡片（带重试）
            card_md = build_premarket_card(data)
            webhook_ok = self._webhook_push_with_retry(
                f"🐕 旺财V7 盘前策略 [R{risk_level}]", card_md,
            )
            if not webhook_ok:
                logger.warning("盘前策略 webhook 推送失败，继续写入多维表格")

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

            push_tracker.mark_success(record_id)
            logger.info(f"盘前策略全渠道推送完成 R{risk_level}")
            return True
        except Exception as e:
            err_msg = str(e)[:500]
            push_tracker.mark_failed(record_id, err_msg)
            logger.error(f"盘前策略推送异常: {e}", exc_info=True)
            return False

    async def push_closing(self, date: str, positions: list[dict], alerts: list[dict],
                           performance: dict, market_summary: str, system_health: dict,
                           preview: str = "") -> bool:
        """收盘全景全渠道推送"""
        record_id = push_tracker.record("daily_report", date, status="pending")
        try:
            data = build_closing_report_data(date, positions, alerts, performance,
                                             market_summary, system_health, preview)

            # 1. 飞书消息卡片（带重试）
            card_md = build_closing_card(data)
            webhook_ok = self._webhook_push_with_retry(
                "📊 旺财V7 收盘全景报告", card_md,
            )
            if not webhook_ok:
                logger.warning("收盘全景 webhook 推送失败，继续其他渠道")

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

            # 云文档创建失败不影响主流程
            try:
                doc_url = create_doc_from_markdown(f"收盘全景_{date}", doc_md)
                if doc_url:
                    logger.info(f"收盘文档已创建: {doc_url}")
            except Exception as e:
                logger.warning(f"收盘文档创建异常（不影响主流程）: {e}")

            # 3. 写入多维表格
            if bitable_writer._available():
                bitable_writer.write_performance(performance)

            push_tracker.mark_success(record_id)
            logger.info("收盘全景全渠道推送完成")
            return True
        except Exception as e:
            err_msg = str(e)[:500]
            push_tracker.mark_failed(record_id, err_msg)
            logger.error(f"收盘全景推送异常: {e}", exc_info=True)
            return False

    async def push_midday(self, date: str, market_summary: str, positions: list[dict],
                          afternoon_tip: str = "") -> bool:
        """午盘快报推送"""
        record_id = push_tracker.record("midday", date, status="pending")
        try:
            data = build_midday_report_data(date, market_summary, positions, afternoon_tip)
            card_md = build_midday_card(data)
            self._webhook_push_with_retry("🌤️ 旺财V7 午盘快报", card_md)

            if bitable_writer._available():
                pos_dicts = [{
                    "code": p.code, "name": p.name, "quantity": p.quantity,
                    "cost_price": p.cost_price, "current_price": p.current_price,
                    "profit_pct": p.profit_pct, "market_value": p.market_value,
                    "risk_level": p.risk_level,
                } for p in data.positions]
                bitable_writer.write_position_monitor(pos_dicts)

            push_tracker.mark_success(record_id)
            return True
        except Exception as e:
            err_msg = str(e)[:500]
            push_tracker.mark_failed(record_id, err_msg)
            logger.error(f"午盘推送异常: {e}")
            return False

    async def push_afternoon_risk(self, date: str, positions: list[dict], alerts: list[dict],
                                  performance: dict) -> bool:
        """午后风控推送（无预警时也推送精简版）"""
        record_id = push_tracker.record("afternoon", date, status="pending")
        try:
            data = build_afternoon_risk_data(date, positions, alerts, performance)
            has_alerts = any(a.level in ("high", "mid") for a in data.alerts)

            # 有预警时红色标题，无预警时绿色标题
            title = "🛡️ 旺财V7 午后风控告警" if has_alerts else "✅ 旺财V7 午后风控检查"
            card_md = build_afternoon_risk_card(data)
            self._webhook_push_with_retry(title, card_md)

            if bitable_writer._available():
                for a in data.alerts:
                    bitable_writer.write_risk_alert({
                        "stock_code": a.stock_code, "stock_name": a.stock_name,
                        "alert_type": a.alert_type, "level": a.level,
                        "message": a.message, "suggestion": a.suggestion,
                    })

            push_tracker.mark_success(record_id)
            return True
        except Exception as e:
            err_msg = str(e)[:500]
            push_tracker.mark_failed(record_id, err_msg)
            logger.error(f"午后风控推送异常: {e}")
            return False

    # ── 带指数退避重试的 Webhook ──────────────────────────

    def _webhook_push_with_retry(self, title: str, content_md: str,
                                 max_retries: Optional[int] = None) -> bool:
        """Webhook 推送 + 指数退避重试

        分布式场景下避免惊群效应，重试间隔加入随机抖动。
        """
        if not self.webhook_url or "YOUR_WEBHOOK" in self.webhook_url:
            return False

        max_retries = max_retries or self.WEBHOOK_MAX_RETRIES

        for attempt in range(1 + max_retries):
            try:
                import requests
                payload = {
                    "msg_type": "interactive",
                    "card": {
                        "header": {
                            "title": {"tag": "plain_text", "content": title},
                            "template": (
                                "red" if any(kw in title for kw in ("风控", "告警"))
                                else "green" if any(kw in title for kw in ("检查", "无忧"))
                                else "blue"
                            ),
                        },
                        "elements": [{"tag": "markdown", "content": content_md[:3000]}],
                    },
                }
                resp = requests.post(self.webhook_url, json=payload, timeout=15)

                if resp.status_code == 200:
                    logger.info(f"Webhook OK (attempt {attempt+1}): {title}")
                    return True

                logger.warning(
                    f"Webhook FAIL (attempt {attempt+1}/{max_retries+1}): "
                    f"{resp.status_code} - {title}"
                )

                # 4xx 错误不重试（客户端问题）
                if 400 <= resp.status_code < 500:
                    logger.warning(f"Webhook 4xx 不重试: {resp.status_code}")
                    return False

            except requests.exceptions.Timeout:
                logger.warning(f"Webhook 超时 (attempt {attempt+1}): {title}")
            except requests.exceptions.ConnectionError as e:
                logger.warning(f"Webhook 连接失败 (attempt {attempt+1}): {e}")
            except Exception as e:
                logger.warning(f"Webhook 异常 (attempt {attempt+1}): {e}")

            # 最后一次尝试也失败了，不再等待
            if attempt == max_retries:
                logger.error(f"Webhook 已达最大重试次数 ({max_retries})，放弃: {title}")
                return False

            # 指数退避 + 随机抖动
            delay = compute_retry_delay(attempt + 1, self.WEBHOOK_BASE_DELAY, 120)
            logger.info(f"Webhook 将在 {delay:.0f}s 后重试...")
            import time
            time.sleep(delay)

        return False

    # ── 兼容旧调用（无重试） ──────────────────────────────

    def _webhook_push(self, title: str, content_md: str):
        """旧版同步 webhook（无重试，仅内部兼容）"""
        self._webhook_push_with_retry(title, content_md, max_retries=0)


# 全局单例
report_engine = ReportEngine()
