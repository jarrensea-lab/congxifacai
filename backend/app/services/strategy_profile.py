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
        "stop_loss_pct": 5,
        "allow_high_volatility": True,
    },
}


def get_strategy_profile(mode: str | None = None) -> dict[str, Any]:
    """Return the active report-time strategy profile without mutating the old iron rules."""
    selected = (mode or os.getenv("CONGXI_STRATEGY_MODE") or "growth_sprint").strip()
    return dict(STRATEGY_PROFILES.get(selected, STRATEGY_PROFILES["capital_preservation"]))
