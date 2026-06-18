"""策略基类 — 定义统一策略接口"""
from abc import ABC, abstractmethod
import pandas as pd


class BaseStrategy(ABC):
    """所有量化策略的基类"""

    name: str = "base"
    params: dict = {}

    def __init__(self, **kwargs):
        self.params = {**self.params, **kwargs}

    @abstractmethod
    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """计算技术指标，返回含新列的DataFrame"""
        ...

    @abstractmethod
    def generate_buy_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        """生成买入信号"""
        ...

    @abstractmethod
    def generate_sell_signals(self, df: pd.DataFrame, holding_cost: float = 0) -> pd.DataFrame:
        """生成卖出信号"""
        ...

    def calculate_position_size(self, cash_fen: int, price_fen: int,
                                 atr_fen: int, lot_size: int = 100) -> int:
        if atr_fen <= 0 or price_fen <= 0:
            return 0
        risk_amount = int(cash_fen * self.params.get("risk_per_trade", 0.02))
        stop_distance = int(atr_fen * self.params.get("atr_stop_multiplier", 1.5))
        if stop_distance <= 0:
            return 0
        qty = risk_amount // stop_distance
        qty = (qty // lot_size) * lot_size
        max_qty_per_position = int(
            cash_fen * self.params.get("max_single_position_pct", 0.20) / price_fen
        )
        return min(qty, max_qty_per_position)

    def check_stop_loss(self, cost_price_fen: int, current_price_fen: int,
                         atr_fen: int) -> bool:
        multiplier = self.params.get("atr_stop_multiplier", 1.5)
        stop_price = cost_price_fen - int(atr_fen * multiplier)
        return current_price_fen <= stop_price

    def check_take_profit(self, cost_price_fen: int, current_price_fen: int,
                           highest_price_fen: int = None) -> tuple[bool, bool]:
        take_profit_pct = self.params.get("take_profit_pct", 0.15)
        trailing_pct = self.params.get("trailing_stop_pct", 0.03)
        gain_pct = (current_price_fen - cost_price_fen) / cost_price_fen
        fixed_tp = gain_pct >= take_profit_pct
        trailing_tp = False
        if highest_price_fen and gain_pct >= take_profit_pct:
            drawdown = (highest_price_fen - current_price_fen) / highest_price_fen
            trailing_tp = drawdown >= trailing_pct
        return fixed_tp, trailing_tp

    def set_params(self, **kwargs):
        self.params.update(kwargs)

    def get_params(self) -> dict:
        return dict(self.params)
