from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest


def _position(code: str):
    from app.integrations.yitaojin.models import BrokerPosition

    return BrokerPosition(
        code=code,
        name=f"持仓{code}",
        shares=100,
        available_shares=100,
        average_cost=Decimal("10"),
        current_price=Decimal("10"),
        market_value=Decimal("1000"),
        unrealized_pnl=Decimal("0"),
    )


def _state(**overrides):
    from app.integrations.yitaojin.state import WatchlistState

    values = {
        "account_fingerprint": "sha256:" + "a" * 64,
        "manual_protected_codes": (),
        "managed_codes": (),
        "pending_removal_counts": {},
        "successful_apply_count": 0,
    }
    values.update(overrides)
    return WatchlistState(**values)


@pytest.mark.parametrize(
    ("status", "included"),
    [
        ("executable", True),
        ("watching", True),
        ("research_reference", False),
        ("removed", False),
        ("expired", False),
        ("cooldown_after_loss", False),
    ],
)
def test_build_desired_codes_uses_production_statuses_only(status, included):
    """Catches research or removed names leaking into the broker watchlist."""
    from app.integrations.yitaojin.planner import build_desired_codes

    desired = build_desired_codes(
        {
            "items": {
                "000001": {
                    "code": "000001",
                    "status": status,
                }
            }
        },
        [],
    )

    assert ("000001" in desired) is included


def test_build_desired_codes_always_includes_real_holdings():
    """Catches a held stock disappearing after it leaves the target pool."""
    from app.integrations.yitaojin.planner import build_desired_codes

    desired = build_desired_codes(
        {
            "items": {
                "000001": {"code": "000001", "status": "removed"},
            }
        },
        [_position("600000")],
    )

    assert desired == {"600000"}


def test_plan_protects_unmanaged_current_codes_as_manual():
    """Catches an existing user watchlist item being treated as system-owned."""
    from app.integrations.yitaojin.planner import plan_watchlist_sync

    outcome = plan_watchlist_sync(
        desired_codes={"600000"},
        current_codes={"000001"},
        held_codes=set(),
        state=_state(),
        allow_removals=False,
        source_valid=True,
    )

    assert outcome.plan.add == ("600000",)
    assert outcome.plan.remove == ()
    assert outcome.plan.keep == ("000001",)
    assert outcome.plan.manual_protected_codes == ("000001",)
    assert outcome.next_state.manual_protected_codes == ("000001",)


def test_plan_never_removes_a_managed_holding():
    """Catches target-pool exit overriding the higher-priority holdings truth."""
    from app.integrations.yitaojin.planner import plan_watchlist_sync

    outcome = plan_watchlist_sync(
        desired_codes={"600000"},
        current_codes={"600000"},
        held_codes={"600000"},
        state=_state(
            managed_codes=("600000",),
            successful_apply_count=3,
        ),
        allow_removals=True,
        source_valid=True,
    )

    assert outcome.plan.remove == ()
    assert outcome.plan.keep == ("600000",)
    assert outcome.next_state.pending_removal_counts == {}


def test_plan_requires_two_post_warmup_confirmations_before_removal():
    """Catches a transient pool omission deleting a system-managed code."""
    from app.integrations.yitaojin.planner import plan_watchlist_sync

    first = plan_watchlist_sync(
        desired_codes=set(),
        current_codes={"600000"},
        held_codes=set(),
        state=_state(
            managed_codes=("600000",),
            successful_apply_count=3,
        ),
        allow_removals=True,
        source_valid=True,
    )
    second = plan_watchlist_sync(
        desired_codes=set(),
        current_codes={"600000"},
        held_codes=set(),
        state=first.next_state,
        allow_removals=True,
        source_valid=True,
    )

    assert first.plan.remove == ()
    assert first.next_state.pending_removal_counts == {"600000": 1}
    assert second.plan.remove == ("600000",)
    assert second.next_state.pending_removal_counts == {"600000": 2}


def test_plan_warmup_does_not_accumulate_removal_confirmations():
    """Catches the fourth apply deleting a code without two post-warmup checks."""
    from app.integrations.yitaojin.planner import plan_watchlist_sync

    outcome = plan_watchlist_sync(
        desired_codes=set(),
        current_codes={"600000"},
        held_codes=set(),
        state=_state(
            managed_codes=("600000",),
            pending_removal_counts={"600000": 9},
            successful_apply_count=2,
        ),
        allow_removals=False,
        source_valid=True,
    )

    assert outcome.plan.remove == ()
    assert outcome.next_state.pending_removal_counts == {}


def test_plan_reentry_clears_pending_removal_confirmation():
    """Catches an old removal count surviving after a candidate re-enters."""
    from app.integrations.yitaojin.planner import plan_watchlist_sync

    outcome = plan_watchlist_sync(
        desired_codes={"600000"},
        current_codes={"600000"},
        held_codes=set(),
        state=_state(
            managed_codes=("600000",),
            pending_removal_counts={"600000": 1},
            successful_apply_count=3,
        ),
        allow_removals=True,
        source_valid=True,
    )

    assert outcome.plan.remove == ()
    assert outcome.next_state.pending_removal_counts == {}


def test_plan_blocks_all_changes_when_source_is_invalid():
    """Catches stale account or pool input causing additions or deletions."""
    from app.integrations.yitaojin.planner import plan_watchlist_sync

    state = _state(managed_codes=("600000",), successful_apply_count=3)
    outcome = plan_watchlist_sync(
        desired_codes={"000001"},
        current_codes={"600000"},
        held_codes=set(),
        state=state,
        allow_removals=True,
        source_valid=False,
    )

    assert outcome.plan.add == ()
    assert outcome.plan.remove == ()
    assert outcome.plan.blocked == ("000001", "600000")
    assert outcome.plan.reasons == ("source_invalid",)
    assert outcome.next_state == state


@pytest.mark.parametrize(
    ("now_text", "updated_text", "valid"),
    [
        ("2026-07-27T08:55:00+08:00", "2026-07-24T20:45:00+08:00", True),
        ("2026-07-27T08:55:00+08:00", "2026-07-23T20:45:00+08:00", False),
        ("2026-07-27T20:45:00+08:00", "2026-07-27T20:30:00+08:00", True),
        ("2026-07-27T20:45:00+08:00", "2026-07-24T20:30:00+08:00", False),
        ("2026-07-26T12:00:00+08:00", "2026-07-24T20:30:00+08:00", True),
    ],
)
def test_candidate_pool_freshness_uses_completed_trading_days(
    now_text,
    updated_text,
    valid,
):
    """Catches weekend age checks rejecting Friday or accepting missed sessions."""
    from app.integrations.yitaojin.planner import validate_candidate_pool_freshness

    freshness = validate_candidate_pool_freshness(
        datetime.fromisoformat(updated_text),
        now=datetime.fromisoformat(now_text),
    )

    assert freshness.valid is valid


def test_candidate_pool_freshness_rejects_future_timestamp():
    """Catches clock-conflicted pool data being treated as current."""
    from app.integrations.yitaojin.planner import validate_candidate_pool_freshness

    freshness = validate_candidate_pool_freshness(
        datetime(2026, 7, 27, 21, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
        now=datetime(2026, 7, 27, 20, 45, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert freshness.valid is False
    assert freshness.reason == "updated_at_in_future"
