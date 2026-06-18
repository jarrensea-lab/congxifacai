"""实时监控服务 — 多源数据 + 分层缓存 + 多维度风控"""
import json
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any, Set
from fastapi import WebSocket
from app.models import TradeLog
from app.database import SessionLocal
from app.data_sources.data_router import DataSourceRouter
from app.trading_engine.position import PositionManager
from app.utils.tiered_cache import tiered_cache


RISK_WEIGHTS = {
    "price_drop": 0.30,
    "volume_spike": 0.15,
    "pe_extreme": 0.10,
    "limit_approach": 0.10,
    "turnover_anomaly": 0.08,
    "amplitude_anomaly": 0.05,
    "stop_loss_hit": 0.30,
    "target_reached": 0.20,
}


class MonitorService:
    """增强监控服务 — 多源容错 + 多维度风控"""

    def __init__(self):
        self.router = DataSourceRouter()
        self.websocket_clients: Set[WebSocket] = set()

    async def connect_websocket(self, websocket: WebSocket):
        await websocket.accept()
        self.websocket_clients.add(websocket)

    def disconnect_websocket(self, websocket: WebSocket):
        self.websocket_clients.discard(websocket)

    async def get_active_holdings(self) -> List[str]:
        """获取当前持仓的股票代码列表 (使用 Position 表)"""
        db = SessionLocal()
        try:
            return PositionManager.get_holdings_codes(db)
        finally:
            db.close()

    async def get_realtime_data(self, stock_code: str) -> Optional[Dict[str, Any]]:
        cache_key = f"rt_{stock_code}"
        cached = tiered_cache.get(cache_key)
        if cached:
            return cached

        data = await self.router.fetch(stock_code)
        if data:
            tiered_cache.set(cache_key, data, tier=1)
        return data

    async def check_risk(self, position: dict, realtime_data: Dict[str, Any],
                         db_session=None) -> Optional[Dict[str, Any]]:
        """多维度风险检查"""
        if not realtime_data or not realtime_data.get("price"):
            return None

        code = position.get("code", "")
        name = position.get("name", "")
        cost_price = position.get("cost_price", 0)
        stop_loss = position.get("stop_loss_price")
        target_price = position.get("target_price")
        position_id = position.get("id")

        price = realtime_data.get("price", 0)
        last_close = realtime_data.get("last_close", price)

        risk_items = []
        total_score = 0

        # 1. 价格跌幅风险
        if cost_price > 0 and price > 0:
            loss_pct = ((price - cost_price) / cost_price) * 100
            if loss_pct < -7:
                risk_items.append({"type": "price_drop", "level": "high", "score": 0.35,
                                   "msg": f"价格暴跌 {abs(loss_pct):.1f}%（成本¥{cost_price:.2f}）"})
                total_score += 0.35
            elif loss_pct < -5:
                risk_items.append({"type": "price_drop", "level": "high", "score": 0.25,
                                   "msg": f"价格大幅下跌 {abs(loss_pct):.1f}%"})
                total_score += 0.25
            elif loss_pct < -3:
                risk_items.append({"type": "price_drop", "level": "medium", "score": 0.15,
                                   "msg": f"价格下跌 {abs(loss_pct):.1f}%"})
                total_score += 0.15

        # 2. 日内涨跌幅异常（相对昨收）
        change_pct = realtime_data.get("change_pct", 0)
        if abs(change_pct) > 9:
            risk_items.append({"type": "limit_approach", "level": "high", "score": 0.15,
                               "msg": f"日内波动 {change_pct:+.1f}%，接近涨跌停"})
            total_score += 0.15
        elif abs(change_pct) > 7:
            risk_items.append({"type": "limit_approach", "level": "medium", "score": 0.10,
                               "msg": f"日内波动 {change_pct:+.1f}%，波动较大"})
            total_score += 0.10

        # 3. 量比异常
        vol_ratio = realtime_data.get("vol_ratio", 0)
        if vol_ratio > 3:
            is_up = change_pct > 0
            direction = "放量上涨" if is_up else "放量下跌"
            risk_items.append({"type": "volume_spike", "level": "medium" if is_up else "high",
                               "score": 0.12, "msg": f"量比 {vol_ratio:.1f}，{direction}"})
            total_score += 0.12

        # 4. PE 极端值
        pe_ttm = realtime_data.get("pe_ttm", 0)
        if pe_ttm > 500 or pe_ttm < 0:
            risk_items.append({"type": "pe_extreme", "level": "low", "score": 0.05,
                               "msg": f"PE(TTM)={pe_ttm:.1f}，估值异常"})
            total_score += 0.05

        # 5. 换手率异常
        turnover = realtime_data.get("turnover_pct", 0)
        if turnover > 20:
            risk_items.append({"type": "turnover_anomaly", "level": "medium", "score": 0.08,
                               "msg": f"换手率 {turnover:.1f}%，筹码异常活跃"})
            total_score += 0.08

        # 6. 振幅异常
        amplitude = realtime_data.get("amplitude_pct", 0)
        if amplitude > 15:
            risk_items.append({"type": "amplitude_anomaly", "level": "high", "score": 0.10,
                               "msg": f"振幅 {amplitude:.1f}%，剧烈波动"})
            total_score += 0.10

        # 7. 止盈止损检查
        if stop_loss and stop_loss > 0 and price <= stop_loss:
            loss_from_stop = ((price - stop_loss) / stop_loss) * 100
            risk_items.append({"type": "stop_loss_hit", "level": "high", "score": 0.30,
                               "msg": f"触发止损! 现价¥{price:.2f} ≤ 止损价¥{stop_loss:.2f} (跌破{abs(loss_from_stop):.1f}%)"})
            total_score += 0.30
        if target_price and target_price > 0 and price >= target_price:
            gain_from_target = ((price - target_price) / target_price) * 100
            risk_items.append({"type": "target_reached", "level": "medium", "score": 0.20,
                               "msg": f"触及目标价! 现价¥{price:.2f} ≥ 目标价¥{target_price:.2f} (超出{gain_from_target:+.1f}%)"})
            total_score += 0.20

        # 8. 交易频率检查（24小时内调整次数）
        if db_session and position.get("code"):
            since = datetime.now() - timedelta(hours=24)
            trade_count = db_session.query(TradeLog).filter(
                TradeLog.stock_code == position["code"],
                TradeLog.traded_at >= since,
            ).count() if hasattr(TradeLog, 'traded_at') else 0
            if trade_count > 5:
                risk_items.append({"type": "trade_frequency", "level": "medium", "score": 0.10,
                                   "msg": f"24小时内交易 {trade_count} 次，频率过高"})
                total_score += 0.10
            elif trade_count > 3:
                risk_items.append({"type": "trade_frequency", "level": "low", "score": 0.05,
                                   "msg": f"24小时内交易 {trade_count} 次，注意频率"})
                total_score += 0.05

        if not risk_items:
            return None

        # 确定综合风险等级
        if total_score >= 0.30:
            overall_level = "high"
        elif total_score >= 0.15:
            overall_level = "medium"
        else:
            overall_level = "low"

        risk_msgs = "; ".join(item["msg"] for item in risk_items)
        suggestions = self._gen_suggestion(risk_items, overall_level)

        return {
            "stock_code": code,
            "stock_name": name,
            "type": "composite",
            "level": overall_level,
            "score": round(total_score, 2),
            "message": risk_msgs,
            "details": risk_items,
            "current_price": price,
            "cost_price": cost_price,
            "pe_ttm": pe_ttm,
            "pb": realtime_data.get("pb", 0),
            "suggestion": suggestions,
        }

    def _gen_suggestion(self, risk_items: list, level: str) -> str:
        has_stop_loss = any(r["type"] == "stop_loss_hit" for r in risk_items)
        has_target = any(r["type"] == "target_reached" for r in risk_items)
        if has_stop_loss:
            return "止损价已触发，建议立即按止损价卖出或减仓"
        if has_target:
            return "目标价已触及，建议考虑分批止盈，锁定利润"
        if level == "high":
            return "建议立即关注，考虑减仓或设置止损单"
        elif level == "medium":
            return "建议密切关注，做好应对预案"
        return "建议持续观察"

    async def broadcast_risk_alert(self, alert_data: Dict[str, Any]):
        disconnected = set()
        for ws in self.websocket_clients:
            try:
                await ws.send_text(json.dumps(alert_data, ensure_ascii=False))
            except Exception:
                disconnected.add(ws)
        self.websocket_clients -= disconnected
