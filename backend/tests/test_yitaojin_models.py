from __future__ import annotations

from copy import deepcopy
from decimal import Decimal

import pytest


def _valid_account_payload() -> dict:
    return {
        "capturedAt": "2026-07-26T09:30:05+08:00",
        "accountFingerprint": "sha256:" + "a" * 64,
        "totalAssets": "6051.25",
        "availableCash": "1383.25",
        "frozenCash": "0.00",
        "emptyPositionsConfirmed": False,
        "positions": [
            {
                "code": "000001",
                "name": "测试股份",
                "shares": 200,
                "availableShares": 100,
                "averageCost": "10.125",
                "currentPrice": "10.50",
                "marketValue": "2100.00",
                "unrealizedPnl": "75.00",
            }
        ],
    }


def test_account_snapshot_parses_decimal_contract_without_sensitive_identity():
    """Catches lossy float parsing or persisted raw account identifiers."""
    from app.integrations.yitaojin.models import AccountSnapshot

    snapshot = AccountSnapshot.from_bridge_payload(_valid_account_payload())
    persisted = snapshot.to_persisted_dict()

    assert snapshot.total_assets == Decimal("6051.25")
    assert snapshot.positions[0].average_cost == Decimal("10.125")
    assert snapshot.positions[0].available_shares == 100
    assert snapshot.captured_at.utcoffset().total_seconds() == 8 * 60 * 60
    assert persisted["account_fingerprint"] == "sha256:" + "a" * 64
    assert "account_number" not in persisted
    assert "raw_account" not in persisted


@pytest.mark.parametrize("invalid_value", ["", "--", "NaN", "Infinity", None, True])
def test_account_snapshot_rejects_non_finite_or_missing_money(invalid_value):
    """Catches malformed UI values being silently converted to zero."""
    from app.integrations.yitaojin.models import (
        AccountSnapshot,
        SnapshotValidationError,
    )

    payload = _valid_account_payload()
    payload["totalAssets"] = invalid_value

    with pytest.raises(SnapshotValidationError, match="totalAssets"):
        AccountSnapshot.from_bridge_payload(payload)


@pytest.mark.parametrize("invalid_code", ["1", "00001A", "0000001", "", None])
def test_account_snapshot_rejects_noncanonical_stock_codes(invalid_code):
    """Catches ambiguous codes entering holdings truth."""
    from app.integrations.yitaojin.models import (
        AccountSnapshot,
        SnapshotValidationError,
    )

    payload = _valid_account_payload()
    payload["positions"][0]["code"] = invalid_code

    with pytest.raises(SnapshotValidationError, match="code"):
        AccountSnapshot.from_bridge_payload(payload)


def test_account_snapshot_rejects_duplicate_position_codes():
    """Catches double counting when the UI parser emits the same row twice."""
    from app.integrations.yitaojin.models import (
        AccountSnapshot,
        SnapshotValidationError,
    )

    payload = _valid_account_payload()
    payload["positions"].append(deepcopy(payload["positions"][0]))

    with pytest.raises(SnapshotValidationError, match="duplicate"):
        AccountSnapshot.from_bridge_payload(payload)


def test_account_snapshot_rejects_raw_sensitive_account_fields():
    """Catches accidental raw account data crossing the bridge boundary."""
    from app.integrations.yitaojin.models import (
        AccountSnapshot,
        SnapshotValidationError,
    )

    payload = _valid_account_payload()
    payload["accountNumber"] = "sensitive-value"

    with pytest.raises(SnapshotValidationError, match="sensitive"):
        AccountSnapshot.from_bridge_payload(payload)


def test_quote_snapshot_parses_market_timestamp_and_price():
    """Catches quote freshness checks using a capture time as market time."""
    from app.integrations.yitaojin.models import QuoteSnapshot

    quote = QuoteSnapshot.from_bridge_payload(
        {
            "code": "600000",
            "capturedAt": "2026-07-26T09:30:07+08:00",
            "marketTime": "2026-07-26T09:30:03+08:00",
            "price": "10.21",
            "changePct": "1.25",
            "volume": "123400",
            "amount": "1250000.50",
            "high": "10.25",
            "low": "10.02",
            "previousClose": "10.08",
            "status": "normal",
        }
    )

    assert quote.price == Decimal("10.21")
    assert quote.market_time.second == 3
    assert quote.captured_at.second == 7
    assert quote.previous_close == Decimal("10.08")
