import pytest

from app.services.target_snapshot import build_target_snapshot


class FakeQuoteSource:
    async def fetch_batch(self, codes):
        return {
            "002123": {
                "code": "002123",
                "name": "测试标的",
                "price": 3.2,
                "change_pct": 4.2,
                "amount_wan": 18000,
                "turnover_pct": 8.0,
                "vol_ratio": 2.6,
                "pe_ttm": 18,
                "pb": 2.1,
            }
        }

    async def fetch_kline(self, code, period="day", count=120):
        return {
            "code": code,
            "period": period,
            "bars": [{"date": "2026-07-01", "close": 3.2, "high": 3.2, "low": 3.0}],
            "source": "fake_quote",
        }


class FakeMarketSource:
    async def fetch_hsgt_flow(self):
        return [{"type": "沪股通", "net": "10亿"}]

    async def fetch_fund_flow_individual(self):
        return [{"code": "002123", "name": "测试标的", "net": "净流入", "turnover": "8%"}]


class FakeNewsSource:
    async def fetch_stock_news(self, code, limit=5):
        return [{"title": "测试公告", "content": "订单增长", "source": "fake"}]


async def fake_financial_fetcher(codes):
    return {
        "002123": {
            "status": "success",
            "source": "fake_financial",
            "revenue_yoy_pct": 12.0,
            "gross_margin_pct": 35.0,
        }
    }


@pytest.mark.asyncio
async def test_build_target_snapshot_collects_structured_sources():
    snapshot = await build_target_snapshot(
        "002123",
        name="测试标的",
        quote_source=FakeQuoteSource(),
        market_source=FakeMarketSource(),
        news_source=FakeNewsSource(),
        financial_fetcher=fake_financial_fetcher,
        sentinel={"evidence_ids": ["ev_test"]},
        serenity={"score": 65, "theme": "测试主题"},
    )

    assert snapshot["quote"]["status"] == "ok"
    assert snapshot["kline"]["status"] == "ok"
    assert snapshot["fund_flow"]["status"] == "ok"
    assert snapshot["northbound"]["status"] == "ok"
    assert snapshot["news"]["status"] == "ok"
    assert snapshot["financial"]["status"] == "ok"
    assert snapshot["sentinel"]["evidence_ids"] == ["ev_test"]
    assert snapshot["serenity"]["score"] == 65


@pytest.mark.asyncio
async def test_build_target_snapshot_uses_quote_name_when_pool_name_is_code():
    snapshot = await build_target_snapshot(
        "002123",
        name="002123",
        quote_source=FakeQuoteSource(),
        market_source=FakeMarketSource(),
        news_source=FakeNewsSource(),
        financial_fetcher=fake_financial_fetcher,
    )

    assert snapshot["name"] == "测试标的"
