"""Unified A-share code parsing and vendor adapter routing tests."""
from __future__ import annotations

import pytest

from app.utils.a_share_codes import (
    a_share_exchange,
    normalize_a_share_code,
    tencent_symbol,
    ts_code,
    validate_a_share_code,
)


@pytest.mark.parametrize(
    ("raw", "normalized", "exchange", "tencent", "tushare"),
    [
        ("000001", "000001", "SZ", "sz000001", "000001.SZ"),
        ("sz300001", "300001", "SZ", "sz300001", "300001.SZ"),
        ("688001.SH", "688001", "SH", "sh688001", "688001.SH"),
        ("SH600000", "600000", "SH", "sh600000", "600000.SH"),
        ("430001", "430001", "BJ", "bj430001", "430001.BJ"),
        ("830001.BJ", "830001", "BJ", "bj830001", "830001.BJ"),
        ("bj920001", "920001", "BJ", "bj920001", "920001.BJ"),
    ],
)
def test_a_share_code_normalization_and_vendor_symbols(
    raw,
    normalized,
    exchange,
    tencent,
    tushare,
):
    assert normalize_a_share_code(raw) == normalized
    assert validate_a_share_code(raw) is True
    assert a_share_exchange(raw) == exchange
    assert tencent_symbol(raw) == tencent
    assert ts_code(raw) == tushare


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "399001",
        "sh000001",
        "000300.SH",
        "900901",
        "200002",
        "899050",
        "990001",
        "92001",
        "not-a-code",
    ],
)
def test_a_share_code_rejects_indices_b_shares_and_invalid_values(raw):
    assert validate_a_share_code(raw) is False
    with pytest.raises(ValueError):
        normalize_a_share_code(raw)


def test_all_vendor_adapters_route_current_bse_920_consistently():
    from app.ai.serenity_financial_evidence import _to_ts_code
    from app.data_sources.realtime_kline_scraper import _market_prefix
    from app.data_sources.tencent_client import TencentDataSource
    from app.data_sources.tushare_client import TushareDataSource
    from app.services.market_state_store import normalize_ts_code

    assert TencentDataSource()._resolve_code("920001") == "bj920001"
    assert TushareDataSource.__new__(TushareDataSource)._to_ts_code(
        "920001"
    ) == "920001.BJ"
    assert _to_ts_code("920001") == "920001.BJ"
    assert normalize_ts_code("920001") == "920001.BJ"
    assert _market_prefix("920001") == "0"
