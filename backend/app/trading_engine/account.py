"""模拟账户管理 — 资金、持仓、净值"""
from datetime import datetime
from typing import Optional
from sqlalchemy.orm import Session
from app.database import SessionLocal
from app.models import SimAccount, Position
from app.utils.logger import logger


class SimAccountManager:
    """模拟账户管理器（单例数据行）"""

    def __init__(self):
        self._db: Optional[Session] = None

    def _get_db(self) -> Session:
        if self._db is None:
            self._db = SessionLocal()
        return self._db

    def get_account(self) -> SimAccount:
        db = self._get_db()
        acc = db.query(SimAccount).first()
        if not acc:
            acc = SimAccount()
            db.add(acc)
            db.commit()
            db.refresh(acc)
        return acc

    def get_cash_yuan(self) -> float:
        return self.get_account().cash / 100.0

    def get_available_cash(self) -> int:
        return self.get_account().cash

    def freeze_cash(self, amount_fen: int) -> bool:
        acc = self.get_account()
        if acc.cash < amount_fen:
            return False
        acc.cash -= amount_fen
        acc.frozen += amount_fen
        acc.updated_at = datetime.now()
        self._db.commit()
        return True

    def unfreeze_cash(self, amount_fen: int):
        acc = self.get_account()
        acc.cash += amount_fen
        acc.frozen -= amount_fen
        acc.updated_at = datetime.now()
        self._db.commit()

    def deduct_cash(self, amount_fen: int) -> bool:
        acc = self.get_account()
        if acc.cash < amount_fen:
            return False
        acc.cash -= amount_fen
        acc.updated_at = datetime.now()
        self._db.commit()
        return True

    def add_cash(self, amount_fen: int):
        acc = self.get_account()
        acc.cash += amount_fen
        acc.updated_at = datetime.now()
        self._db.commit()

    def update_total_value(self, positions_value_fen: int):
        acc = self.get_account()
        new_total = acc.cash + acc.frozen + positions_value_fen
         # 只更新 total_value，PnL 通过 recalculate_pnl() 独立计算
        acc.total_value = new_total
        acc.updated_at = datetime.now()
        self._db.commit()

    def recalculate_pnl(self) -> int:
        """基于现金流水重新计算 PnL — 消除 total_pnl 累积误差

        原理: total_pnl = (初始资金 - 所有买入支出 + 所有卖出收入) - 当前市值的互补
        简化: total_pnl = cash_current + market_value_at_cost - initial_capital

        这保证了 PnL 永远等于 "已实现 + 未实现盈亏"，不会随时间漂移。
        """
        acc = self.get_account()
        db = self._get_db()

         # 当前现金 + 持仓市值(成本)
        market_value_cost_fen = 0
        positions = db.query(Position).filter(Position.quantity > 0).all()
        for p in positions:
            market_value_cost_fen += p.quantity * p.avg_cost

         # PnL = (cash + frozen + market_at_cost) - initial_capital
        pnl_fen = (acc.cash + acc.frozen + market_value_cost_fen) - acc.initial_capital

        acc.total_pnl = pnl_fen
        acc.daily_pnl = pnl_fen
        acc.total_value = acc.cash + acc.frozen + market_value_cost_fen
        acc.updated_at = datetime.now()
        self._db.commit()

        logger.info(f"PnL 重新计算: initial={acc.initial_capital/100:.2f}, "
                     f"current_total={acc.total_value/100:.2f}, "
                     f"pnl={pnl_fen/100:.2f} ({pnl_fen*100/acc.initial_capital:.2f}%)")
        return pnl_fen

    def reset_account(self):
        acc = self.get_account()
        acc.cash = acc.initial_capital
        acc.frozen = 0
        acc.total_value = acc.initial_capital
        acc.daily_pnl = 0
        acc.total_pnl = 0
        acc.updated_at = datetime.now()
        self._db.commit()
        logger.info("模拟账户已重置")

    def get_summary(self) -> dict:
        acc = self.get_account()
        return {
            "cash": round(acc.cash / 100, 2),
            "frozen": round(acc.frozen / 100, 2),
            "total_value": round(acc.total_value / 100, 2),
            "initial_capital": round(acc.initial_capital / 100, 2),
            "daily_pnl": round(acc.daily_pnl / 100, 2),
            "total_pnl": round(acc.total_pnl / 100, 2),
            "total_return_pct": round(
                (acc.total_value - acc.initial_capital) / acc.initial_capital * 100, 2
            ) if acc.initial_capital else 0,
        }
