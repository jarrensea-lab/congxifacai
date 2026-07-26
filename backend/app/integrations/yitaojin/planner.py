"""Pure watchlist planning and candidate-pool freshness rules."""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, time
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

from app.integrations.yitaojin.models import (
    BrokerPosition,
    WatchlistPlan,
    normalize_stock_code,
)
from app.integrations.yitaojin.state import WatchlistState
from app.utils.trading_calendar import is_trading_day, prev_trading_day


PROJECT_TIMEZONE = ZoneInfo("Asia/Shanghai")
PRODUCTION_STATUSES = frozenset({"executable", "watching"})
DAILY_REPORT_READY_AT = time(20, 30)


@dataclass(frozen=True)
class PoolFreshness:
    valid: bool
    reason: str
    updated_at: datetime
    required_trading_day: str


@dataclass(frozen=True)
class WatchlistPlanningResult:
    plan: WatchlistPlan
    next_state: WatchlistState


def _china_time(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=PROJECT_TIMEZONE)
    return value.astimezone(PROJECT_TIMEZONE)


def validate_candidate_pool_freshness(
    updated_at: datetime,
    *,
    now: datetime,
) -> PoolFreshness:
    """Require the latest completed daily-report trading session."""
    observed = _china_time(updated_at)
    current = _china_time(now)
    if observed > current:
        return PoolFreshness(
            valid=False,
            reason="updated_at_in_future",
            updated_at=observed,
            required_trading_day=current.date().isoformat(),
        )
    current_day = current.date()
    if is_trading_day(current_day) and current.time() >= time(15, 30):
        required_day = current_day
    else:
        required_day = prev_trading_day(current_day)
    if observed.date() < required_day:
        reason = "missed_completed_trading_day"
        valid = False
    elif observed.date() > required_day:
        reason = "updated_at_in_future_session"
        valid = False
    elif observed.time() < DAILY_REPORT_READY_AT:
        reason = "before_daily_report"
        valid = False
    else:
        reason = "fresh"
        valid = True
    return PoolFreshness(
        valid=valid,
        reason=reason,
        updated_at=observed,
        required_trading_day=required_day.isoformat(),
    )


def _pool_items(pool_payload: Mapping[str, object]) -> list[tuple[str, Mapping[str, Any]]]:
    raw_items = pool_payload.get("items")
    if isinstance(raw_items, Mapping):
        return [
            (str(key), item)
            for key, item in raw_items.items()
            if isinstance(item, Mapping)
        ]
    if isinstance(raw_items, list):
        return [
            (str(item.get("code") or ""), item)
            for item in raw_items
            if isinstance(item, Mapping)
        ]
    return []


def build_desired_codes(
    pool_payload: Mapping[str, object],
    positions: Sequence[BrokerPosition],
) -> set[str]:
    desired = {position.code for position in positions}
    for fallback_code, item in _pool_items(pool_payload):
        status = str(item.get("status") or "").strip().lower()
        if status not in PRODUCTION_STATUSES:
            continue
        desired.add(normalize_stock_code(item.get("code") or fallback_code))
    return desired


def _normalized_codes(values: set[str]) -> set[str]:
    return {normalize_stock_code(code) for code in values}


def plan_watchlist_sync(
    *,
    desired_codes: set[str],
    current_codes: set[str],
    held_codes: set[str],
    state: WatchlistState,
    allow_removals: bool,
    source_valid: bool,
) -> WatchlistPlanningResult:
    desired = _normalized_codes(desired_codes)
    current = _normalized_codes(current_codes)
    held = _normalized_codes(held_codes)
    managed = set(state.managed_codes)
    protected = set(state.manual_protected_codes)

    if not source_valid:
        blocked = tuple(sorted(desired.symmetric_difference(current)))
        plan = WatchlistPlan(
            add=(),
            remove=(),
            keep=tuple(sorted(current)),
            blocked=blocked,
            reasons=("source_invalid",),
            desired_codes=tuple(sorted(desired)),
            current_codes=tuple(sorted(current)),
            managed_codes=tuple(sorted(managed)),
            manual_protected_codes=tuple(sorted(protected)),
        )
        return WatchlistPlanningResult(plan=plan, next_state=state)

    observed_manual = current - managed
    protected.update(observed_manual)
    system_desired = desired | held
    add = system_desired - current
    removal_counts: dict[str, int] = {}
    remove: set[str] = set()

    if allow_removals:
        candidates = (managed & current) - system_desired - protected
        for code in candidates:
            count = state.pending_removal_counts.get(code, 0) + 1
            removal_counts[code] = count
            if count >= 2:
                remove.add(code)

    keep = current - remove
    effective_desired = system_desired | (protected & current)
    next_state = replace(
        state,
        manual_protected_codes=tuple(sorted(protected)),
        pending_removal_counts=removal_counts,
    )
    plan = WatchlistPlan(
        add=tuple(sorted(add)),
        remove=tuple(sorted(remove)),
        keep=tuple(sorted(keep)),
        blocked=(),
        reasons=(),
        desired_codes=tuple(sorted(effective_desired)),
        current_codes=tuple(sorted(current)),
        managed_codes=tuple(sorted(managed)),
        manual_protected_codes=tuple(sorted(protected)),
    )
    return WatchlistPlanningResult(plan=plan, next_state=next_state)
