"""Validated account snapshot and portfolio reconciliation."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import stat
import tempfile
import uuid
from copy import deepcopy
from dataclasses import asdict, dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping

from app.integrations.yitaojin.models import (
    AccountSnapshot,
    BridgeCommand,
    BridgeUnavailableError,
    SnapshotValidationError,
    YitaojinError,
)
from app.integrations.yitaojin.state import YitaojinStateStore
from app.services.portfolio_store import (
    load_user_portfolio,
    portfolio_transaction_lock,
    recalculate_portfolio,
    save_user_portfolio,
)


@dataclass(frozen=True)
class PositionChange:
    code: str
    before: Decimal | int | None
    after: Decimal | int | None


@dataclass(frozen=True)
class AccountDiff:
    added_positions: tuple[str, ...]
    removed_positions: tuple[str, ...]
    changed_shares: tuple[PositionChange, ...]
    changed_costs: tuple[PositionChange, ...]
    total_asset_delta: Decimal
    cash_delta: Decimal


@dataclass(frozen=True)
class AccountSyncResult:
    status: str
    requested_apply: bool
    applied: bool
    requires_manual_review: bool
    reasons: tuple[str, ...]
    snapshot: AccountSnapshot | None
    diff: AccountDiff | None


class YitaojinAccountService:
    def __init__(
        self,
        *,
        bridge,
        account_snapshot_path: str | Path,
        fingerprint_salt_path: str | Path,
        audit_path: str | Path,
        portfolio_path: str | Path,
        max_asset_change_ratio: Decimal = Decimal("0.20"),
        asset_reconciliation_tolerance: Decimal = Decimal("1.00"),
    ) -> None:
        self.bridge = bridge
        self.account_snapshot_path = Path(account_snapshot_path)
        self.fingerprint_salt_path = Path(fingerprint_salt_path)
        self.audit_path = Path(audit_path)
        self.portfolio_path = str(portfolio_path)
        self.max_asset_change_ratio = max_asset_change_ratio
        self.asset_reconciliation_tolerance = asset_reconciliation_tolerance

    def sync_account(
        self,
        *,
        apply: bool = False,
        bootstrap: bool = False,
    ) -> AccountSyncResult:
        try:
            previous = self._load_snapshot()
        except BridgeUnavailableError:
            return self._result(
                status="blocked",
                reasons=("trusted_snapshot_invalid",),
                snapshot=None,
                diff=None,
                requested_apply=apply,
                applied=False,
            )
        if previous is None and not bootstrap:
            return self._result(
                status="blocked",
                reasons=("account_not_bootstrapped",),
                snapshot=None,
                diff=None,
                requested_apply=apply,
                applied=False,
            )
        try:
            salt = self._load_or_create_salt(bootstrap=bootstrap)
            payload = self.bridge.run(
                BridgeCommand.READ_ACCOUNT,
                {
                    "fingerprintSalt": base64.b64encode(salt).decode("ascii"),
                },
            )
            snapshot = AccountSnapshot.from_bridge_payload(payload)
        except (YitaojinError, SnapshotValidationError) as exc:
            return self._result(
                status="blocked",
                reasons=(exc.__class__.__name__,),
                snapshot=None,
                diff=None,
                requested_apply=apply,
                applied=False,
            )

        reasons = self._validation_reasons(snapshot, previous)
        diff = self._build_diff(snapshot)
        if reasons:
            status = (
                "requires_manual_review"
                if reasons == ["total_assets_changed_over_20pct"]
                else "blocked"
            )
            return self._result(
                status=status,
                reasons=tuple(reasons),
                snapshot=snapshot,
                diff=diff,
                requested_apply=apply,
                applied=False,
            )

        if apply:
            self._apply_to_portfolio(snapshot)
            status = "applied"
        else:
            status = "validated"
        self._save_snapshot(snapshot)
        return self._result(
            status=status,
            reasons=(),
            snapshot=snapshot,
            diff=diff,
            requested_apply=apply,
            applied=apply,
        )

    def _validation_reasons(
        self,
        snapshot: AccountSnapshot,
        previous: AccountSnapshot | None,
    ) -> list[str]:
        reasons: list[str] = []
        if (
            previous is not None
            and snapshot.account_fingerprint != previous.account_fingerprint
        ):
            reasons.append("account_fingerprint_mismatch")
        if not snapshot.positions and not snapshot.empty_positions_confirmed:
            reasons.append("empty_positions_unconfirmed")
        computed_assets = (
            snapshot.available_cash
            + snapshot.frozen_cash
            + sum(
                (position.market_value for position in snapshot.positions),
                Decimal("0"),
            )
        )
        if abs(snapshot.total_assets - computed_assets) > self.asset_reconciliation_tolerance:
            reasons.append("asset_reconciliation_gap")
        if previous is not None and previous.total_assets > 0:
            ratio = abs(snapshot.total_assets - previous.total_assets) / previous.total_assets
            if ratio > self.max_asset_change_ratio:
                reasons.append("total_assets_changed_over_20pct")
        return reasons

    def _build_diff(self, snapshot: AccountSnapshot) -> AccountDiff:
        portfolio = load_user_portfolio(self.portfolio_path)
        old_positions = {
            str(item.get("code") or ""): item
            for item in portfolio.get("positions", [])
            if isinstance(item, dict) and item.get("code")
        }
        new_positions = {position.code: position for position in snapshot.positions}
        changed_shares = []
        changed_costs = []
        for code in sorted(old_positions.keys() & new_positions.keys()):
            old = old_positions[code]
            new = new_positions[code]
            old_shares = int(old.get("shares", 0) or 0)
            if old_shares != new.shares:
                changed_shares.append(
                    PositionChange(code=code, before=old_shares, after=new.shares)
                )
            old_cost = Decimal(str(old.get("avg_cost", 0) or 0))
            if old_cost != new.average_cost:
                changed_costs.append(
                    PositionChange(
                        code=code,
                        before=old_cost,
                        after=new.average_cost,
                    )
                )
        return AccountDiff(
            added_positions=tuple(sorted(new_positions.keys() - old_positions.keys())),
            removed_positions=tuple(sorted(old_positions.keys() - new_positions.keys())),
            changed_shares=tuple(changed_shares),
            changed_costs=tuple(changed_costs),
            total_asset_delta=snapshot.total_assets
            - Decimal(str(portfolio.get("total_assets", 0) or 0)),
            cash_delta=snapshot.available_cash
            - Decimal(
                str(
                    portfolio.get(
                        "available_cash",
                        portfolio.get("cash", 0),
                    )
                    or 0
                )
            ),
        )

    def _apply_to_portfolio(self, snapshot: AccountSnapshot) -> None:
        with portfolio_transaction_lock(self.portfolio_path):
            portfolio = load_user_portfolio(self.portfolio_path)
            existing = {
                str(item.get("code") or ""): item
                for item in portfolio.get("positions", [])
                if isinstance(item, dict) and item.get("code")
            }
            reconciled = []
            for broker_position in snapshot.positions:
                current = deepcopy(existing.get(broker_position.code, {}))
                is_new = not current
                current.update(
                    {
                        "code": broker_position.code,
                        "name": broker_position.name,
                        "shares": broker_position.shares,
                        "available_shares": broker_position.available_shares,
                        "avg_cost": float(broker_position.average_cost),
                        "current_price": float(broker_position.current_price),
                        "broker_market_value": float(broker_position.market_value),
                        "broker_unrealized_pnl": float(
                            broker_position.unrealized_pnl
                        ),
                        "source": "yitaojin_ui",
                        "quote_source": "yitaojin_ui",
                        "quote_captured_at": snapshot.captured_at.isoformat(),
                    }
                )
                if is_new:
                    current["trade_history"] = []
                    current["trade_history_status"] = (
                        "unattributed_broker_position"
                    )
                reconciled.append(current)

            removed_codes = sorted(existing.keys() - {p.code for p in snapshot.positions})
            events = portfolio.setdefault("recent_account_events", [])
            if not isinstance(events, list):
                events = []
                portfolio["recent_account_events"] = events
            for code in removed_codes:
                events.append(
                    {
                        "event": "broker_position_missing",
                        "code": code,
                        "captured_at": snapshot.captured_at.isoformat(),
                        "realized_pnl_status": "pending_attribution",
                    }
                )
            if removed_codes:
                pending = set(portfolio.get("realized_pnl_pending_codes") or [])
                pending.update(removed_codes)
                portfolio["realized_pnl_pending_codes"] = sorted(pending)
                portfolio["realized_pnl_complete"] = False

            portfolio["positions"] = reconciled
            portfolio["available_cash"] = float(snapshot.available_cash)
            portfolio["cash"] = float(snapshot.available_cash)
            portfolio["frozen_cash"] = float(snapshot.frozen_cash)
            portfolio["source"] = "yitaojin_ui"
            portfolio["portfolio_sync_failed"] = False
            portfolio["portfolio_sync_status"] = "broker_applied"
            computed_assets = (
                snapshot.available_cash
                + snapshot.frozen_cash
                + sum(
                    (position.market_value for position in snapshot.positions),
                    Decimal("0"),
                )
            )
            portfolio["broker_snapshot"] = {
                "source": "yitaojin_ui",
                "captured_at": snapshot.captured_at.isoformat(),
                "account_fingerprint": snapshot.account_fingerprint,
                "reported_total_assets": float(snapshot.total_assets),
                "position_count": len(snapshot.positions),
                "reconciliation_gap": float(
                    snapshot.total_assets - computed_assets
                ),
            }
            recalculate_portfolio(portfolio)
            save_user_portfolio(portfolio, self.portfolio_path)

    def _load_or_create_salt(self, *, bootstrap: bool) -> bytes:
        path = self.fingerprint_salt_path
        if path.exists():
            if path.is_symlink() or not path.is_file():
                raise BridgeUnavailableError("fingerprint salt path is unsafe")
            mode = stat.S_IMODE(path.stat().st_mode)
            if mode != 0o600:
                raise BridgeUnavailableError("fingerprint salt permissions are unsafe")
            salt = path.read_bytes()
            if len(salt) != 32:
                raise BridgeUnavailableError("fingerprint salt length is invalid")
            return salt
        if not bootstrap:
            raise BridgeUnavailableError("fingerprint salt is not bootstrapped")
        path.parent.mkdir(parents=True, exist_ok=True)
        salt = secrets.token_bytes(32)
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        try:
            with os.fdopen(descriptor, "wb") as salt_file:
                salt_file.write(salt)
                salt_file.flush()
                os.fsync(salt_file.fileno())
        except Exception:
            path.unlink(missing_ok=True)
            raise
        return salt

    def _load_snapshot(self) -> AccountSnapshot | None:
        if not self.account_snapshot_path.exists():
            return None
        try:
            payload = json.loads(
                self.account_snapshot_path.read_text(encoding="utf-8")
            )
            return AccountSnapshot.from_persisted_dict(payload)
        except (OSError, json.JSONDecodeError, SnapshotValidationError) as exc:
            raise BridgeUnavailableError("trusted account snapshot is unreadable") from exc

    def _save_snapshot(self, snapshot: AccountSnapshot) -> None:
        path = self.account_snapshot_path
        path.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(
            snapshot.to_persisted_dict(),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        descriptor, temp_name = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
        )
        temp_path = Path(temp_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as temp_file:
                temp_file.write(encoded)
                temp_file.write("\n")
                temp_file.flush()
                os.fsync(temp_file.fileno())
            os.replace(temp_path, path)
        except Exception:
            temp_path.unlink(missing_ok=True)
            raise

    def _result(
        self,
        *,
        status: str,
        reasons: tuple[str, ...],
        snapshot: AccountSnapshot | None,
        diff: AccountDiff | None,
        requested_apply: bool,
        applied: bool,
    ) -> AccountSyncResult:
        result = AccountSyncResult(
            status=status,
            requested_apply=requested_apply,
            applied=applied,
            requires_manual_review=status == "requires_manual_review",
            reasons=reasons,
            snapshot=snapshot,
            diff=diff,
        )
        self._append_audit(result)
        return result

    def _append_audit(self, result: AccountSyncResult) -> None:
        snapshot_payload = (
            result.snapshot.to_persisted_dict() if result.snapshot else {}
        )
        canonical = json.dumps(
            snapshot_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        input_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        diff = _json_safe(asdict(result.diff)) if result.diff is not None else {}
        store = YitaojinStateStore(
            self.account_snapshot_path,
            audit_path=self.audit_path,
        )
        store.append_audit(
            {
                "run_id": str(uuid.uuid4()),
                "mode": "apply" if result.requested_apply else "dry-run",
                "input_hash": input_hash,
                "plan": diff,
                "result": {
                    "status": result.status,
                    "applied": result.applied,
                    "reasons": list(result.reasons),
                },
            }
        )


def _json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _json_safe(nested) for key, nested in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(nested) for nested in value]
    return value
