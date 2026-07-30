from pathlib import Path

import pytest


class MarketSource:
    async def fetch_fund_flow_individual(self):
        return [
            {
                "code": "000001",
                "name": "可买标的",
                "price": 3,
                "change_pct": 3,
                "amount": 200_000_000,
                "net_amount": 20_000_000,
            },
            {
                "code": "600000",
                "name": "超预算标的",
                "price": 12,
                "change_pct": 2,
                "amount": 300_000_000,
                "net_amount": 30_000_000,
            },
        ]


def snapshot(candidate):
    return {
        "code": candidate["code"],
        "name": candidate["name"],
        "generated_at": "2026-07-31T20:30:00+08:00",
        "quote": {"status": "ok", "price": 3, "change_pct": 3, "vol_ratio": 2.2},
        "kline": {"status": "ok", "bars": [{"close": 2.5}, {"close": 2.8}, {"close": 3}]},
        "fund_flow": {"status": "ok", "main_net_amount_wan": 800},
        "financial": {"status": "ok", "revenue_yoy_pct": 10},
    }


def score(snapshot, **_):
    return {
        "code": snapshot["code"],
        "name": snapshot["name"],
        "action": "buy",
        "status": "executable",
        "score": 82,
        "grade": "A",
        "entry_price": 3,
        "shares": 100,
        "planned_amount": 300,
        "stop_loss": 2.7,
        "target_price": 3.6,
        "max_planned_loss": 30,
        "top_reasons": ["量价改善", "资金流入", "基本面稳定"],
        "primary_risk": "板块波动",
        "playbook": "breakout_entry",
    }


@pytest.mark.asyncio
async def test_v9_pipeline_generates_actionable_report_and_delivery_truth(tmp_path):
    from app.services.opportunity_pipeline import OpportunityPipeline, STAGES
    from app.services.pipeline_run_store import PipelineRunStore

    pipeline = OpportunityPipeline(
        discovery_source=MarketSource(),
        snapshot_builder=snapshot,
        scorer=score,
        run_store=PipelineRunStore(tmp_path / "pipeline.db"),
        artifact_dir=tmp_path / "artifacts",
    )

    result = await pipeline.run(
        "2026-07-31",
        available_cash=800,
        total_assets=800,
    )

    report_path = Path(result["delivery"]["report_path"])
    content = report_path.read_text(encoding="utf-8")
    assert result["metrics"]["scanned_count"] == 2
    assert result["metrics"]["affordable_count"] == 1
    assert result["report_validation"]["ok"] is True
    assert result["delivery"]["report_digest"]
    assert "000001" in content
    assert "600000" not in content
    assert set(result["stages"]) == set(STAGES)
    assert all(
        stage["status"] in {"succeeded", "degraded"}
        for stage in result["stages"].values()
    )
