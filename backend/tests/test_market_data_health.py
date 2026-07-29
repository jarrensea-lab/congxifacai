"""Pure market-data health contract tests."""
from datetime import datetime, timedelta, timezone

import pytest

from app.services.market_data_health import aggregate_market_quote_truth


EXPECTED_CODES = ("sh000001", "sz399001", "sz399006")
NOW = datetime(2026, 7, 26, 9, 40, tzinfo=timezone(timedelta(hours=8)))


def _aggregate(quotes):
    return aggregate_market_quote_truth(
        quotes,
        EXPECTED_CODES,
        default_provider="tencent",
        now=NOW,
    )


def _fresh_quote(
    *,
    price=4000,
    quote_timestamp=None,
):
    return {
        "price": price,
        "source": "tencent",
        "quote_timestamp": quote_timestamp or NOW.isoformat(),
        "freshness": "fresh",
    }


def test_aggregate_degrades_when_provider_omits_one_expected_index():
    result = _aggregate(
        {
            "sh000001": _fresh_quote(),
            "sz399001": _fresh_quote(),
        }
    )

    assert list(result["quotes"]) == ["sh000001", "sz399001"]
    assert result["market_source_status"]["status"] == "degraded"
    assert result["market_source_status"]["coverage"] == {
        "expected": 3,
        "verified": 2,
    }
    assert result["market_source_status"]["missing_sources"] == ["sz399006"]


@pytest.mark.parametrize("invalid_price", [float("nan"), float("inf"), float("-inf")])
def test_aggregate_rejects_nonfinite_prices(invalid_price):
    result = _aggregate(
        {
            "sh000001": _fresh_quote(),
            "sz399001": _fresh_quote(price=invalid_price),
            "sz399006": _fresh_quote(),
        }
    )

    assert list(result["quotes"]) == ["sh000001", "sz399006"]
    assert result["market_source_status"]["status"] == "degraded"
    assert result["market_source_status"]["rejected_sources"] == ["sz399001"]


def test_aggregate_selects_earliest_cutoff_by_absolute_time():
    result = _aggregate(
        {
            "sh000001": _fresh_quote(
                quote_timestamp="2026-07-26T09:30:00+08:00"
            ),
            "sz399001": _fresh_quote(
                quote_timestamp="2026-07-26T01:35:00+00:00"
            ),
            "sz399006": _fresh_quote(
                quote_timestamp="2026-07-26T09:38:00+08:00"
            ),
        }
    )

    assert result["market_source_status"]["status"] == "ok"
    assert (
        result["market_source_status"]["data_cutoff"]
        == "2026-07-26T09:30:00+08:00"
    )


def test_aggregate_rejects_naive_timestamp_as_unreliable():
    result = _aggregate(
        {
            "sh000001": _fresh_quote(),
            "sz399001": _fresh_quote(
                quote_timestamp=NOW.replace(tzinfo=None).isoformat()
            ),
            "sz399006": _fresh_quote(),
        }
    )

    assert list(result["quotes"]) == ["sh000001", "sz399006"]
    assert result["market_source_status"]["status"] == "degraded"
    assert result["market_source_status"]["rejected_sources"] == ["sz399001"]
