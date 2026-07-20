"""费用引擎 — 按板块/方向查表计算完整交易费用"""
import os
from enum import Enum
from typing import Any, Dict

COST_MODEL_VERSION = "a_share_round_trip_v2026_07"


class EntryPolicy(str, Enum):
    PREDICTION_CLOSE = "prediction_close"


class ExitPolicy(str, Enum):
    HORIZON_CLOSE = "horizon_close"

# 费率配置 (分, 1元=100分)
FEE_SCHEDULE = {
    "main": {
        "commission_rate": 0.00015,       # 佣金预估值 0.015%
        "min_commission": 500,             # 最低5元
        "stamp_tax_rate": 0.0005,          # 印花税 0.05% (仅卖出)
        "transfer_fee_rate": 0.00001,      # 过户费 0.001% (双向)
        "handling_fee_rate": 0.0000341,    # 经手费 0.00341% (双向)
        "regulatory_fee_rate": 0.00002,    # 证管费 ~0.002% (双向)
        "slippage_rate": 0.001,            # 滑点 0.1%
        "lot_size": 100,
    },
    "chi_next": {
        "commission_rate": 0.00015,
        "min_commission": 500,
        "stamp_tax_rate": 0.0005,
        "transfer_fee_rate": 0.00001,
        "handling_fee_rate": 0.0000341,
        "regulatory_fee_rate": 0.00002,
        "slippage_rate": 0.001,
        "lot_size": 100,
    },
    "star": {
        "commission_rate": 0.00015,
        "min_commission": 500,
        "stamp_tax_rate": 0.0005,
        "transfer_fee_rate": 0.00001,
        "handling_fee_rate": 0.0000341,
        "regulatory_fee_rate": 0.00002,
        "slippage_rate": 0.001,
        "lot_size": 200,  # 科创板200股/手
    },
    "bei_jiao": {
        "commission_rate": 0.00015,
        "min_commission": 500,
        "stamp_tax_rate": 0.0005,
        "transfer_fee_rate": 0.00001,
        "handling_fee_rate": 0.0000341,
        "regulatory_fee_rate": 0.00002,
        "slippage_rate": 0.001,
        "lot_size": 100,
    }
}

# 涨跌停比例 (按板块)
PRICE_LIMIT_PCT = {
    "main": 0.10,     # 主板 ±10%
    "chi_next": 0.20, # 创业板 ±20%
    "star": 0.20,     # 科创板 ±20%
    "bei_jiao": 0.30, # 北交所 ±30%
}


def get_board_type(code: str) -> str:
    """根据代码判断板块"""
    if code.startswith("688") or code.startswith("689"):
        return "star"
    elif code.startswith("300") or code.startswith("301"):
        return "chi_next"
    elif code.startswith("8") or code.startswith("4"):
        return "bei_jiao"
    return "main"


def get_fee_config(code: str, *, commission_rate: float | None = None) -> dict:
    """获取某股票的费率配置"""
    board = get_board_type(code)
    config = dict(FEE_SCHEDULE.get(board, FEE_SCHEDULE["main"]))
    environment_rate = os.environ.get("CONGXI_COMMISSION_RATE")
    if commission_rate is not None:
        config["commission_rate"] = float(commission_rate)
        commission_source = "explicit"
    elif environment_rate not in (None, ""):
        config["commission_rate"] = float(environment_rate)
        commission_source = "environment"
    else:
        commission_source = "estimated_default"
    config.update(
        {
            "cost_model_version": COST_MODEL_VERSION,
            "commission_source": commission_source,
            "commission_estimated": commission_source == "estimated_default",
        }
    )
    return config


def get_price_limit_pct(code: str) -> float:
    """获取某股票的涨跌停比例"""
    board = get_board_type(code)
    return PRICE_LIMIT_PCT.get(board, 0.10)


def calc_fees(
    code: str,
    direction: str,
    amount_fen: int,
    *,
    commission_rate: float | None = None,
) -> Dict:
    """计算完整交易费用 (返回分)"""
    cfg = get_fee_config(code, commission_rate=commission_rate)
    if amount_fen <= 0:
        return {
            "commission": 0,
            "stamp_tax": 0,
            "transfer": 0,
            "handling": 0,
            "regulatory": 0,
            "total": 0,
        }
    commission = max(cfg["min_commission"], int(amount_fen * cfg["commission_rate"]))
    stamp_tax = int(amount_fen * cfg["stamp_tax_rate"]) if direction == "sell" else 0
    transfer = int(amount_fen * cfg["transfer_fee_rate"])
    handling = int(amount_fen * cfg["handling_fee_rate"])
    regulatory = int(amount_fen * cfg["regulatory_fee_rate"])
    total = commission + stamp_tax + transfer + handling + regulatory
    return {
        "commission": commission,
        "stamp_tax": stamp_tax,
        "transfer": transfer,
        "handling": handling,
        "regulatory": regulatory,
        "total": total,
    }


def apply_slippage(price_fen: int, direction: str, code: str) -> int:
    """应用滑点: 买+滑点, 卖-滑点"""
    cfg = get_fee_config(code)
    slip = max(1, int(price_fen * cfg["slippage_rate"]))
    return price_fen + slip if direction == "buy" else price_fen - slip


def get_lot_size(code: str) -> int:
    """获取最小交易单位"""
    cfg = get_fee_config(code)
    return cfg["lot_size"]


def round_lot(quantity: int, code: str) -> int:
    """向下取整到板别最小交易单位，不足 1 手向上取整"""
    lot = get_lot_size(code)
    if quantity <= 0:
        return 0
    if quantity < lot:
        return lot
    return (quantity // lot) * lot


def calculate_tradable_return(
    code: str,
    *,
    entry_price: float,
    exit_price: float,
    budget_fen: int | None,
    entry_policy: Any = None,
    exit_policy: Any = None,
    commission_rate: float | None = None,
) -> dict[str, Any]:
    """Calculate a fail-closed, lot-aware round-trip return in fen."""
    config = get_fee_config(code, commission_rate=commission_rate)
    gross_return_pct = (
        round((exit_price - entry_price) / entry_price * 100, 3)
        if entry_price > 0
        else None
    )
    base = {
        "gross_return_pct": gross_return_pct,
        "round_trip_cost_fen": None,
        "round_trip_cost_pct": None,
        "net_tradable_return_pct": None,
        "cost_model_version": COST_MODEL_VERSION,
        "cost_estimated": config["commission_estimated"],
        "commission_source": config["commission_source"],
        "tradable": False,
        "untradable_reason": None,
        "quantity": 0,
    }
    if not entry_policy:
        return {**base, "untradable_reason": "entry_policy_missing"}
    if not exit_policy:
        return {**base, "untradable_reason": "exit_policy_missing"}
    normalized_entry_policy = (
        entry_policy.value if isinstance(entry_policy, EntryPolicy) else str(entry_policy)
    )
    normalized_exit_policy = (
        exit_policy.value if isinstance(exit_policy, ExitPolicy) else str(exit_policy)
    )
    if normalized_entry_policy != EntryPolicy.PREDICTION_CLOSE.value:
        return {**base, "untradable_reason": "unsupported_entry_policy"}
    if normalized_exit_policy != ExitPolicy.HORIZON_CLOSE.value:
        return {**base, "untradable_reason": "unsupported_exit_policy"}
    if entry_price <= 0 or exit_price <= 0:
        return {**base, "untradable_reason": "invalid_price"}
    if budget_fen is None or budget_fen <= 0:
        return {**base, "untradable_reason": "budget_missing"}

    raw_entry_fen = round(entry_price * 100)
    raw_exit_fen = round(exit_price * 100)
    entry_execution_fen = apply_slippage(raw_entry_fen, "buy", code)
    exit_execution_fen = apply_slippage(raw_exit_fen, "sell", code)
    lot_size = get_lot_size(code)
    quantity = (budget_fen // entry_execution_fen // lot_size) * lot_size
    while quantity >= lot_size:
        buy_amount_fen = entry_execution_fen * quantity
        buy_fees = calc_fees(
            code,
            "buy",
            buy_amount_fen,
            commission_rate=commission_rate,
        )
        if buy_amount_fen + buy_fees["total"] <= budget_fen:
            break
        quantity -= lot_size
    if quantity < lot_size:
        return {**base, "untradable_reason": "insufficient_budget_for_min_lot"}

    sell_amount_fen = exit_execution_fen * quantity
    sell_fees = calc_fees(
        code,
        "sell",
        sell_amount_fen,
        commission_rate=commission_rate,
    )
    raw_entry_amount_fen = raw_entry_fen * quantity
    gross_pnl_fen = (raw_exit_fen - raw_entry_fen) * quantity
    slippage_cost_fen = (
        (entry_execution_fen - raw_entry_fen) * quantity
        + (raw_exit_fen - exit_execution_fen) * quantity
    )
    round_trip_cost_fen = buy_fees["total"] + sell_fees["total"] + slippage_cost_fen
    net_pnl_fen = gross_pnl_fen - round_trip_cost_fen
    return {
        **base,
        "round_trip_cost_fen": round_trip_cost_fen,
        "round_trip_cost_pct": round(round_trip_cost_fen / raw_entry_amount_fen * 100, 3),
        "net_tradable_return_pct": round(net_pnl_fen / raw_entry_amount_fen * 100, 3),
        "tradable": True,
        "quantity": quantity,
        "buy_fees": buy_fees,
        "sell_fees": sell_fees,
        "slippage_cost_fen": slippage_cost_fen,
        "entry_execution_price_fen": entry_execution_fen,
        "exit_execution_price_fen": exit_execution_fen,
    }
