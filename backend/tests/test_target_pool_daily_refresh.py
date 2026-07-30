import pytest


@pytest.mark.asyncio
async def test_build_target_scores_evicts_broken_mid_long_pool_item(monkeypatch):
    from scripts.daily_report import build_target_scores_for_report

    writes = []
    thesis = {
        "symbol": "600000",
        "name": "浦发银行",
        "thesis_status": "broken",
        "quality_score": 80,
    }

    class FakeStore:
        def load(self):
            return {
                "items": {
                    "600000": {
                        "code": "600000",
                        "name": "浦发银行",
                        "status": "research_reference",
                        "pool_kind": "mid_long_term",
                        "scoring_decision": {"pool_retained": True},
                    }
                }
            }

        def upsert_target(self, **kwargs):
            writes.append(kwargs)
            return True

    class FakeLongStore:
        def load_strict(self):
            return {"items": {"600000": thesis}}

    class FakeSource:
        async def fetch_fund_flow_individual(self):
            return []

        async def fetch_hsgt_flow(self):
            return []

    async def fake_snapshot(code, **kwargs):
        return {"code": code, "name": kwargs["name"], "quote": {"price": 8.0}}

    def fake_score(snapshot, **kwargs):
        assert kwargs["long_thesis"] == thesis
        return {
            "code": snapshot["code"],
            "name": snapshot["name"],
            "score": 45,
            "action": "watch",
            "long_quality_score": 0,
            "thesis_status": "broken",
            "missing_data": [],
        }

    monkeypatch.setattr("app.services.quant_lifecycle.TargetPoolStore", FakeStore)
    monkeypatch.setattr("app.data_sources.akshare_market.AKShareMarketClient", FakeSource)
    monkeypatch.setattr("app.data_sources.akshare_news.AKShareNewsClient", FakeSource)
    monkeypatch.setattr(
        "app.data_sources.realtime_market_data.FastRealtimeMarketDataSource",
        FakeSource,
    )
    monkeypatch.setattr(
        "app.services.target_snapshot.build_target_snapshot",
        fake_snapshot,
    )
    monkeypatch.setattr("app.services.target_scoring.score_target", fake_score)

    scores = await build_target_scores_for_report(
        available_cash=6085.61,
        total_assets=6085.61,
        limit=1,
        long_thesis_store=FakeLongStore(),
    )

    assert scores[0]["pool_kind"] == "mid_long_term"
    assert scores[0]["pool_retained"] is False
    assert writes[0]["status"] == "thesis_review"
    assert writes[0]["pool_kind"] == "mid_long_term"
    assert writes[0]["scoring_decision"]["pool_retained"] is False


@pytest.mark.asyncio
async def test_daily_refresh_scores_every_existing_pool_member_before_discovery_limit(
    monkeypatch,
):
    from scripts.daily_report import build_target_scores_for_report

    selected_codes = []

    class FakeStore:
        def load(self):
            return {
                "items": {
                    "000001": {
                        "code": "000001",
                        "name": "短线成员",
                        "status": "watching",
                        "pool_kind": "short_term",
                        "scoring_decision": {"pool_retained": True},
                    },
                    "600000": {
                        "code": "600000",
                        "name": "长线成员",
                        "status": "research_reference",
                        "pool_kind": "mid_long_term",
                        "scoring_decision": {"pool_retained": True},
                    },
                    "000002": {
                        "code": "000002",
                        "name": "新研究线索",
                        "status": "research_reference",
                        "updated_at": "2026-07-29 20:00:00",
                    },
                }
            }

        def upsert_target(self, **kwargs):
            return True

    class FakeLongStore:
        def load_strict(self):
            return {
                "items": {
                    "600000": {
                        "symbol": "600000",
                        "thesis_status": "healthy",
                        "quality_score": 62,
                    }
                }
            }

    class FakeSource:
        async def fetch_fund_flow_individual(self):
            return []

        async def fetch_hsgt_flow(self):
            return []

    async def fake_snapshot(code, **kwargs):
        selected_codes.append(code)
        return {"code": code, "name": kwargs["name"], "quote": {"price": 8.0}}

    def fake_score(snapshot, **kwargs):
        is_long = kwargs.get("long_thesis") is not None
        return {
            "code": snapshot["code"],
            "name": snapshot["name"],
            "score": 52,
            "action": "watch",
            "long_quality_score": 62 if is_long else 0,
            "thesis_status": "healthy" if is_long else "",
            "missing_data": [],
        }

    monkeypatch.setattr("app.services.quant_lifecycle.TargetPoolStore", FakeStore)
    monkeypatch.setattr("app.data_sources.akshare_market.AKShareMarketClient", FakeSource)
    monkeypatch.setattr("app.data_sources.akshare_news.AKShareNewsClient", FakeSource)
    monkeypatch.setattr(
        "app.data_sources.realtime_market_data.FastRealtimeMarketDataSource",
        FakeSource,
    )
    monkeypatch.setattr(
        "app.services.target_snapshot.build_target_snapshot",
        fake_snapshot,
    )
    monkeypatch.setattr("app.services.target_scoring.score_target", fake_score)

    await build_target_scores_for_report(
        available_cash=6085.61,
        total_assets=6085.61,
        limit=1,
        long_thesis_store=FakeLongStore(),
    )

    assert selected_codes == ["000001", "600000"]
