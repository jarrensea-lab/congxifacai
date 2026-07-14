"""Strategy mode profiles for report-time risk constraints."""
from __future__ import annotations

import os
from typing import Any


STRATEGY_PROFILES: dict[str, dict[str, Any]] = {
    "capital_preservation": {
        "mode": "capital_preservation",
        "title": "保守铁律模式",
        "target": "本金安全优先",
        "max_drawdown_pct": 3,
        "cash_reserve_pct": 30,
        "single_position_limit_pct": 10,
        "standard_single_position_limit_pct": 20,
        "stop_loss_pct": 3,
        "risk_per_trade_pct": 0.5,
        "allow_high_volatility": False,
    },
    "growth_sprint": {
        "mode": "growth_sprint",
        "title": "高收益试验模式",
        "target": "30天内争取 +10%",
        "max_drawdown_pct": 10,
        "cash_reserve_pct": 10,
        "single_position_limit_pct": 50,
        "standard_single_position_limit_pct": 50,
        "stop_loss_pct": 10,
        "risk_per_trade_pct": 2,
        "target_profit_pct": 20,
        "min_reward_risk_ratio": 2,
        "allow_high_volatility": True,
    },
}


def get_strategy_profile(mode: str | None = None) -> dict[str, Any]:
    """Return the active report-time strategy profile without mutating the old iron rules."""
    selected = (mode or os.getenv("CONGXI_STRATEGY_MODE") or "growth_sprint").strip()
    return dict(STRATEGY_PROFILES.get(selected, STRATEGY_PROFILES["capital_preservation"]))


def calculate_stop_loss_price(entry_price: float, profile: dict[str, Any] | None = None) -> float:
    """Return the profile stop price for a positive entry price."""
    try:
        price = float(entry_price or 0)
        stop_pct = float((profile or get_strategy_profile()).get("stop_loss_pct", 0) or 0)
    except (TypeError, ValueError):
        return 0.0
    if price <= 0 or stop_pct <= 0 or stop_pct >= 100:
        return 0.0
    return round(price * (1 - stop_pct / 100), 2)


def calculate_target_price(entry_price: float, profile: dict[str, Any] | None = None) -> float:
    """Return a target price that respects both profit and reward-risk floors."""
    selected = profile or get_strategy_profile()
    try:
        price = float(entry_price or 0)
        stop_pct = float(selected.get("stop_loss_pct", 0) or 0)
        target_pct = float(selected.get("target_profit_pct", 12) or 12)
        min_ratio = float(selected.get("min_reward_risk_ratio", 0) or 0)
    except (TypeError, ValueError):
        return 0.0
    if price <= 0:
        return 0.0
    effective_target_pct = max(target_pct, stop_pct * min_ratio)
    return round(price * (1 + effective_target_pct / 100), 2)
