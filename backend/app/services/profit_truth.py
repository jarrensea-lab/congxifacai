"""Deterministic reconciliation for portfolio, risk plans, and loss cooldowns."""
from __future__ import annotations

from typing import Any

from app.services.quant_lifecycle import PositionWatchStore, TargetPoolStore
from app.services.strategy_profile import (
    calculate_stop_loss_price,
    calculate_target_price,
    get_strategy_profile,
)


def _number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def reconcile_position_watch(
    portfolio: dict[str, Any],
    store: PositionWatchStore,
) -> dict[str, Any]:
    """Ensure every real open position has an actionable stop and target plan."""
    profile = get_strategy_profile()
    added_codes: list[str] = []
    unresolved_codes: list[str] = []

    for position in portfolio.get("positions", []) or []:
        if not isinstance(position, dict) or _number(position.get("shares")) <= 0:
            continue
        code = str(position.get("code") or "").strip()
        if not code:
            continue
        existing = store.get(code) or {}
        if (
            _number(existing.get("stop_loss_price")) > 0
            and _number(existing.get("target_price")) > 0
        ):
            continue
        entry_price = _number(position.get("avg_cost")) or _number(position.get("current_price"))
        if entry_price <= 0:
            unresolved_codes.append(code)
            continue
        store.upsert_plan(
            code,
            str(position.get("name") or code),
            stop_loss_price=calculate_stop_loss_price(entry_price, profile),
            target_price=calculate_target_price(entry_price, profile),
            entry_price=entry_price,
            source="portfolio_reconciliation",
        )
        added_codes.append(code)

    return {
        "healthy": not unresolved_codes,
        "added_codes": sorted(added_codes),
        "unresolved_codes": sorted(unresolved_codes),
    }


def reconcile_closed_loss_cooldowns(
    portfolio: dict[str, Any],
    store: TargetPoolStore,
) -> dict[str, Any]:
    """Make confirmed losing exits durable in the production target lifecycle."""
    cooled_codes: list[str] = []
    for closed in portfolio.get("closed_positions", []) or []:
        if not isinstance(closed, dict):
            continue
        pnl = _number(closed.get("realized_pnl"))
        pnl_pct = _number(closed.get("realized_pnl_pct"))
        if pnl >= 0 and pnl_pct >= 0:
            continue
        code = str(closed.get("code") or "").strip()
        if not code:
            continue
        store.mark_cooldown_after_loss(
            code=code,
            name=str(closed.get("name") or code),
            close_date=str(closed.get("close_date") or ""),
            realized_pnl=pnl,
            realized_pnl_pct=pnl_pct,
        )
        cooled_codes.append(code)
    return {"cooled_codes": sorted(set(cooled_codes))}
