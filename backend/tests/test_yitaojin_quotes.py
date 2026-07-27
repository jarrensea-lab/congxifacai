from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal

import pytest


NOW = datetime.fromisoformat("2026-07-27T10:00:00+08:00")


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


def _quote(
    *,
    code="600000",
    age_seconds=10,
    price="10.00",
    status="normal",
):
    from app.integrations.yitaojin.models import QuoteSnapshot

    market_time = NOW - timedelta(seconds=age_seconds)
    return QuoteSnapshot(
        code=code,
        captured_at=market_time + timedelta(seconds=1),
        market_time=market_time,
        price=Decimal(price),
        change_pct=Decimal("1.25"),
        volume=Decimal("1000"),
        amount=Decimal("10000"),
        high=Decimal("10.10"),
        low=Decimal("9.90"),
        previous_close=Decimal("9.88"),
        status=status,
    )


def _bridge_quote_payload(code="600000", *, age_seconds=10):
    quote = _quote(code=code, age_seconds=age_seconds)
    return {
        "code": quote.code,
        "capturedAt": quote.captured_at.isoformat(),
        "marketTime": quote.market_time.isoformat(),
        "price": str(quote.price),
        "changePct": str(quote.change_pct),
        "volume": str(quote.volume),
        "amount": str(quote.amount),
        "high": str(quote.high),
        "low": str(quote.low),
        "previousClose": str(quote.previous_close),
        "status": quote.status,
    }


class FakeBridge:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def run(self, command, payload=None, *, timeout=15.0):
        self.calls.append((command, deepcopy(payload), timeout))
        return deepcopy(self.payload)


class FailingBridge:
    def __init__(self, error):
        self.error = error
        self.calls = []

    def run(self, command, payload=None, *, timeout=15.0):
        self.calls.append((command, deepcopy(payload), timeout))
        raise self.error


def test_collect_quote_codes_uses_holdings_and_production_pool_only():
    """Catches research or removed names expanding the broker quote scope."""
    from app.integrations.yitaojin.quotes import collect_quote_codes

    codes = collect_quote_codes(
        {
            "items": {
                "000001": {"code": "000001", "status": "executable"},
                "000002": {"code": "000002", "status": "watching"},
                "000003": {
                    "code": "000003",
                    "status": "research_reference",
                },
                "000004": {"code": "000004", "status": "removed"},
            }
        },
        [_position("600000")],
    )

    assert codes == ("000001", "000002", "600000")


@pytest.mark.parametrize(
    ("age_seconds", "max_age_seconds", "status", "blocked"),
    [
        (90, 90, "fresh", False),
        (91, 90, "stale", True),
        (30, 30, "fresh", False),
        (31, 30, "stale", True),
    ],
)
def test_validate_quote_enforces_regular_and_action_freshness(
    age_seconds,
    max_age_seconds,
    status,
    blocked,
):
    """Catches the 90-second scan threshold being reused at action time."""
    from app.integrations.yitaojin.quotes import validate_quote

    result = validate_quote(
        _quote(age_seconds=age_seconds),
        None,
        now=NOW,
        max_age_seconds=max_age_seconds,
        max_divergence_pct=Decimal("0.5"),
    )

    assert result.status == status
    assert result.blocks_new_entry is blocked


def test_validate_quote_marks_near_time_price_divergence_as_conflict():
    """Catches two current production sources disagreeing by over 0.5%."""
    from app.integrations.yitaojin.quotes import validate_quote

    result = validate_quote(
        _quote(price="10.10"),
        {
            "price": "10.00",
            "quote_timestamp": (NOW - timedelta(seconds=12)).isoformat(),
        },
        now=NOW,
        max_age_seconds=90,
        max_divergence_pct=Decimal("0.5"),
    )

    assert result.status == "conflict"
    assert result.divergence_pct == Decimal("1.00")
    assert result.blocks_new_entry is True
    assert result.requires_manual_price_check is True


@pytest.mark.parametrize("status", ["halted", "suspended", "limit_up", "limit_down"])
def test_halted_or_price_limited_quote_never_authorizes_new_entry(status):
    """Catches an untradeable quote being labeled executable."""
    from app.integrations.yitaojin.quotes import validate_quote

    result = validate_quote(
        _quote(status=status),
        None,
        now=NOW,
        max_age_seconds=90,
        max_divergence_pct=Decimal("0.5"),
    )

    assert result.blocks_new_entry is True
    assert status in result.reasons


def test_missing_quote_is_explicitly_blocking():
    from app.integrations.yitaojin.quotes import validate_quote

    result = validate_quote(
        None,
        None,
        now=NOW,
        max_age_seconds=90,
        max_divergence_pct=Decimal("0.5"),
        code="600000",
    )

    assert result.status == "missing"
    assert result.blocks_new_entry is True


def test_nonpositive_or_incomplete_quote_never_counts_as_fresh():
    """Catches a partially parsed UI row authorizing an entry."""
    from app.integrations.yitaojin.quotes import validate_quote

    zero_price = validate_quote(
        _quote(price="0"),
        None,
        now=NOW,
        max_age_seconds=90,
        max_divergence_pct=Decimal("0.5"),
    )
    incomplete = validate_quote(
        replace(_quote(), amount=None),
        None,
        now=NOW,
        max_age_seconds=90,
        max_divergence_pct=Decimal("0.5"),
    )

    assert zero_price.status == "halted"
    assert zero_price.blocks_new_entry is True
    assert incomplete.status == "missing"
    assert incomplete.blocks_new_entry is True


def test_quote_service_disabled_does_not_call_bridge_and_persists_not_enabled(
    tmp_path,
):
    """Catches disabled mode either opening the App or reusing stale validation."""
    from app.integrations.yitaojin.quotes import YitaojinQuoteService

    bridge = FakeBridge({"quotes": [], "missingCodes": []})
    snapshot_path = tmp_path / "quotes.json"
    service = YitaojinQuoteService(
        bridge=bridge,
        snapshot_path=snapshot_path,
        environment={"CONGXI_YITAOJIN_ENABLED": "false"},
        now_provider=lambda: NOW,
    )

    result = service.refresh(
        pool_payload={
            "items": {
                "600000": {"code": "600000", "status": "executable"},
            }
        },
        positions=(),
    )

    assert result.status == "not_enabled"
    assert bridge.calls == []
    persisted = json.loads(snapshot_path.read_text(encoding="utf-8"))
    assert persisted["status"] == "not_enabled"
    assert persisted["validations"] == {}


def test_quote_service_empty_scope_does_not_open_app(tmp_path):
    """Catches an empty target set needlessly touching the broker client."""
    from app.integrations.yitaojin.quotes import YitaojinQuoteService

    bridge = FakeBridge({"quotes": [], "missingCodes": []})
    result = YitaojinQuoteService(
        bridge=bridge,
        snapshot_path=tmp_path / "quotes.json",
        environment={"CONGXI_YITAOJIN_ENABLED": "true"},
        now_provider=lambda: NOW,
    ).refresh(pool_payload={"items": {}}, positions=())

    assert result.status == "ok"
    assert result.requested_codes == ()
    assert bridge.calls == []


def test_quote_service_validates_every_requested_code_and_writes_atomically(
    tmp_path,
):
    """Catches partial bridge output silently omitting a requested candidate."""
    from app.integrations.yitaojin.models import BridgeCommand
    from app.integrations.yitaojin.quotes import YitaojinQuoteService

    bridge = FakeBridge(
        {
            "quotes": [_bridge_quote_payload("600000")],
            "missingCodes": ["000001"],
        }
    )
    snapshot_path = tmp_path / "nested" / "quotes.json"
    service = YitaojinQuoteService(
        bridge=bridge,
        snapshot_path=snapshot_path,
        environment={"CONGXI_YITAOJIN_ENABLED": "true"},
        now_provider=lambda: NOW,
    )

    result = service.refresh(
        pool_payload={
            "items": {
                "000001": {"code": "000001", "status": "watching"},
                "600000": {"code": "600000", "status": "executable"},
            }
        },
        positions=(),
    )

    assert result.status == "blocked"
    assert result.validations["600000"].status == "fresh"
    assert result.validations["000001"].status == "missing"
    assert bridge.calls[0][0] == BridgeCommand.READ_QUOTES
    assert bridge.calls[0][1] == {"codes": ["000001", "600000"]}
    persisted = json.loads(snapshot_path.read_text(encoding="utf-8"))
    assert persisted["schema_version"] == 1
    assert persisted["requested_codes"] == ["000001", "600000"]
    assert not list(snapshot_path.parent.glob("*.tmp"))


def test_quote_read_failure_persists_unavailable_blocking_summary(tmp_path):
    """Catches App login loss deleting the report instead of closing entries."""
    from app.integrations.yitaojin.models import AppNotLoggedInError
    from app.integrations.yitaojin.quotes import YitaojinQuoteService

    snapshot_path = tmp_path / "quotes.json"
    result = YitaojinQuoteService(
        bridge=FailingBridge(AppNotLoggedInError("not logged in")),
        snapshot_path=snapshot_path,
        environment={"CONGXI_YITAOJIN_ENABLED": "true"},
        now_provider=lambda: NOW,
    ).refresh(
        pool_payload={
            "items": {
                "600000": {"code": "600000", "status": "executable"},
            }
        },
        positions=(),
    )

    assert result.status == "unavailable"
    assert result.validations["600000"].status == "missing"
    persisted = json.loads(snapshot_path.read_text(encoding="utf-8"))
    assert persisted["status"] == "unavailable"
    assert persisted["validations"]["600000"]["blocks_new_entry"] is True


def test_load_quote_summary_revalidates_action_codes_at_30_seconds(tmp_path):
    """Catches a regular-scan snapshot being treated as fresh for a buy action."""
    from app.integrations.yitaojin.quotes import (
        YitaojinQuoteService,
        load_quote_validation_summary,
    )

    snapshot_path = tmp_path / "quotes.json"
    bridge = FakeBridge(
        {
            "quotes": [_bridge_quote_payload(age_seconds=45)],
            "missingCodes": [],
        }
    )
    YitaojinQuoteService(
        bridge=bridge,
        snapshot_path=snapshot_path,
        environment={"CONGXI_YITAOJIN_ENABLED": "true"},
        now_provider=lambda: NOW,
    ).refresh(
        pool_payload={
            "items": {
                "600000": {"code": "600000", "status": "executable"},
            }
        },
        positions=(),
    )

    summary = load_quote_validation_summary(
        snapshot_path,
        environment={"CONGXI_YITAOJIN_ENABLED": "true"},
        now=NOW,
        critical_codes={"600000"},
    )

    assert summary["status"] == "blocked"
    assert summary["validations"]["600000"]["status"] == "stale"
    assert summary["validations"]["600000"]["blocks_new_entry"] is True


def test_daily_report_loader_marks_entry_codes_critical(
    tmp_path,
    monkeypatch,
):
    """Catches the report loading a 45-second quote with the 90-second limit."""
    from app.integrations.yitaojin.quotes import YitaojinQuoteService

    snapshot_path = tmp_path / "quotes.json"
    monkeypatch.setenv("CONGXI_YITAOJIN_ENABLED", "true")
    monkeypatch.setenv(
        "CONGXI_YITAOJIN_QUOTE_SNAPSHOT_PATH",
        str(snapshot_path),
    )
    bridge = FakeBridge(
        {
            "quotes": [_bridge_quote_payload(age_seconds=45)],
            "missingCodes": [],
        }
    )
    YitaojinQuoteService(
        bridge=bridge,
        snapshot_path=snapshot_path,
        environment={"CONGXI_YITAOJIN_ENABLED": "true"},
        now_provider=lambda: NOW,
    ).refresh(
        pool_payload={
            "items": {
                "600000": {"code": "600000", "status": "executable"},
            }
        },
        positions=(),
    )
    from scripts.daily_report import (
        _load_yitaojin_quote_validation_for_decision,
    )

    summary = _load_yitaojin_quote_validation_for_decision(
        {
            "target_scores": [
                {"code": "600000", "action": "buy"},
            ]
        },
        now=NOW,
    )

    assert summary["validations"]["600000"]["status"] == "stale"
