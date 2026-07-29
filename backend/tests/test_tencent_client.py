from datetime import datetime, timedelta, timezone

import pytest

from app.data_sources.tencent_client import TencentDataSource


CHINA_TZ = timezone(timedelta(hours=8))


def _quote_line(timestamp: str) -> str:
    values = [""] * 53
    values[1] = "京东方A"
    values[3] = "7.59"
    values[4] = "7.70"
    values[5] = "7.68"
    values[30] = timestamp
    return f'v_sz000725="{"~".join(values)}"'


def _instrument_quote_line(symbol: str, name: str) -> str:
    values = [""] * 53
    values[1] = name
    values[3] = "3210.5"
    values[4] = "3200.0"
    values[5] = "3201.0"
    values[30] = "20260726145930"
    return f'v_{symbol}="{"~".join(values)}"'


def test_tencent_quote_parser_exposes_source_timestamp_and_trading_date():
    quote = TencentDataSource()._parse_one(_quote_line("20260715145930"), "000725")

    assert quote is not None
    assert quote["source"] == "tencent"
    assert quote["quote_timestamp"] == "2026-07-15T14:59:30+08:00"
    assert quote["trading_date"] == "2026-07-15"
    assert quote["captured_at"]


def test_tencent_quote_parser_marks_unparseable_source_time_unknown():
    quote = TencentDataSource()._parse_one(_quote_line("not-a-timestamp"), "000725")

    assert quote is not None
    assert quote["source"] == "tencent"
    assert quote["quote_timestamp"] is None
    assert quote["trading_date"] is None
    assert quote["freshness"] == "unknown"
    assert quote["captured_at"]


def test_tencent_parser_marks_latest_completed_session_as_valid_close():
    quote = TencentDataSource()._parse_one(
        _quote_line("20260717150000"),
        "000725",
        captured_at=datetime(2026, 7, 19, 20, 30, tzinfo=CHINA_TZ),
    )

    assert quote is not None
    assert quote["trading_date"] == "2026-07-17"
    assert quote["freshness"] == "valid_close"


def test_tencent_parser_marks_non_latest_completed_close_stale():
    quote = TencentDataSource()._parse_one(
        _quote_line("20260717150000"),
        "000725",
        captured_at=datetime(2026, 7, 20, 20, 30, tzinfo=CHINA_TZ),
    )

    assert quote is not None
    assert quote["freshness"] == "stale"


@pytest.mark.parametrize(
    "index_symbol",
    ["sh000001", "sz399001", "sz399006"],
)
def test_tencent_resolver_allows_only_known_market_indices(index_symbol):
    assert TencentDataSource()._resolve_code(index_symbol) == index_symbol


@pytest.mark.asyncio
async def test_tencent_fetch_batch_preserves_three_market_indices(
    monkeypatch,
):
    import app.data_sources.tencent_client as tencent_client

    requested_urls = []
    body = ";".join([
        _instrument_quote_line("sh000001", "上证指数"),
        _instrument_quote_line("sz399001", "深证成指"),
        _instrument_quote_line("sz399006", "创业板指"),
    ])

    class FakeResponse:
        content = body.encode("gbk")

        @staticmethod
        def raise_for_status():
            return None

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url, **kwargs):
            requested_urls.append(url)
            return FakeResponse()

    monkeypatch.setattr(tencent_client.httpx, "AsyncClient", FakeClient)
    symbols = ["sh000001", "sz399001", "sz399006"]

    quotes = await TencentDataSource().fetch_batch(symbols)

    assert list(quotes) == symbols
    assert requested_urls == [
        "https://qt.gtimg.cn/q=sh000001,sz399001,sz399006"
    ]
