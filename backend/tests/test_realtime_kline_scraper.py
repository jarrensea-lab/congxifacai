import pytest

from app.data_sources.realtime_kline_scraper import ScraplingRealtimeKlineSource
from app.data_sources.realtime_market_data import FastRealtimeMarketDataSource


class FakeScraplingResponse:
    status = 200

    def json(self):
        return {
            "rc": 0,
            "data": {
                "klines": [
                    "2026-07-09 14:59,8.14,8.15,8.16,8.13,1000,8145000.00,0.37,0.12,0.01,0.01",
                    "2026-07-09 15:00,8.15,8.15,8.15,8.15,1200,9780000.00,0.00,0.00,0.00,0.02",
                ]
            },
        }


@pytest.mark.asyncio
async def test_scrapling_realtime_kline_parses_eastmoney_rows(monkeypatch):
    from scrapling.fetchers import Fetcher

    captured = {}

    def fake_get(url, **kwargs):
        captured["url"] = url
        captured["kwargs"] = kwargs
        return FakeScraplingResponse()

    monkeypatch.setattr(Fetcher, "get", fake_get)

    result = await ScraplingRealtimeKlineSource().fetch_kline("000725", period="1", count=2)

    assert result["source"] == "eastmoney_scrapling_kline"
    assert result["vendor"] == "eastmoney_push2his"
    assert result["bars"][-1]["date"] == "2026-07-09 15:00"
    assert result["bars"][-1]["close"] == 8.15
    assert "push2his.eastmoney.com" in captured["url"]
    assert "klt=1" in captured["url"]


@pytest.mark.asyncio
async def test_fast_realtime_market_data_falls_back_when_scrapling_has_no_bars():
    class EmptyKline:
        async def fetch_kline(self, code, period="day", count=120):
            return {"code": code, "period": period, "bars": [], "source": "empty"}

    class FallbackKline:
        async def fetch_kline(self, code, period="day", count=120):
            return {"code": code, "period": period, "bars": [{"date": "2026-07-09", "close": 1.0}], "source": "fallback"}

    source = FastRealtimeMarketDataSource(
        quote_source=None,
        kline_source=EmptyKline(),
        fallback_kline_source=FallbackKline(),
    )

    result = await source.fetch_kline("000725", period="day", count=1)

    assert result["bars"]
    assert result["source"] == "fallback+fallback_after_scrapling"
