"""信号引擎 — 生成/审批/过期管理"""
import json
from datetime import datetime, date
from typing import Optional, List
from sqlalchemy.orm import Session
from app.database import SessionLocal
from app.models import TradingSignal
from app.utils.logger import logger


class SignalEngine:
    def __init__(self):
        self._db: Optional[Session] = None

    def _get_db(self) -> Session:
        if self._db is None:
            self._db = SessionLocal()
        return self._db

    def create_signal(self, stock_code: str, stock_name: str, signal_type: str,
                       price: float, reason: str, strategy_params: dict,
                       suggested_qty: int = 0, confidence: float = 0.5,
                       auto_approve: bool = False) -> TradingSignal:
        """创建信号, price元→存储为分"""
        db = self._get_db()
        price_fen = int(price * 100) if price < 10000 else int(price)
        signal = TradingSignal(
            stock_code=stock_code, stock_name=stock_name,
            strategy_name="trend_tracker", signal_type=signal_type,
            price=price_fen, confidence=confidence, reason=reason,
            params_json=json.dumps(strategy_params, ensure_ascii=False),
            suggested_qty=suggested_qty,
            status="approved" if auto_approve else "pending",
            approved_by="auto" if auto_approve else None,
            approved_at=datetime.now() if auto_approve else None,
        )
        db.add(signal)
        db.commit()
        db.refresh(signal)
        return signal

    def get_pending_signals(self) -> List[TradingSignal]:
        return self._get_db().query(TradingSignal).filter(
            TradingSignal.status == "pending"
        ).order_by(TradingSignal.created_at.desc()).all()

    def approve_signal(self, signal_id: int) -> Optional[TradingSignal]:
        db = self._get_db()
        s = db.query(TradingSignal).filter(TradingSignal.id == signal_id).first()
        if not s or s.status != "pending":
            return None
        s.status = "approved"
        s.approved_by = "manual"
        s.approved_at = datetime.now()
        db.commit()
        db.refresh(s)
        logger.info(f"信号已批准: {s.stock_code} {s.signal_type} #{signal_id}")
        return s

    def reject_signal(self, signal_id: int, reason: str = "") -> Optional[TradingSignal]:
        db = self._get_db()
        s = db.query(TradingSignal).filter(TradingSignal.id == signal_id).first()
        if not s or s.status != "pending":
            return None
        s.status = "rejected"
        s.reason = (s.reason or "") + f" [拒绝原因: {reason}]"
        db.commit()
        db.refresh(s)
        return s

    def mark_executed(self, signal_id: int):
        db = self._get_db()
        s = db.query(TradingSignal).filter(TradingSignal.id == signal_id).first()
        if s:
            s.status = "executed"
            db.commit()

    def expire_stale_signals(self):
        db = self._get_db()
        today = date.today()
        pending = db.query(TradingSignal).filter(TradingSignal.status == "pending").all()
        expired_count = 0
        for s in pending:
            created_date = s.created_at.date() if s.created_at else today
            if s.signal_type == "buy" and created_date < today:
                s.status = "expired"; expired_count += 1
            elif s.signal_type == "sell" and (today - created_date).days > 3:
                s.status = "expired"; expired_count += 1
        if expired_count > 0:
            db.commit()
            logger.info(f"过期信号: {expired_count} 个")

    def get_active_holdings_codes(self) -> List[str]:
        from app.trading_engine.position import PositionManager
        return PositionManager.get_holdings_codes(self._get_db())
