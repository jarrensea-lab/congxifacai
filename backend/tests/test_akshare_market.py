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
                "流入资金": "125.65亿",
                "流出资金": "177.15亿",
                "净额": "-51.50亿",
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
