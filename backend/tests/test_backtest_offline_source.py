import pytest

from app.engine import backtest
from app.data_sources import tencent_client, tushare_client


@pytest.mark.asyncio
async def test_backtest_prefers_registered_offline_history(monkeypatch):
    bars = [
        {
            "date": f"2025-01-{index + 1:02d}",
            "open": 10.0 + index / 10,
            "close": 10.0 + index / 10,
            "high": 10.2 + index / 10,
            "low": 9.8 + index / 10,
            "volume": 1000,
            "amount": 10000,
        }
        for index in range(30)
    ]

    class FakeOfflineSource:
        async def fetch_kline(self, stock_code, period, count, **kwargs):
            assert stock_code == "000725"
            assert period == "day"
            assert count == 30
            assert kwargs["adjustment"] == "qfq"
            return {
                "status": "ok",
                "bars": bars,
                "data_cutoff": "2025-01-30 15:00:00",
            }

    class FakeOfflineFactory:
        @classmethod
        def from_default_registry(cls):
            return FakeOfflineSource()

    monkeypatch.setattr(
        backtest,
        "OfflineMinuteDataSource",
        FakeOfflineFactory,
    )

    result = await backtest._fetch_kline("000725", 30)

    assert result == bars


@pytest.mark.asyncio
async def test_backtest_does_not_hide_malformed_offline_success_with_provider_fallback(
    monkeypatch,
):
    provider_calls = []

    class FakeOfflineSource:
        async def fetch_kline(self, *_args, **_kwargs):
            return {"status": "ok", "bars": []}

    class FakeOfflineFactory:
        @classmethod
        def from_default_registry(cls):
            return FakeOfflineSource()

    class FakeTushare:
        def is_available(self):
            return True

        async def fetch_kline(self, *_args, **_kwargs):
            provider_calls.append("tushare")
            return {"status": "ok", "bars": [{"close": 1}] * 30}

    class FakeTencent:
        async def fetch_kline(self, *_args, **_kwargs):
            provider_calls.append("tencent")
            return {"status": "ok", "bars": [{"close": 1}] * 30}

    monkeypatch.setattr(backtest, "OfflineMinuteDataSource", FakeOfflineFactory)
    monkeypatch.setattr(tushare_client, "TushareDataSource", FakeTushare)
    monkeypatch.setattr(tencent_client, "TencentDataSource", FakeTencent)

    result = await backtest._fetch_kline("000725", 30)

    assert result == []
    assert provider_calls == []


@pytest.mark.asyncio
async def test_backtest_falls_back_after_explicit_offline_error(monkeypatch):
    provider_bars = [{"date": str(index), "close": 10.0} for index in range(30)]

    class FakeOfflineSource:
        async def fetch_kline(self, *_args, **_kwargs):
            return {
                "status": "error",
                "reason": "source_unavailable",
                "bars": [],
            }

    class FakeOfflineFactory:
        @classmethod
        def from_default_registry(cls):
            return FakeOfflineSource()

    class FakeTushare:
        def is_available(self):
            return True

        async def fetch_kline(self, *_args, **_kwargs):
            return {"status": "ok", "bars": provider_bars}

    monkeypatch.setattr(backtest, "OfflineMinuteDataSource", FakeOfflineFactory)
    monkeypatch.setattr(tushare_client, "TushareDataSource", FakeTushare)

    result = await backtest._fetch_kline("000725", 30)

    assert result == provider_bars


@pytest.mark.asyncio
async def test_backtest_rejects_nonempty_malformed_offline_success(monkeypatch):
    provider_calls = []
    bars = [
        {
            "date": f"2025-01-{index + 1:02d}",
            "open": 10.0,
            "close": 10.1,
            "high": 10.2,
            "low": 9.9,
            "volume": 1000,
            "amount": 10000,
        }
        for index in range(30)
    ]
    bars[-1]["close"] = float("nan")

    class FakeOfflineSource:
        async def fetch_kline(self, *_args, **_kwargs):
            return {
                "status": "ok",
                "bars": bars,
                "data_cutoff": "2025-01-30 15:00:00",
            }

    class FakeOfflineFactory:
        @classmethod
        def from_default_registry(cls):
            return FakeOfflineSource()

    class FakeTushare:
        def is_available(self):
            return True

        async def fetch_kline(self, *_args, **_kwargs):
            provider_calls.append("tushare")
            return {"status": "ok", "bars": [{"close": 1}] * 30}

    monkeypatch.setattr(backtest, "OfflineMinuteDataSource", FakeOfflineFactory)
    monkeypatch.setattr(tushare_client, "TushareDataSource", FakeTushare)

    result = await backtest._fetch_kline("000725", 30)

    assert result == []
    assert provider_calls == []


@pytest.mark.asyncio
async def test_backtest_fails_closed_when_offline_payload_is_not_an_object(
    monkeypatch,
):
    class FakeOfflineSource:
        async def fetch_kline(self, *_args, **_kwargs):
            return None

    class FakeOfflineFactory:
        @classmethod
        def from_default_registry(cls):
            return FakeOfflineSource()

    monkeypatch.setattr(backtest, "OfflineMinuteDataSource", FakeOfflineFactory)

    result = await backtest._fetch_kline("000725", 30)

    assert result == []
