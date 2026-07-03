"""Market regime guardrails for trade playbooks."""
from __future__ import annotations

from typing import Any


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(str(value).replace("%", "").replace(",", ""))
    except (TypeError, ValueError):
        return default


def _ratio(value: Any, default: float) -> float:
    ratio = _to_float(value, default)
    return ratio / 100 if ratio > 1 else ratio


def evaluate_market_regime(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Return conservative playbook permissions from market/sector context."""
    regime = snapshot.get("market_regime") or {}
    label = str(regime.get("label") or regime.get("status") or "neutral").lower()
    index_change_pct = _to_float(regime.get("index_change_pct") or regime.get("market_change_pct"))
    breadth = _ratio(regime.get("breadth") or regime.get("advance_ratio"), 1)
    sector_rank = _to_float(regime.get("sector_relative_rank"), 50)

    bearish_label = label in {"bear", "bearish", "ice_point", "panic", "downtrend"}
    broad_selloff = index_change_pct <= -1.5 and breadth < 0.35
    sector_weak = sector_rank > 70
    can_dip = not (bearish_label or broad_selloff or sector_weak)

    reason = "市场状态中性，允许按剧本评估。"
    if bearish_label:
        reason = "市场处于熊市/冰点/恐慌标签，禁止低吸接飞刀。"
    elif broad_selloff:
        reason = "指数大跌且上涨家数占比过低，禁止低吸接飞刀。"
    elif sector_weak:
        reason = "板块相对排名过弱，禁止低吸接飞刀。"

    return {
        "label": label,
        "can_buy": True,
        "can_dip": can_dip,
        "reason": reason,
    }
