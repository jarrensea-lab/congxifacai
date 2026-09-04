"""Risk-budget position sizing for account-executable target decisions."""
from __future__ import annotations

from math import floor
from typing import Any

from app.services.quant_lifecycle import lot_size_for_code


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(str(value).replace("%", "").replace(",", ""))
    except (TypeError, ValueError):
        return default


def calculate_position_size(
    *,
    code: str,
    entry_price: float,
    stop_loss: float,
    available_cash: float,
    total_assets: float,
    profile: dict[str, Any],
) -> dict[str, Any]:
    """Calculate buy size from risk budget, cash reserve, and board lot size."""
    lot_size = lot_size_for_code(code)
    entry = _to_float(entry_price)
    stop = _to_float(stop_loss)
    cash = _to_float(available_cash)
    assets = _to_float(total_assets)
    if assets <= 0:
        assets = cash
    reserve_cash = assets * (_to_float(profile.get("cash_reserve_pct"), 10) / 100) if assets else 0
    single_limit = assets * (_to_float(profile.get("single_position_limit_pct"), 50) / 100) if assets else cash
    executable_budget = round(max(0.0, min(cash - reserve_cash, single_limit)), 2)
    risk_budget = round(max(0.0, assets * (_to_float(profile.get("risk_per_trade_pct"), 1) / 100)), 2)

    base = {
        "lot_size": lot_size,
        "executable_budget": executable_budget,
        "risk_budget": risk_budget,
        "position_amount": 0.0,
        "shares": 0,
        "risk_amount": 0.0,
        "risk_per_lot": 0.0,
        "risk_budget_utilization_pct": 0.0,
        "lot_concentration_pct": 0.0,
        "block_reason": "",
    }

    if entry <= 0:
        return {**base, "block_reason": "price_missing"}
    if stop <= 0 or stop >= entry:
        return {**base, "block_reason": "invalid_stop_loss"}

    lot_value = entry * lot_size
    risk_per_share = entry - stop
    risk_per_lot = risk_per_share * lot_size
    base.update({
        "risk_per_lot": round(risk_per_lot, 2),
        "risk_budget_utilization_pct": round(
            risk_per_lot / risk_budget * 100,
            2,
        ) if risk_budget > 0 else 0.0,
        "lot_concentration_pct": round(
            lot_value / assets * 100,
            2,
        ) if assets > 0 else 0.0,
    })
    if risk_budget < risk_per_lot:
        return {**base, "block_reason": "risk_budget_too_small"}
    if lot_value > cash or lot_value > executable_budget:
        return {**base, "block_reason": "lot_size_exceeded"}

    shares_by_cash = floor(executable_budget / entry / lot_size) * lot_size
    shares_by_risk = floor(risk_budget / risk_per_share / lot_size) * lot_size
    shares = max(0, min(shares_by_cash, shares_by_risk))
    if shares < lot_size:
        return {**base, "block_reason": "risk_budget_too_small"}

    position_amount = round(shares * entry, 2)
    risk_amount = round(shares * risk_per_share, 2)
    return {
        **base,
        "position_amount": position_amount,
        "shares": shares,
        "risk_amount": risk_amount,
    }
