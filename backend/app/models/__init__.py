"""数据模型定义 — 统一导出所有模型，保持向后兼容。

使用方式: from app.models import RiskAlert, Position, AIStrategy
"""
from app.models.trading import (
    SimAccount,
    TradingSignal,
    TradingOrder,
    TradeLog,
    Position,
)
from app.models.strategy import (
    AIStrategy,
    StrategyInstance,
    ReviewLog,
    DebateResult,
)
from app.models.system import (
    RiskAlert,
    UserPreference,
    PushRecord,
)

__all__ = [
    "SimAccount", "TradingSignal", "TradingOrder", "TradeLog", "Position",
    "AIStrategy", "StrategyInstance", "ReviewLog", "DebateResult",
    "RiskAlert", "UserPreference", "PushRecord",
]
