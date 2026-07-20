import pytest
import sys
from types import SimpleNamespace

from app.data_sources import akshare_market
from app.data_sources.akshare_market import AKShareMarketClient


@pytest.mark.asyncio
async def test_akshare_market_call_timeout_covers_full_fund_flow_scan(monkeypatch):
    captured = {}

    async def fake_wait_for(awaitable, timeout):
        captured["timeout"] = timeout
        return await awaitable

    monkeypatch.setattr(akshare_market.asyncio, "wait_for", fake_wait_for)

    result = await AKShareMarketClient()._call_async(lambda: "ok")

    assert result == "ok"
    assert captured["timeout"] >= 90


@pytest.mark.asyncio
async def test_fetch_fund_flow_individual_normalizes_unpadded_stock_codes(monkeypatch):
    pd = pytest.importorskip("pandas")

    def fake_stock_fund_flow_individual(period):
        assert period == "即时"
        return pd.DataFrame([
            {
                "股票代码": 725,
                "股票简称": "京东方A",
                "最新价": "6.38",
                "流入资金": "125.65亿",
                "流出资金": "177.15亿",
                "净额": "-51.50亿",
                "成交额": "302.80亿",
                "涨跌幅": "-6.87%",
                "换手率": "10.76%",
            }
        ])

    monkeypatch.setitem(
        sys.modules,
        "akshare",
        SimpleNamespace(stock_fund_flow_individual=fake_stock_fund_flow_individual),
    )

    rows = await AKShareMarketClient().fetch_fund_flow_individual()

    assert rows[0]["code"] == "000725"
    assert rows[0]["latest_price"] == "6.38"
    assert rows[0]["amount"] == "302.80亿"


@pytest.mark.asyncio
async def test_fetch_fund_flow_individual_reuses_one_client_snapshot(monkeypatch):
    pd = pytest.importorskip("pandas")
    calls = 0

    def fake_stock_fund_flow_individual(period):
        nonlocal calls
        calls += 1
        return pd.DataFrame([
            {
                "股票代码": "000563",
                "股票简称": "陕国投A",
                "最新价": "3.02",
                "流入资金": "2亿",
                "流出资金": "1亿",
                "净额": "1亿",
                "成交额": "12亿",
                "涨跌幅": "5.96%",
                "换手率": "4.37%",
            }
        ])

    monkeypatch.setitem(
        sys.modules,
        "akshare",
        SimpleNamespace(stock_fund_flow_individual=fake_stock_fund_flow_individual),
    )
    client = AKShareMarketClient()

    first = await client.fetch_fund_flow_individual()
    second = await client.fetch_fund_flow_individual()

    assert first == second
    assert calls == 1


@pytest.mark.asyncio
async def test_fetch_fund_flow_individual_refreshes_after_cache_ttl(monkeypatch):
    pd = pytest.importorskip("pandas")
    calls = 0
    clock = {"now": 100.0}

    def fake_stock_fund_flow_individual(period):
        nonlocal calls
        calls += 1
        return pd.DataFrame([
            {
                "股票代码": "002131",
                "股票简称": "利欧股份",
                "最新价": str(4.0 + calls / 10),
            }
        ])

    monkeypatch.setitem(
        sys.modules,
        "akshare",
        SimpleNamespace(stock_fund_flow_individual=fake_stock_fund_flow_individual),
    )
    monkeypatch.setattr(akshare_market.time, "monotonic", lambda: clock["now"])
    client = AKShareMarketClient()

    first = await client.fetch_fund_flow_individual()
    clock["now"] += akshare_market.AKSHARE_CACHE_TTL_SECONDS + 1
    second = await client.fetch_fund_flow_individual()

    assert calls == 2
    assert first[0]["latest_price"] != second[0]["latest_price"]


@pytest.mark.asyncio
async def test_fetch_hsgt_flow_reuses_one_client_snapshot(monkeypatch):
    pd = pytest.importorskip("pandas")
    calls = 0

    def fake_stock_hsgt_fund_flow_summary_em():
        nonlocal calls
        calls += 1
        return pd.DataFrame([
            {"交易日": "2026-07-15", "类型": "沪股通", "板块": "沪市", "资金方向": "北向", "成交净买额": "1亿", "当日资金余额": "10亿"}
        ])

    monkeypatch.setitem(
        sys.modules,
        "akshare",
        SimpleNamespace(stock_hsgt_fund_flow_summary_em=fake_stock_hsgt_fund_flow_summary_em),
    )
    client = AKShareMarketClient()

    first = await client.fetch_hsgt_flow()
    second = await client.fetch_hsgt_flow()

    assert first == second
    assert calls == 1
