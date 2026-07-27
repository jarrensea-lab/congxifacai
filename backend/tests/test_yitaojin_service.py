from __future__ import annotations

import json
from copy import deepcopy
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal


NOW = datetime.fromisoformat("2026-07-27T20:45:00+08:00")
FINGERPRINT = "sha256:" + "a" * 64


class FakeBridge:
    def __init__(self, codes=()):
        self.codes = set(codes)
        self.calls = []
        self.fail_add = set()
        self.fail_remove = set()
        self.error = None

    def run(self, command, payload=None, *, timeout=15.0):
        from app.integrations.yitaojin.models import BridgeCommand

        self.calls.append((command, deepcopy(payload), timeout))
        if self.error is not None:
            raise self.error
        if command == BridgeCommand.READ_WATCHLIST:
            return {"codes": sorted(self.codes)}
        code = str((payload or {}).get("code") or "")
        if command == BridgeCommand.ADD_WATCHLIST:
            if code in self.fail_add:
                return {"code": code, "state": "failed", "confirmed": False}
            state = "already_present" if code in self.codes else "added"
            self.codes.add(code)
            return {"code": code, "state": state, "confirmed": True}
        if command == BridgeCommand.REMOVE_WATCHLIST:
            if code in self.fail_remove:
                return {"code": code, "state": "failed", "confirmed": False}
            state = "removed" if code in self.codes else "already_absent"
            self.codes.discard(code)
            return {"code": code, "state": state, "confirmed": True}
        raise AssertionError(f"unexpected command: {command}")


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


def _write_account(path, *, fingerprint=FINGERPRINT, positions=()):
    from app.integrations.yitaojin.models import AccountSnapshot

    snapshot = AccountSnapshot(
        captured_at=NOW,
        account_fingerprint=fingerprint,
        total_assets=Decimal("1000"),
        available_cash=Decimal("0"),
        frozen_cash=Decimal("0"),
        positions=tuple(positions),
        empty_positions_confirmed=not positions,
    )
    path.write_text(
        json.dumps(snapshot.to_persisted_dict(), ensure_ascii=False),
        encoding="utf-8",
    )


def _write_pool(path, *, items=None, updated_at="2026-07-27 20:30:00"):
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "updated_at": updated_at,
                "items": items or {},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _service(
    tmp_path,
    bridge,
    *,
    write_enabled=True,
    monotonic_provider=None,
):
    from app.integrations.yitaojin.service import YitaojinSyncService
    from app.integrations.yitaojin.state import YitaojinStateStore

    account_path = tmp_path / "account.json"
    pool_path = tmp_path / "pool.json"
    state_store = YitaojinStateStore(
        tmp_path / "watchlist.json",
        audit_path=tmp_path / "watchlist.jsonl",
    )
    environment = {"CONGXI_YITAOJIN_ENABLED": "true"}
    if write_enabled:
        environment["CONGXI_YITAOJIN_WRITE_ENABLED"] = "true"
    service = YitaojinSyncService(
        bridge=bridge,
        state_store=state_store,
        account_snapshot_path=account_path,
        candidate_pool_path=pool_path,
        environment=environment,
        now_provider=lambda: NOW,
        monotonic_provider=monotonic_provider,
    )
    return service, state_store, account_path, pool_path


def _bootstrap(service, account_path, pool_path):
    _write_account(account_path)
    _write_pool(pool_path)
    result = service.sync_watchlist(bootstrap=True)
    assert result.status == "bootstrapped"


def _write_commands(bridge):
    from app.integrations.yitaojin.models import BridgeCommand

    return [
        command
        for command, _, _ in bridge.calls
        if command
        in {
            BridgeCommand.ADD_WATCHLIST,
            BridgeCommand.REMOVE_WATCHLIST,
        }
    ]


def test_watchlist_bootstrap_protects_current_codes_without_writing(tmp_path):
    """Catches first-run ownership claiming or deleting the user's own list."""
    bridge = FakeBridge({"000001", "600000"})
    service, state_store, account_path, pool_path = _service(tmp_path, bridge)
    _write_account(account_path)
    _write_pool(pool_path)

    result = service.sync_watchlist(bootstrap=True)
    state = state_store.load()

    assert result.status == "bootstrapped"
    assert result.applied is False
    assert state.account_fingerprint == FINGERPRINT
    assert state.manual_protected_codes == ("000001", "600000")
    assert state.managed_codes == ()
    assert _write_commands(bridge) == []


def test_watchlist_sync_holds_one_cross_process_run_lock(tmp_path):
    """Catches scheduler and manual runs interleaving broker UI mutations."""
    bridge = FakeBridge()
    service, state_store, account_path, pool_path = _service(tmp_path, bridge)
    _write_account(account_path)
    _write_pool(pool_path)
    entries = []

    @contextmanager
    def tracked_lock():
        entries.append("entered")
        yield
        entries.append("exited")

    state_store.sync_lock = tracked_lock

    result = service.sync_watchlist(bootstrap=True)

    assert result.status == "bootstrapped"
    assert entries == ["entered", "exited"]


def test_watchlist_sync_blocks_when_total_run_budget_is_exhausted(tmp_path):
    """Catches many individually bounded UI calls creating an unbounded cycle."""
    ticks = iter((0.0, 61.0))
    bridge = FakeBridge()
    service, _, account_path, pool_path = _service(
        tmp_path,
        bridge,
        monotonic_provider=lambda: next(ticks),
    )
    _write_account(account_path)
    _write_pool(pool_path)

    result = service.sync_watchlist(bootstrap=True)

    assert result.status == "blocked"
    assert result.reasons == ("BridgeUnavailableError",)
    assert bridge.calls == []


def test_watchlist_dry_run_plans_add_without_mutating_state_or_app(tmp_path):
    """Catches preview mode changing ownership, counters, or the broker UI."""
    bridge = FakeBridge({"000001"})
    service, state_store, account_path, pool_path = _service(tmp_path, bridge)
    _bootstrap(service, account_path, pool_path)
    before = state_store.load()
    _write_pool(
        pool_path,
        items={"600000": {"code": "600000", "status": "executable"}},
    )

    result = service.sync_watchlist(apply=False)

    assert result.status == "planned"
    assert result.plan is not None
    assert result.plan.add == ("600000",)
    assert state_store.load() == before
    assert bridge.codes == {"000001"}
    assert _write_commands(bridge) == []


def test_watchlist_apply_requires_write_flag_before_any_ui_write(tmp_path):
    """Catches the Python caller bypassing the Swift write gate."""
    bridge = FakeBridge()
    service, _, account_path, pool_path = _service(
        tmp_path,
        bridge,
        write_enabled=False,
    )
    _bootstrap(service, account_path, pool_path)
    _write_pool(
        pool_path,
        items={"600000": {"code": "600000", "status": "watching"}},
    )

    result = service.sync_watchlist(apply=True)

    assert result.status == "blocked"
    assert result.reasons == ("write_disabled",)
    assert _write_commands(bridge) == []


def test_confirmed_add_becomes_system_managed(tmp_path):
    """Catches ownership being recorded before the App confirms the add."""
    bridge = FakeBridge()
    service, state_store, account_path, pool_path = _service(tmp_path, bridge)
    _bootstrap(service, account_path, pool_path)
    _write_pool(
        pool_path,
        items={"600000": {"code": "600000", "status": "executable"}},
    )

    result = service.sync_watchlist(apply=True)
    state = state_store.load()

    assert result.status == "applied"
    assert result.applied is True
    assert bridge.codes == {"600000"}
    assert state.managed_codes == ("600000",)
    assert state.successful_apply_count == 1


def test_any_add_failure_skips_all_removals(tmp_path):
    """Catches a partial refresh deleting old coverage before new coverage exists."""
    bridge = FakeBridge({"000001"})
    bridge.fail_add.add("600000")
    service, state_store, account_path, pool_path = _service(tmp_path, bridge)
    _write_account(account_path)
    _write_pool(
        pool_path,
        items={"600000": {"code": "600000", "status": "executable"}},
    )
    from app.integrations.yitaojin.state import WatchlistState

    state_store.save(
        WatchlistState(
            account_fingerprint=FINGERPRINT,
            managed_codes=("000001",),
            pending_removal_counts={"000001": 1},
            successful_apply_count=3,
        )
    )

    result = service.sync_watchlist(apply=True)

    assert result.status == "partial"
    assert bridge.codes == {"000001"}
    assert state_store.load().managed_codes == ("000001",)
    assert state_store.load().successful_apply_count == 3
    from app.integrations.yitaojin.models import BridgeCommand

    assert _write_commands(bridge) == [BridgeCommand.ADD_WATCHLIST]


def test_confirmed_add_before_later_failure_keeps_system_ownership(tmp_path):
    """Catches a partial add becoming permanently protected as if user-owned."""
    bridge = FakeBridge()
    bridge.fail_add.add("600000")
    service, state_store, account_path, pool_path = _service(tmp_path, bridge)
    _write_account(account_path)
    _write_pool(
        pool_path,
        items={
            "000001": {"code": "000001", "status": "watching"},
            "600000": {"code": "600000", "status": "executable"},
        },
    )
    from app.integrations.yitaojin.state import WatchlistState

    state_store.save(WatchlistState(account_fingerprint=FINGERPRINT))

    result = service.sync_watchlist(apply=True)

    assert result.status == "partial"
    assert bridge.codes == {"000001"}
    assert state_store.load().managed_codes == ("000001",)
    assert state_store.load().successful_apply_count == 0


def test_failed_remove_stays_managed_and_pending(tmp_path):
    """Catches an unconfirmed delete being forgotten by the next safe cycle."""
    bridge = FakeBridge({"600000"})
    bridge.fail_remove.add("600000")
    service, state_store, account_path, pool_path = _service(tmp_path, bridge)
    _write_account(account_path)
    _write_pool(pool_path)
    from app.integrations.yitaojin.state import WatchlistState

    state_store.save(
        WatchlistState(
            account_fingerprint=FINGERPRINT,
            managed_codes=("600000",),
            pending_removal_counts={"600000": 1},
            successful_apply_count=3,
        )
    )

    result = service.sync_watchlist(apply=True)

    assert result.status == "partial"
    assert bridge.codes == {"600000"}
    assert state_store.load().managed_codes == ("600000",)
    assert state_store.load().pending_removal_counts == {"600000": 2}
    assert state_store.load().successful_apply_count == 3


def test_removal_waits_for_three_successes_and_two_exit_confirmations(tmp_path):
    """Catches a transient pool exit removing a system-added stock too early."""
    bridge = FakeBridge({"600000"})
    service, state_store, account_path, pool_path = _service(tmp_path, bridge)
    _write_account(account_path)
    _write_pool(pool_path)
    from app.integrations.yitaojin.state import WatchlistState

    state_store.save(
        WatchlistState(
            account_fingerprint=FINGERPRINT,
            managed_codes=("600000",),
        )
    )

    for expected_count in (1, 2, 3):
        result = service.sync_watchlist(apply=True)
        assert result.status == "applied"
        assert state_store.load().successful_apply_count == expected_count
        assert bridge.codes == {"600000"}
        assert state_store.load().pending_removal_counts == {}

    first_exit = service.sync_watchlist(apply=True)
    assert first_exit.status == "applied"
    assert bridge.codes == {"600000"}
    assert state_store.load().pending_removal_counts == {"600000": 1}

    second_exit = service.sync_watchlist(apply=True)
    assert second_exit.status == "applied"
    assert bridge.codes == set()
    assert state_store.load().managed_codes == ()


def test_held_code_is_never_removed(tmp_path):
    """Catches candidate lifecycle overriding the account holdings truth."""
    bridge = FakeBridge({"600000"})
    service, state_store, account_path, pool_path = _service(tmp_path, bridge)
    _write_account(account_path, positions=(_position("600000"),))
    _write_pool(pool_path)
    from app.integrations.yitaojin.state import WatchlistState

    state_store.save(
        WatchlistState(
            account_fingerprint=FINGERPRINT,
            managed_codes=("600000",),
            pending_removal_counts={"600000": 1},
            successful_apply_count=9,
        )
    )

    result = service.sync_watchlist(apply=True)

    assert result.status == "applied"
    assert bridge.codes == {"600000"}
    assert state_store.load().pending_removal_counts == {}


def test_stale_pool_blocks_all_changes(tmp_path):
    """Catches old target-pool data driving broker UI changes."""
    bridge = FakeBridge()
    service, state_store, account_path, pool_path = _service(tmp_path, bridge)
    _bootstrap(service, account_path, pool_path)
    before = state_store.load()
    _write_pool(
        pool_path,
        items={"600000": {"code": "600000", "status": "executable"}},
        updated_at="2026-07-24 20:30:00",
    )

    result = service.sync_watchlist(apply=True)

    assert result.status == "blocked"
    assert "candidate_pool_missed_completed_trading_day" in result.reasons
    assert state_store.load() == before
    assert _write_commands(bridge) == []


def test_corrupt_pool_blocks_all_changes(tmp_path):
    """Catches malformed lifecycle data degrading into an empty desired list."""
    bridge = FakeBridge({"600000"})
    service, state_store, account_path, pool_path = _service(tmp_path, bridge)
    _bootstrap(service, account_path, pool_path)
    before = state_store.load()
    pool_path.write_text("{broken", encoding="utf-8")

    result = service.sync_watchlist(apply=True)

    assert result.status == "blocked"
    assert result.reasons == ("candidate_pool_invalid",)
    assert state_store.load() == before
    assert _write_commands(bridge) == []


def test_account_fingerprint_mismatch_blocks_before_app_write(tmp_path):
    """Catches another logged-in account inheriting watchlist ownership."""
    bridge = FakeBridge()
    service, state_store, account_path, pool_path = _service(tmp_path, bridge)
    _bootstrap(service, account_path, pool_path)
    _write_account(account_path, fingerprint="sha256:" + "b" * 64)
    _write_pool(
        pool_path,
        items={"600000": {"code": "600000", "status": "watching"}},
    )

    result = service.sync_watchlist(apply=True)

    assert result.status == "blocked"
    assert result.reasons == ("account_fingerprint_mismatch",)
    assert _write_commands(bridge) == []


def test_manual_code_remains_protected_across_exit_cycles(tmp_path):
    """Catches a pre-existing user-selected stock becoming deletion eligible."""
    bridge = FakeBridge({"000001"})
    service, state_store, account_path, pool_path = _service(tmp_path, bridge)
    _bootstrap(service, account_path, pool_path)
    _write_pool(pool_path)

    for _ in range(6):
        result = service.sync_watchlist(apply=True)
        assert result.status == "applied"

    assert bridge.codes == {"000001"}
    assert state_store.load().manual_protected_codes == ("000001",)
    assert _write_commands(bridge) == []


def test_app_login_error_is_returned_as_blocked_result(tmp_path):
    """Catches broker login loss being interpreted as an empty watchlist."""
    from app.integrations.yitaojin.models import AppNotLoggedInError

    bridge = FakeBridge()
    service, _, account_path, pool_path = _service(tmp_path, bridge)
    _write_account(account_path)
    _write_pool(pool_path)
    bridge.error = AppNotLoggedInError("not logged in")

    result = service.sync_watchlist(bootstrap=True)

    assert result.status == "blocked"
    assert result.reasons == ("AppNotLoggedInError",)


def test_idempotent_verified_apply_does_not_send_mutations(tmp_path):
    """Catches repeated syncs producing unnecessary UI writes."""
    bridge = FakeBridge({"600000"})
    service, state_store, account_path, pool_path = _service(tmp_path, bridge)
    _write_account(account_path)
    _write_pool(
        pool_path,
        items={"600000": {"code": "600000", "status": "executable"}},
    )
    from app.integrations.yitaojin.state import WatchlistState

    state_store.save(
        WatchlistState(
            account_fingerprint=FINGERPRINT,
            managed_codes=("600000",),
        )
    )

    result = service.sync_watchlist(apply=True)

    assert result.status == "applied"
    assert result.applied is True
    assert state_store.load().successful_apply_count == 1
    assert _write_commands(bridge) == []
