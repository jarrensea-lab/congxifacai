import pytest


class FakeDiscoverySource:
    def __init__(self, rows):
        self.rows = rows

    async def fetch_fund_flow_individual(self):
        return list(self.rows)


def complete_snapshot(code, name, price):
    return {
        "code": code,
        "name": name,
        "quote": {
            "status": "ok",
            "price": price,
            "change_pct": 3.5,
            "vol_ratio": 2.4,
            "amount_wan": 20_000,
        },
        "kline": {
            "status": "ok",
            "bars": [{"close": price * 0.96}, {"close": price * 0.98}, {"close": price}],
        },
        "fund_flow": {"status": "ok", "net": 20_000_000},
        "financial": {"status": "ok", "revenue_yoy_pct": 12},
        "sentinel": {"status": "ok", "evidence_ids": ["ev-test"]},
        "serenity": {"status": "ok", "score": 70},
    }


@pytest.mark.asyncio
async def test_pipeline_filters_lot_cost_before_enrichment_and_scores_new_names_same_run():
    from app.services.opportunity_pipeline import OpportunityPipeline

    source = FakeDiscoverySource([
        {
            "code": "000001",
            "name": "可买",
            "price": 6.0,
            "amount": 200_000_000,
            "net_amount": 20_000_000,
        },
        {
            "code": "600000",
            "name": "买不起",
            "price": 12.0,
            "amount": 300_000_000,
            "net_amount": 30_000_000,
        },
    ])
    enriched = []

    async def enrich(row):
        enriched.append(row["code"])
        return complete_snapshot(row["code"], row["name"], row["price"])

    def score(snapshot, **_):
        return {
            "code": snapshot["code"],
            "name": snapshot["name"],
            "score": 78,
            "action": "watch",
        }

    result = await OpportunityPipeline(
        discovery_source=source,
        snapshot_builder=enrich,
        scorer=score,
    ).evaluate(
        trade_date="2026-07-31",
        available_cash=800,
        total_assets=1600,
    )

    assert result["metrics"]["scanned_count"] == 2
    assert result["metrics"]["affordable_count"] == 1
    assert result["metrics"]["scored_count"] == 1
    assert enriched == ["000001"]
    assert [row["code"] for row in result["scorecards"]] == ["000001"]
    assert result["rejected"]["budget_blocked"] == 1


@pytest.mark.asyncio
async def test_pipeline_records_zero_market_rows_without_falling_back_to_seed_names():
    from app.services.opportunity_pipeline import OpportunityPipeline

    result = await OpportunityPipeline(
        discovery_source=FakeDiscoverySource([]),
        snapshot_builder=lambda row: row,
        scorer=lambda snapshot, **_: snapshot,
    ).evaluate(
        trade_date="2026-07-31",
        available_cash=800,
        total_assets=1600,
    )

    assert result["metrics"]["scanned_count"] == 0
    assert result["metrics"]["affordable_count"] == 0
    assert result["scorecards"] == []
    assert result["health"]["status"] == "failed"
    assert result["health"]["error_code"] == "empty_market_universe"


@pytest.mark.asyncio
async def test_pipeline_builds_deterministic_promotion_evidence_for_actionable_score():
    from app.services.opportunity_pipeline import OpportunityPipeline

    source = FakeDiscoverySource([
        {
            "code": "000001",
            "name": "可执行",
            "price": 6.0,
            "amount": 200_000_000,
            "net_amount": 20_000_000,
        }
    ])

    async def enrich(row):
        return complete_snapshot(row["code"], row["name"], row["price"])

    def score(snapshot, **_):
        return {
            "code": snapshot["code"],
            "name": snapshot["name"],
            "score": 82,
            "score_version": "composite_score_v1",
            "action": "buy",
            "playbook": "breakout_entry",
            "missing_data": [],
            "block_reason": "",
            "risk_amount": 30,
        }

    result = await OpportunityPipeline(
        discovery_source=source,
        snapshot_builder=enrich,
        scorer=score,
    ).evaluate(
        trade_date="2026-07-31",
        available_cash=800,
        total_assets=1600,
    )

    evidence = result["scorecards"][0]["promotion_evidence"]
    assert evidence["score"] == 82
    assert all(evidence["hard_gates"].values())
    assert evidence["data_cutoff_at"]
