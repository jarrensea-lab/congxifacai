"""Audit whether a stored recommendation quote still matches current truth."""
from __future__ import annotations

import math
from typing import Any


def _positive_number(value: Any) -> float | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        parsed = float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed) or parsed <= 0:
        return None
    return parsed


def audit_recommendation_reference(
    recommendation: dict[str, Any] | None,
    *,
    current_price: Any,
    max_divergence_pct: float = 5.0,
) -> dict[str, Any]:
    """Compare a historical recommendation quote with the current snapshot.

    The function never mutates or rewrites the recommendation. Its result only
    decides whether historical price prose may be used for a current action.
    """
    payload = recommendation if isinstance(recommendation, dict) else {}
    raw_quote = payload.get("realtime_quote")
    quote = raw_quote if isinstance(raw_quote, dict) else {}
    reference_price = _positive_number(quote.get("price"))
    normalized_current = _positive_number(current_price)
    trading_date = str(
        quote.get("trading_date")
        or quote.get("quote_trading_date")
        or ""
    ).strip() or None

    if reference_price is None or normalized_current is None:
        return {
            "status": "unverifiable",
            "reference_price": reference_price,
            "current_price": normalized_current,
            "divergence_pct": None,
            "reference_trading_date": trading_date,
            "usable_for_current_action": False,
        }

    divergence = round(
        abs(normalized_current - reference_price) / reference_price * 100,
        2,
    )
    current = divergence <= max(0.0, float(max_divergence_pct))
    return {
        "status": "current" if current else "stale_divergence",
        "reference_price": reference_price,
        "current_price": normalized_current,
        "divergence_pct": divergence,
        "reference_trading_date": trading_date,
        "usable_for_current_action": current,
    }
