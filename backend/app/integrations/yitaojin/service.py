"""Fail-closed orchestration for Yitaojin watchlist synchronization."""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping
from zoneinfo import ZoneInfo

from app.integrations.yitaojin.models import (
    AccountSnapshot,
    BridgeCommand,
    BridgeUnavailableError,
    SnapshotValidationError,
    WatchlistPlan,
    YitaojinError,
    normalize_stock_code,
)
from app.integrations.yitaojin.planner import (
    build_desired_codes,
    plan_watchlist_sync,
    validate_candidate_pool_freshness,
)
from app.integrations.yitaojin.state import (
    StateStoreError,
    WatchlistState,
    YitaojinStateStore,
)


PROJECT_TIMEZONE = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True)
class WatchlistActionResult:
    command: str
    code: str
    state: str
    confirmed: bool


@dataclass(frozen=True)
class WatchlistSyncResult:
    status: str
    requested_apply: bool
    applied: bool
    reasons: tuple[str, ...]
    plan: WatchlistPlan | None
    actions: tuple[WatchlistActionResult, ...]


class YitaojinSyncService:
    def __init__(
        self,
        *,
        bridge,
        state_store: YitaojinStateStore,
        account_snapshot_path: str | Path,
        candidate_pool_path: str | Path,
        environment: Mapping[str, str] | None = None,
        now_provider: Callable[[], datetime] | None = None,
        monotonic_provider: Callable[[], float] | None = None,
        bridge_timeout_seconds: float = 15.0,
        total_budget_seconds: float = 60.0,
    ) -> None:
        self.bridge = bridge
        self.state_store = state_store
        self.account_snapshot_path = Path(account_snapshot_path)
        self.candidate_pool_path = Path(candidate_pool_path)
        self.environment = dict(os.environ if environment is None else environment)
        self.now_provider = now_provider or (lambda: datetime.now(tz=PROJECT_TIMEZONE))
        self.monotonic_provider = monotonic_provider or time.monotonic
        if bridge_timeout_seconds <= 0 or total_budget_seconds <= 0:
            raise ValueError("Yitaojin timeouts must be positive")
        self.bridge_timeout_seconds = bridge_timeout_seconds
        self.total_budget_seconds = total_budget_seconds
        self._deadline: float | None = None

    def sync_watchlist(
        self,
        *,
        apply: bool = False,
        bootstrap: bool = False,
    ) -> WatchlistSyncResult:
        with self.state_store.sync_lock():
            self._deadline = self.monotonic_provider() + self.total_budget_seconds
            try:
                return self._sync_watchlist_locked(
                    apply=apply,
                    bootstrap=bootstrap,
                )
            finally:
                self._deadline = None

    def _sync_watchlist_locked(
        self,
        *,
        apply: bool,
        bootstrap: bool,
    ) -> WatchlistSyncResult:
        mode = "bootstrap" if bootstrap else ("apply" if apply else "dry-run")
        if not self._enabled("CONGXI_YITAOJIN_ENABLED"):
            return self._result(
                status="blocked",
                reasons=("integration_disabled",),
                plan=None,
                actions=(),
                apply=apply,
                mode=mode,
                input_payload={},
            )
        if apply and not (
            self._enabled("CONGXI_YITAOJIN_WRITE_ENABLED")
            or self._enabled("CONGXI_YITAOJIN_WATCHLIST_WRITE_ENABLED")
        ):
            return self._result(
                status="blocked",
                reasons=("write_disabled",),
                plan=None,
                actions=(),
                apply=True,
                mode=mode,
                input_payload={},
            )

        try:
            state = self.state_store.load()
        except StateStoreError:
            return self._result(
                status="blocked",
                reasons=("watchlist_state_invalid",),
                plan=None,
                actions=(),
                apply=apply,
                mode=mode,
                input_payload={},
            )
        try:
            account = self._load_account_snapshot()
        except (OSError, json.JSONDecodeError, SnapshotValidationError):
            return self._result(
                status="blocked",
                reasons=("trusted_account_snapshot_invalid",),
                plan=None,
                actions=(),
                apply=apply,
                mode=mode,
                input_payload={},
            )

        if bootstrap:
            return self._bootstrap(
                state=state,
                account=account,
                apply=apply,
                mode=mode,
            )
        if state.account_fingerprint is None:
            return self._result(
                status="blocked",
                reasons=("watchlist_not_bootstrapped",),
                plan=None,
                actions=(),
                apply=apply,
                mode=mode,
                input_payload={},
            )
        if state.account_fingerprint != account.account_fingerprint:
            return self._result(
                status="blocked",
                reasons=("account_fingerprint_mismatch",),
                plan=None,
                actions=(),
                apply=apply,
                mode=mode,
                input_payload={},
            )

        try:
            pool, pool_updated_at = self._load_candidate_pool()
        except (OSError, json.JSONDecodeError, SnapshotValidationError, ValueError):
            return self._result(
                status="blocked",
                reasons=("candidate_pool_invalid",),
                plan=None,
                actions=(),
                apply=apply,
                mode=mode,
                input_payload={},
            )
        freshness = validate_candidate_pool_freshness(
            pool_updated_at,
            now=self.now_provider(),
        )
        if not freshness.valid:
            return self._result(
                status="blocked",
                reasons=(f"candidate_pool_{freshness.reason}",),
                plan=None,
                actions=(),
                apply=apply,
                mode=mode,
                input_payload={
                    "pool_updated_at": pool_updated_at.isoformat(),
                    "required_trading_day": freshness.required_trading_day,
                },
            )

        try:
            current = self._read_watchlist()
            desired = build_desired_codes(pool, account.positions)
        except (YitaojinError, SnapshotValidationError) as exc:
            return self._result(
                status="blocked",
                reasons=(exc.__class__.__name__,),
                plan=None,
                actions=(),
                apply=apply,
                mode=mode,
                input_payload={},
            )
        held = {position.code for position in account.positions}
        planning = plan_watchlist_sync(
            desired_codes=desired,
            current_codes=current,
            held_codes=held,
            state=state,
            allow_removals=state.successful_apply_count >= 3,
            source_valid=True,
        )
        input_payload = {
            "pool_updated_at": pool_updated_at.isoformat(),
            "desired_codes": sorted(desired),
            "current_codes": sorted(current),
            "held_codes": sorted(held),
            "successful_apply_count": state.successful_apply_count,
        }
        if not apply:
            return self._result(
                status="planned",
                reasons=(),
                plan=planning.plan,
                actions=(),
                apply=False,
                mode=mode,
                input_payload=input_payload,
            )
        return self._apply_plan(
            state=state,
            planning_state=planning.next_state,
            plan=planning.plan,
            input_payload=input_payload,
            mode=mode,
        )

    def _bootstrap(
        self,
        *,
        state: WatchlistState,
        account: AccountSnapshot,
        apply: bool,
        mode: str,
    ) -> WatchlistSyncResult:
        if state.account_fingerprint is not None:
            reason = (
                "already_bootstrapped"
                if state.account_fingerprint == account.account_fingerprint
                else "account_fingerprint_mismatch"
            )
            return self._result(
                status="blocked",
                reasons=(reason,),
                plan=None,
                actions=(),
                apply=apply,
                mode=mode,
                input_payload={},
            )
        try:
            current = self._read_watchlist()
        except (YitaojinError, SnapshotValidationError) as exc:
            return self._result(
                status="blocked",
                reasons=(exc.__class__.__name__,),
                plan=None,
                actions=(),
                apply=apply,
                mode=mode,
                input_payload={},
            )
        snapshot_hash = self._snapshot_hash(
            {"current_codes": sorted(current), "bootstrap": True}
        )
        bootstrapped = replace(
            state,
            account_fingerprint=account.account_fingerprint,
            manual_protected_codes=tuple(sorted(current)),
            managed_codes=(),
            pending_removal_counts={},
            successful_apply_count=0,
            last_snapshot_hash=snapshot_hash,
        )
        self.state_store.save(bootstrapped)
        return self._result(
            status="bootstrapped",
            reasons=(),
            plan=None,
            actions=(),
            apply=apply,
            mode=mode,
            input_payload={"current_codes": sorted(current), "bootstrap": True},
        )

    def _apply_plan(
        self,
        *,
        state: WatchlistState,
        planning_state: WatchlistState,
        plan: WatchlistPlan,
        input_payload: Mapping[str, Any],
        mode: str,
    ) -> WatchlistSyncResult:
        actions: list[WatchlistActionResult] = []
        managed = set(state.managed_codes)
        added_codes: set[str] = set()

        for code in plan.add:
            action = self._run_mutation(BridgeCommand.ADD_WATCHLIST, code)
            actions.append(action)
            if not action.confirmed:
                partial = replace(
                    state,
                    manual_protected_codes=planning_state.manual_protected_codes,
                    managed_codes=tuple(sorted(managed | added_codes)),
                    last_snapshot_hash=self._snapshot_hash(input_payload),
                )
                self.state_store.save(partial)
                return self._result(
                    status="partial",
                    reasons=(f"add_unconfirmed:{code}",),
                    plan=plan,
                    actions=tuple(actions),
                    apply=True,
                    mode=mode,
                    input_payload=input_payload,
                )
            added_codes.add(code)
            managed.add(code)
            addition_checkpoint = replace(
                state,
                manual_protected_codes=planning_state.manual_protected_codes,
                managed_codes=tuple(sorted(managed)),
                last_snapshot_hash=self._snapshot_hash(input_payload),
            )
            self.state_store.save(addition_checkpoint)

        checkpoint = replace(
            state,
            manual_protected_codes=planning_state.manual_protected_codes,
            managed_codes=tuple(sorted(managed)),
            last_snapshot_hash=self._snapshot_hash(input_payload),
        )
        self.state_store.save(checkpoint)
        try:
            after_add = self._read_watchlist()
        except (YitaojinError, SnapshotValidationError) as exc:
            return self._result(
                status="partial",
                reasons=(f"add_recheck_failed:{exc.__class__.__name__}",),
                plan=plan,
                actions=tuple(actions),
                apply=True,
                mode=mode,
                input_payload=input_payload,
            )
        missing_adds = added_codes - after_add
        if missing_adds:
            return self._result(
                status="partial",
                reasons=tuple(
                    f"add_recheck_missing:{code}" for code in sorted(missing_adds)
                ),
                plan=plan,
                actions=tuple(actions),
                apply=True,
                mode=mode,
                input_payload=input_payload,
            )

        for code in plan.remove:
            if code not in managed:
                return self._result(
                    status="partial",
                    reasons=(f"remove_not_system_managed:{code}",),
                    plan=plan,
                    actions=tuple(actions),
                    apply=True,
                    mode=mode,
                    input_payload=input_payload,
                )
            action = self._run_mutation(BridgeCommand.REMOVE_WATCHLIST, code)
            actions.append(action)
            if not action.confirmed:
                partial = replace(
                    planning_state,
                    managed_codes=tuple(sorted(managed)),
                    successful_apply_count=state.successful_apply_count,
                    last_snapshot_hash=self._snapshot_hash(input_payload),
                )
                self.state_store.save(partial)
                return self._result(
                    status="partial",
                    reasons=(f"remove_unconfirmed:{code}",),
                    plan=plan,
                    actions=tuple(actions),
                    apply=True,
                    mode=mode,
                    input_payload=input_payload,
                )
            managed.remove(code)
            removal_checkpoint = replace(
                planning_state,
                managed_codes=tuple(sorted(managed)),
                successful_apply_count=state.successful_apply_count,
                pending_removal_counts={
                    pending_code: count
                    for pending_code, count in (
                        planning_state.pending_removal_counts or {}
                    ).items()
                    if pending_code in managed
                },
                last_snapshot_hash=self._snapshot_hash(input_payload),
            )
            self.state_store.save(removal_checkpoint)

        try:
            final_codes = self._read_watchlist()
        except (YitaojinError, SnapshotValidationError) as exc:
            return self._result(
                status="partial",
                reasons=(f"final_recheck_failed:{exc.__class__.__name__}",),
                plan=plan,
                actions=tuple(actions),
                apply=True,
                mode=mode,
                input_payload=input_payload,
            )
        missing = set(plan.add) - final_codes
        still_present = set(plan.remove) & final_codes
        if missing or still_present:
            reasons = [
                *(f"final_add_missing:{code}" for code in sorted(missing)),
                *(f"final_remove_present:{code}" for code in sorted(still_present)),
            ]
            return self._result(
                status="partial",
                reasons=tuple(reasons),
                plan=plan,
                actions=tuple(actions),
                apply=True,
                mode=mode,
                input_payload=input_payload,
            )

        pending_counts = {
            code: count
            for code, count in (planning_state.pending_removal_counts or {}).items()
            if code in managed
        }
        now = self.now_provider()
        final_state = replace(
            planning_state,
            managed_codes=tuple(sorted(managed)),
            pending_removal_counts=pending_counts,
            successful_apply_count=state.successful_apply_count + 1,
            last_success_at=now.isoformat(),
            last_snapshot_hash=self._snapshot_hash(
                {
                    **input_payload,
                    "final_codes": sorted(final_codes),
                }
            ),
        )
        self.state_store.save(final_state)
        return self._result(
            status="applied",
            reasons=(),
            plan=plan,
            actions=tuple(actions),
            apply=True,
            mode=mode,
            input_payload=input_payload,
            applied=True,
        )

    def _run_mutation(
        self,
        command: BridgeCommand,
        code: str,
    ) -> WatchlistActionResult:
        try:
            payload = self._bridge_run(command, {"code": code})
        except YitaojinError:
            return WatchlistActionResult(
                command=command.value,
                code=code,
                state="bridge_error",
                confirmed=False,
            )
        response_code = payload.get("code")
        state = str(payload.get("state") or "")
        confirmed = payload.get("confirmed")
        allowed_states = (
            {"added", "already_present"}
            if command == BridgeCommand.ADD_WATCHLIST
            else {"removed", "already_absent"}
        )
        valid = response_code == code and confirmed is True and state in allowed_states
        return WatchlistActionResult(
            command=command.value,
            code=code,
            state=state if state else "invalid_response",
            confirmed=valid,
        )

    def _read_watchlist(self) -> set[str]:
        payload = self._bridge_run(BridgeCommand.READ_WATCHLIST)
        raw_codes = payload.get("codes")
        if not isinstance(raw_codes, list):
            raise SnapshotValidationError("watchlist codes must be a list")
        return {normalize_stock_code(code) for code in raw_codes}

    def _bridge_run(
        self,
        command: BridgeCommand,
        payload: Mapping[str, object] | None = None,
    ) -> Mapping[str, Any]:
        if self._deadline is None:
            raise BridgeUnavailableError("watchlist sync has no active run budget")
        remaining = self._deadline - self.monotonic_provider()
        if remaining <= 0:
            raise BridgeUnavailableError("watchlist sync total budget exhausted")
        return self.bridge.run(
            command,
            payload,
            timeout=min(self.bridge_timeout_seconds, remaining),
        )

    def _load_account_snapshot(self) -> AccountSnapshot:
        payload = json.loads(self.account_snapshot_path.read_text(encoding="utf-8"))
        return AccountSnapshot.from_persisted_dict(payload)

    def _load_candidate_pool(
        self,
    ) -> tuple[Mapping[str, object], datetime]:
        payload = json.loads(self.candidate_pool_path.read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping):
            raise SnapshotValidationError("candidate pool must be an object")
        raw_items = payload.get("items")
        if isinstance(raw_items, Mapping):
            if any(not isinstance(item, Mapping) for item in raw_items.values()):
                raise SnapshotValidationError(
                    "candidate pool items must contain objects"
                )
        elif isinstance(raw_items, list):
            if any(not isinstance(item, Mapping) for item in raw_items):
                raise SnapshotValidationError(
                    "candidate pool items must contain objects"
                )
        else:
            raise SnapshotValidationError(
                "candidate pool items must be an object or list"
            )
        raw_updated_at = payload.get("updated_at")
        if not isinstance(raw_updated_at, str) or not raw_updated_at.strip():
            raise SnapshotValidationError("candidate pool updated_at is required")
        updated_at = datetime.fromisoformat(
            raw_updated_at.strip().replace("Z", "+00:00")
        )
        return payload, updated_at

    def _enabled(self, name: str) -> bool:
        return self.environment.get(name, "").strip().lower() == "true"

    @staticmethod
    def _snapshot_hash(payload: Mapping[str, Any]) -> str:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def _result(
        self,
        *,
        status: str,
        reasons: tuple[str, ...],
        plan: WatchlistPlan | None,
        actions: tuple[WatchlistActionResult, ...],
        apply: bool,
        mode: str,
        input_payload: Mapping[str, Any],
        applied: bool = False,
    ) -> WatchlistSyncResult:
        result = WatchlistSyncResult(
            status=status,
            requested_apply=apply,
            applied=applied,
            reasons=reasons,
            plan=plan,
            actions=actions,
        )
        self.state_store.append_audit(
            {
                "run_id": str(uuid.uuid4()),
                "mode": mode,
                "input_hash": self._snapshot_hash(input_payload),
                "plan": plan.to_dict() if plan is not None else {},
                "result": {
                    "status": status,
                    "requested_apply": apply,
                    "applied": applied,
                    "reasons": list(reasons),
                    "actions": [asdict(action) for action in actions],
                },
            }
        )
        return result
