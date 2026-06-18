"""绩效分析器 — 收益率/夏普/最大回撤/胜率/盈亏比"""
import math
from datetime import date, timedelta
from typing import List
import numpy as np
from sqlalchemy.orm import Session
from app.database import SessionLocal
from app.models import TradeLog, SimAccount


class PerformanceAnalyzer:
    def __init__(self):
        self._db: Session = None

    def _get_db(self) -> Session:
        if self._db is None:
            self._db = SessionLocal()
        return self._db

    def get_all_trades(self, days: int = 365) -> List[TradeLog]:
        since = date.today() - timedelta(days=days)
        return self._get_db().query(TradeLog).filter(
            TradeLog.traded_at >= since
        ).order_by(TradeLog.traded_at.asc()).all()

    def calc_win_rate(self, trades: List[TradeLog]) -> float:
        pnl_trades = [t for t in trades if t.pnl is not None and t.pnl != 0]
        if not pnl_trades:
            return 0.0
        wins = sum(1 for t in pnl_trades if t.pnl > 0)
        return round(wins / len(pnl_trades), 4)

    def calc_profit_factor(self, trades: List[TradeLog]) -> float:
        wins = [t.pnl for t in trades if t.pnl and t.pnl > 0]
        losses = [abs(t.pnl) for t in trades if t.pnl and t.pnl < 0]
        if not losses or not wins:
            return 0.0
        return round(np.mean(wins) / np.mean(losses), 2)

    def calc_max_drawdown(self, trades: List[TradeLog]) -> dict:
        if not trades:
            return {"max_drawdown_pct": 0, "recovery_days": 0}
        cumulative = 0; peak = 0; max_dd = 0; dd_start = None; recovery_days = 0
        for t in trades:
            cumulative += (t.pnl or 0)
            if cumulative > peak:
                peak = cumulative
                if dd_start:
                    recovery_days = max(recovery_days, (t.traded_at.date() - dd_start).days if t.traded_at else 0)
                    dd_start = None
            dd = (peak - cumulative) / peak if peak > 0 else 0
            if dd > max_dd:
                max_dd = dd
                if dd_start is None and t.traded_at:
                    dd_start = t.traded_at.date()
        return {"max_drawdown_pct": round(max_dd * 100, 2), "recovery_days": recovery_days}

    def calc_sharpe_ratio(self, trades: List[TradeLog], risk_free_rate: float = 0.025) -> float:
        if not trades:
            return 0.0
        daily_returns = [t.pnl_pct or 0 for t in trades if t.pnl_pct is not None]
        if len(daily_returns) < 2:
            return 0.0
        avg_daily = np.mean(daily_returns)
        std_daily = np.std(daily_returns, ddof=1)
        if std_daily == 0:
            return 0.0
        sharpe = (avg_daily * 252 - risk_free_rate) / (std_daily * math.sqrt(252))
        return round(sharpe, 2)

    def calc_annual_return(self) -> float:
        acc = self._get_db().query(SimAccount).first()
        if not acc:
            return 0.0
        total_return = (acc.total_value - acc.initial_capital) / acc.initial_capital
        days = (date.today() - (acc.created_at.date() if acc.created_at else date.today())).days or 1
        annual = (1 + total_return) ** (252 / days) - 1
        return round(annual * 100, 2)

    def get_summary(self) -> dict:
        trades = self.get_all_trades()
        completed = [t for t in trades if t.pnl is not None]
        total_trades = len(completed)
        acc = self._get_db().query(SimAccount).first()
        total_return_pct = round(
            (acc.total_value - acc.initial_capital) / acc.initial_capital * 100, 2
        ) if acc else 0
        avg_days = 0
        if completed:
            days_list = [t.holding_days for t in completed if t.holding_days]
            avg_days = round(np.mean(days_list), 1) if days_list else 0
        return {
            "total_return_pct": total_return_pct,
            "annual_return_pct": self.calc_annual_return(),
            "max_drawdown": self.calc_max_drawdown(completed),
            "sharpe_ratio": self.calc_sharpe_ratio(completed),
            "win_rate": self.calc_win_rate(completed),
            "profit_factor": self.calc_profit_factor(completed),
            "total_trades": total_trades,
            "avg_holding_days": avg_days,
        }

    def get_equity_curve(self, days: int = 90) -> list:
        since = date.today() - timedelta(days=days)
        trades = self._get_db().query(TradeLog).filter(
            TradeLog.traded_at >= since
        ).order_by(TradeLog.traded_at.asc()).all()
        acc = self._get_db().query(SimAccount).first()
        base = acc.initial_capital if acc else 10000000
        points = []
        cumulative = base
        for t in trades:
            cumulative += (t.pnl or 0)
            points.append({
                "date": t.traded_at.strftime("%Y-%m-%d") if t.traded_at else "",
                "value": round(cumulative / 100, 2),
                "pnl": round((t.pnl or 0) / 100, 2),
            })
        return points
