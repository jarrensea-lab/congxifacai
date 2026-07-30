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


def test_account_budget_snapshot_uses_active_strategy_profile(monkeypatch):
    from app.services import small_account_discovery

    monkeypatch.setattr(
        small_account_discovery,
        "get_strategy_profile",
        lambda: {
            "cash_reserve_pct": 20,
            "single_position_limit_pct": 30,
        },
    )

    account = small_account_discovery.build_account_budget_snapshot(
        available_cash=1000,
        total_assets=2000,
    )

    assert account["reserve_cash"] == 400
    assert account["executable_budget"] == 600


@pytest.mark.asyncio
async def test_service_date_assets_include_cash_when_total_assets_is_absent(
    tmp_path,
    monkeypatch,
):
    import json
    from app.services.opportunity_pipeline import OpportunityPipeline

    portfolio = tmp_path / "portfolio.json"
    portfolio.write_text(
        json.dumps({
            "available_cash": 800,
            "total_value": 300,
            "positions": [],
        }),
        encoding="utf-8",
    )
    candidate_pool = tmp_path / "candidate_pool.json"
    candidate_pool.write_text(
        json.dumps({
            "version": 1,
            "items": {
                "000001": {"status": "watching"},
                "000002": {"status": "removed"},
            },
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("CONGXI_PORTFOLIO_PATH", str(portfolio))
    monkeypatch.setenv("CONGXI_CANDIDATE_POOL_PATH", str(candidate_pool))
    observed = {}
    pipeline = OpportunityPipeline(
        discovery_source=FakeDiscoverySource([]),
        snapshot_builder=lambda row: row,
    )

    async def capture_run(trade_date, **kwargs):
        observed.update(kwargs)
        return {"trade_date": trade_date}

    monkeypatch.setattr(pipeline, "run", capture_run)

    await pipeline.run_for_service_date()

    assert observed["total_assets"] == 1100
    assert observed["priority_codes"] == {"000001"}
    assert observed["existing_codes"] == {"000002"}


@pytest.mark.asyncio
async def test_enrichment_concurrency_is_bounded_and_ordered():
    import asyncio
    from app.services.opportunity_pipeline import OpportunityPipeline

    active = 0
    peak = 0

    async def enrich(row):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0)
        active -= 1
        return complete_snapshot(row["code"], row["name"], row["price"])

    rows = [
        {
            "code": f"00000{index}",
            "name": f"标的{index}",
            "price": 3,
            "amount": 200_000_000,
            "net_amount": 20_000_000 - index,
        }
        for index in range(1, 6)
    ]
    result = await OpportunityPipeline(
        discovery_source=FakeDiscoverySource(rows),
        snapshot_builder=enrich,
        scorer=lambda snapshot, **_: {
            "code": snapshot["code"],
            "name": snapshot["name"],
            "score": 70,
            "action": "watch",
        },
        max_enrichment_concurrency=2,
    ).evaluate(
        trade_date="2026-07-31",
        available_cash=2000,
        total_assets=4000,
    )

    assert peak == 2
    assert [item["code"] for item in result["scorecards"]] == [
        f"00000{index}" for index in range(1, 6)
    ]


@pytest.mark.asyncio
async def test_priority_target_is_rescored_in_same_run_even_outside_top_rank():
    from app.services.opportunity_pipeline import OpportunityPipeline

    source = FakeDiscoverySource([
        {
            "code": "000001",
            "name": "高排名",
            "price": 3,
            "amount": 200_000_000,
            "net_amount": 20_000_000,
        },
        {
            "code": "000002",
            "name": "旧标的",
            "price": 3,
            "amount": 1,
            "net_amount": 1,
        },
    ])

    result = await OpportunityPipeline(
        discovery_source=source,
        snapshot_builder=lambda row: complete_snapshot(
            row["code"], row["name"], row["price"]
        ),
        scorer=lambda snapshot, **_: {
            "code": snapshot["code"],
            "name": snapshot["name"],
            "score": 70,
            "action": "watch",
        },
        max_candidates=1,
    ).evaluate(
        trade_date="2026-07-31",
        available_cash=1000,
        total_assets=1000,
        priority_codes={"000002"},
    )

    assert [item["code"] for item in result["scorecards"]] == [
        "000001",
        "000002",
    ]
    assert result["metrics"]["scored_count"] == 2


def test_lifecycle_ignores_new_c_grade_then_downgrades_and_removes_old_target(
    tmp_path,
):
    from app.services.opportunity_pipeline import apply_scorecard_lifecycle
    from app.services.quant_lifecycle import TargetPoolStore

    store = TargetPoolStore(tmp_path / "candidate_pool.json")
    store.save({
        "version": 1,
        "items": {
            "000001": {
                "code": "000001",
                "name": "旧标的",
                "status": "executable",
                "source": "dynamic_market_discovery",
                "scoring_decision": {"score": 80},
            }
        },
    })

    ignored = apply_scorecard_lifecycle(
        store,
        [{"code": "000002", "name": "新低分", "score": 50, "action": "watch"}],
        available_cash=1000,
        total_assets=1000,
    )
    first = apply_scorecard_lifecycle(
        store,
        [{
            "code": "000001",
            "name": "旧标的",
            "score": 60,
            "action": "watch",
            "lifecycle_only": True,
            "discovery_source": "target_pool_daily_refresh",
        }],
        available_cash=1000,
        total_assets=1000,
    )
    second = apply_scorecard_lifecycle(
        store,
        [{
            "code": "000001",
            "name": "旧标的",
            "score": 60,
            "action": "watch",
            "lifecycle_only": True,
            "discovery_source": "target_pool_daily_refresh",
        }],
        available_cash=1000,
        total_assets=1000,
    )

    assert ignored == []
    assert store.load()["items"].get("000002") is None
    assert first[0]["event"] == "downgraded"
    assert first[0]["new_status"] == "watching"
    assert second[0]["event"] == "removed"
    assert store.load()["items"]["000001"]["status"] == "removed"
    assert store.load()["items"]["000001"]["source"] == "dynamic_market_discovery"


def test_long_thesis_break_removes_existing_target_immediately(tmp_path):
    from app.services.opportunity_pipeline import apply_scorecard_lifecycle
    from app.services.quant_lifecycle import TargetPoolStore

    store = TargetPoolStore(tmp_path / "candidate_pool.json")
    store.save({
        "version": 1,
        "items": {
            "000001": {
                "code": "000001",
                "name": "旧标的",
                "status": "watching",
                "source": "dynamic_market_discovery",
                "scoring_decision": {"score": 75},
            }
        },
    })

    events = apply_scorecard_lifecycle(
        store,
        [{
            "code": "000001",
            "name": "旧标的",
            "score": 70,
            "action": "watch",
            "block_reason": "long_thesis_broken",
        }],
        available_cash=1000,
        total_assets=1000,
    )

    assert events[0]["event"] == "removed"
    assert "逻辑红线" in events[0]["reason"]


def test_lifecycle_does_not_create_a_removed_row_for_new_broken_thesis(tmp_path):
    from app.services.opportunity_pipeline import apply_scorecard_lifecycle
    from app.services.quant_lifecycle import TargetPoolStore

    store = TargetPoolStore(tmp_path / "candidate_pool.json")

    events = apply_scorecard_lifecycle(
        store,
        [{
            "code": "000001",
            "name": "新标的",
            "score": 70,
            "action": "watch",
            "block_reason": "long_thesis_broken",
        }],
        available_cash=1000,
        total_assets=1000,
    )

    assert events == []
    assert store.load()["items"] == {}
