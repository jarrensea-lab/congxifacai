from __future__ import annotations

import json
import stat
from copy import deepcopy
from pathlib import Path

import pytest


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "yitaojin"


class FakeBridge:
    def __init__(self, payload: dict):
        self.payload = payload
        self.calls = []

    def run(self, command, payload=None, *, timeout=15.0):
        self.calls.append((command, payload, timeout))
        return deepcopy(self.payload)


class FailingBridge:
    def __init__(self, error):
        self.error = error
        self.calls = []

    def run(self, command, payload=None, *, timeout=15.0):
        self.calls.append((command, payload, timeout))
        raise self.error


def _valid_payload() -> dict:
    return json.loads(
        (FIXTURE_ROOT / "account_snapshot_valid.json").read_text(encoding="utf-8")
    )


def _portfolio() -> dict:
    return {
        "positions": [
            {
                "code": "000001",
                "name": "旧名称",
                "shares": 100,
                "avg_cost": 9.0,
                "current_price": 9.5,
                "trade_history": [{"date": "2026-07-01", "type": "buy"}],
            },
            {
                "code": "600000",
                "name": "已消失持仓",
                "shares": 100,
                "avg_cost": 8.0,
                "current_price": 8.0,
                "trade_history": [{"date": "2026-07-02", "type": "buy"}],
            },
        ],
        "available_cash": 1000.0,
        "realized_pnl": 12.5,
        "realized_pnl_complete": True,
        "trade_events": [],
    }


def _service(tmp_path, bridge, portfolio_path):
    from app.integrations.yitaojin.account import YitaojinAccountService

    return YitaojinAccountService(
        bridge=bridge,
        account_snapshot_path=tmp_path / "account.json",
        fingerprint_salt_path=tmp_path / "fingerprint-salt",
        audit_path=tmp_path / "account.jsonl",
        portfolio_path=portfolio_path,
    )


def test_account_dry_run_bootstrap_writes_isolated_snapshot_not_portfolio(tmp_path):
    """Catches dry-run mutating live holdings while binding the account."""
    portfolio_path = tmp_path / "portfolio.json"
    original = _portfolio()
    portfolio_path.write_text(json.dumps(original), encoding="utf-8")
    service = _service(tmp_path, FakeBridge(_valid_payload()), portfolio_path)

    result = service.sync_account(apply=False, bootstrap=True)

    assert result.status == "validated"
    assert result.applied is False
    assert json.loads(portfolio_path.read_text(encoding="utf-8")) == original
    assert (tmp_path / "account.json").exists()
    salt_path = tmp_path / "fingerprint-salt"
    assert stat.S_IMODE(salt_path.stat().st_mode) == 0o600
    assert salt_path.stat().st_size == 32


def test_account_sync_blocks_fingerprint_mismatch_without_mutation(tmp_path):
    """Catches a different logged-in account overwriting holdings truth."""
    portfolio_path = tmp_path / "portfolio.json"
    original = _portfolio()
    portfolio_path.write_text(json.dumps(original), encoding="utf-8")
    payload = _valid_payload()
    service = _service(tmp_path, FakeBridge(payload), portfolio_path)
    assert service.sync_account(apply=False, bootstrap=True).status == "validated"

    other = _valid_payload()
    other["accountFingerprint"] = "sha256:" + "b" * 64
    service.bridge = FakeBridge(other)
    result = service.sync_account(apply=True)

    assert result.status == "blocked"
    assert "account_fingerprint_mismatch" in result.reasons
    assert json.loads(portfolio_path.read_text(encoding="utf-8")) == original


def test_account_sync_blocks_unconfirmed_empty_positions(tmp_path):
    """Catches parser failure being interpreted as a real empty account."""
    portfolio_path = tmp_path / "portfolio.json"
    original = _portfolio()
    portfolio_path.write_text(json.dumps(original), encoding="utf-8")
    incomplete = json.loads(
        (FIXTURE_ROOT / "account_snapshot_incomplete.json").read_text(
            encoding="utf-8"
        )
    )
    service = _service(tmp_path, FakeBridge(incomplete), portfolio_path)

    result = service.sync_account(apply=True, bootstrap=True)

    assert result.status == "blocked"
    assert "empty_positions_unconfirmed" in result.reasons
    assert json.loads(portfolio_path.read_text(encoding="utf-8")) == original


def test_account_sync_converts_login_failure_to_blocked_result(tmp_path):
    """Catches an App login failure escaping the fail-closed account boundary."""
    from app.integrations.yitaojin.models import AppNotLoggedInError

    portfolio_path = tmp_path / "portfolio.json"
    original = _portfolio()
    portfolio_path.write_text(json.dumps(original), encoding="utf-8")
    service = _service(
        tmp_path,
        FailingBridge(AppNotLoggedInError("not logged in")),
        portfolio_path,
    )

    result = service.sync_account(apply=True, bootstrap=True)

    assert result.status == "blocked"
    assert result.reasons == ("AppNotLoggedInError",)
    assert json.loads(portfolio_path.read_text(encoding="utf-8")) == original


def test_account_sync_blocks_corrupt_trusted_snapshot_before_bridge_call(tmp_path):
    """Catches corrupted account binding being silently replaced."""
    portfolio_path = tmp_path / "portfolio.json"
    original = _portfolio()
    portfolio_path.write_text(json.dumps(original), encoding="utf-8")
    bridge = FakeBridge(_valid_payload())
    service = _service(tmp_path, bridge, portfolio_path)
    (tmp_path / "account.json").write_text(
        json.dumps(
            {
                "captured_at": "2026-07-26T09:30:05+08:00",
                "account_fingerprint": "raw-account-id",
                "total_assets": "4100",
                "available_cash": "1000",
                "frozen_cash": "100",
                "positions": [],
            }
        ),
        encoding="utf-8",
    )

    result = service.sync_account(apply=True)

    assert result.status == "blocked"
    assert result.reasons == ("trusted_snapshot_invalid",)
    assert bridge.calls == []
    assert json.loads(portfolio_path.read_text(encoding="utf-8")) == original


def test_account_sync_blocks_asset_formula_gap(tmp_path):
    """Catches incomplete positions reconciling against an unrelated asset total."""
    portfolio_path = tmp_path / "portfolio.json"
    original = _portfolio()
    portfolio_path.write_text(json.dumps(original), encoding="utf-8")
    payload = _valid_payload()
    payload["totalAssets"] = "5000.00"
    service = _service(tmp_path, FakeBridge(payload), portfolio_path)

    result = service.sync_account(apply=True, bootstrap=True)

    assert result.status == "blocked"
    assert "asset_reconciliation_gap" in result.reasons
    assert json.loads(portfolio_path.read_text(encoding="utf-8")) == original
    audit = json.loads(
        (tmp_path / "account.jsonl").read_text(encoding="utf-8").splitlines()[-1]
    )
    assert audit["mode"] == "apply"
    assert audit["result"]["applied"] is False


def test_account_sync_requires_review_for_large_asset_jump(tmp_path):
    """Catches a structurally valid but implausible asset jump auto-applying."""
    portfolio_path = tmp_path / "portfolio.json"
    original = _portfolio()
    portfolio_path.write_text(json.dumps(original), encoding="utf-8")
    service = _service(tmp_path, FakeBridge(_valid_payload()), portfolio_path)
    assert service.sync_account(apply=False, bootstrap=True).status == "validated"

    jumped = _valid_payload()
    jumped["availableCash"] = "3000.00"
    jumped["totalAssets"] = "6100.00"
    service.bridge = FakeBridge(jumped)
    result = service.sync_account(apply=True)

    assert result.status == "requires_manual_review"
    assert "total_assets_changed_over_20pct" in result.reasons
    assert json.loads(portfolio_path.read_text(encoding="utf-8")) == original


def test_account_apply_preserves_history_and_never_invents_realized_pnl(tmp_path):
    """Catches broker reconciliation fabricating fills or realized profit."""
    portfolio_path = tmp_path / "portfolio.json"
    portfolio_path.write_text(json.dumps(_portfolio()), encoding="utf-8")
    service = _service(tmp_path, FakeBridge(_valid_payload()), portfolio_path)

    result = service.sync_account(apply=True, bootstrap=True)
    updated = json.loads(portfolio_path.read_text(encoding="utf-8"))
    positions = {item["code"]: item for item in updated["positions"]}

    assert result.status == "applied"
    assert result.applied is True
    assert positions["000001"]["trade_history"] == [
        {"date": "2026-07-01", "type": "buy"}
    ]
    assert positions["000001"]["available_shares"] == 100
    assert positions["300001"]["trade_history"] == []
    assert positions["300001"]["trade_history_status"] == "unattributed_broker_position"
    assert "600000" not in positions
    pending_positions = {
        item["code"]: item
        for item in updated["broker_missing_positions_pending"]
    }
    assert pending_positions["600000"]["trade_history"] == [
        {"date": "2026-07-02", "type": "buy"}
    ]
    assert pending_positions["600000"]["realized_pnl_status"] == (
        "pending_attribution"
    )
    assert "600000" in updated["realized_pnl_pending_codes"]
    assert updated["realized_pnl"] == 12.5
    assert updated["realized_pnl_complete"] is False
    assert updated["frozen_cash"] == 100.0
    assert updated["total_assets"] == 4100.0
    assert updated["broker_snapshot"]["reported_total_assets"] == 4100.0
    assert updated["portfolio_sync_status"] == "broker_applied"


def test_account_apply_restores_history_when_pending_position_reappears(tmp_path):
    """Catches a transient broker omission permanently discarding local history."""
    portfolio_path = tmp_path / "portfolio.json"
    portfolio_path.write_text(json.dumps(_portfolio()), encoding="utf-8")
    bridge = FakeBridge(_valid_payload())
    service = _service(tmp_path, bridge, portfolio_path)
    assert service.sync_account(apply=True, bootstrap=True).status == "applied"

    reappeared = _valid_payload()
    reappeared["capturedAt"] = "2026-07-26T09:35:05+08:00"
    reappeared["totalAssets"] = "4900.00"
    reappeared["positions"].append(
        {
            "code": "600000",
            "name": "已消失持仓",
            "shares": 100,
            "availableShares": 100,
            "averageCost": "8.00",
            "currentPrice": "8.00",
            "marketValue": "800.00",
            "unrealizedPnl": "0.00",
        }
    )
    service.bridge = FakeBridge(reappeared)

    result = service.sync_account(apply=True)
    updated = json.loads(portfolio_path.read_text(encoding="utf-8"))
    positions = {item["code"]: item for item in updated["positions"]}

    assert result.status == "applied"
    assert positions["600000"]["trade_history"] == [
        {"date": "2026-07-02", "type": "buy"}
    ]
    assert updated["broker_missing_positions_pending"] == []
    assert "600000" not in updated["realized_pnl_pending_codes"]
