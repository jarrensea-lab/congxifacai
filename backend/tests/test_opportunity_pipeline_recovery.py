from __future__ import annotations

import asyncio

import pytest


def _market_row(code: str = "000001") -> dict:
    return {
        "code": code,
        "name": "测试标的",
        "price": 3.0,
        "change_pct": 3.2,
        "vol_ratio": 2.1,
        "amount_wan": 12000,
        "main_net_amount_wan": 800,
    }


class SequenceSource:
    def __init__(self, values):
        self.values = list(values)
        self.calls = 0

    async def fetch_fund_flow_individual(self):
        value = self.values[min(self.calls, len(self.values) - 1)]
        self.calls += 1
        return value


def _snapshot(candidate):
    return {
        "code": candidate["code"],
        "name": candidate["name"],
        "generated_at": "2026-07-31T20:30:00+08:00",
        "quote": {"status": "ok", "price": 3.0},
        "kline": {"status": "ok", "bars": [{"close": 2.5}, {"close": 2.8}, {"close": 3}]},
        "fund_flow": {"status": "ok", "main_net_amount_wan": 800},
        "financial": {"status": "ok", "revenue_yoy_pct": 10},
    }


def _score(snapshot, **_):
    return {
        "code": snapshot["code"],
        "name": snapshot["name"],
        "action": "buy",
        "status": "executable",
        "score": 82,
        "grade": "A",
        "entry_price": 3,
        "stop_loss": 2.7,
        "target_price": 3.6,
        "shares": 100,
        "max_planned_loss": 30,
        "top_reasons": ["量价改善", "资金流入", "估值可接受"],
        "primary_risk": "板块波动",
        "playbook": "breakout_entry",
    }


@pytest.mark.asyncio
async def test_zero_market_rows_retries_fallback_and_never_reports_success(tmp_path):
    from app.services.opportunity_pipeline import OpportunityPipeline
    from app.services.pipeline_run_store import PipelineRunStore

    primary = SequenceSource([[], [], []])
    fallback = SequenceSource([[_market_row()]])
    pipeline = OpportunityPipeline(
        discovery_source=primary,
        fallback_source=fallback,
        snapshot_builder=_snapshot,
        scorer=_score,
        run_store=PipelineRunStore(tmp_path / "pipeline.db"),
        artifact_dir=tmp_path / "artifacts",
        sleeper=lambda _: asyncio.sleep(0),
    )

    result = await pipeline.run(
        "2026-07-31",
        available_cash=800,
        total_assets=800,
    )

    stage = result["stages"]["market_discovery"]
    assert stage["status"] == "degraded"
    assert stage["attempt"] == 3
    assert stage["recovery_action"] == "fallback_source"
    assert result["report_validation"]["ok"] is True


@pytest.mark.asyncio
async def test_watchdog_resumes_render_stage_without_reapplying_lifecycle(tmp_path):
    from app.services.opportunity_pipeline import OpportunityPipeline
    from app.services.pipeline_run_store import PipelineRunStore

    counts = {"lifecycle": 0, "render": 0}

    def lifecycle(scorecards):
        counts["lifecycle"] += 1
        return [
            {
                "code": scorecards[0]["code"],
                "name": scorecards[0]["name"],
                "event": "added",
                "reason": "首次达到可执行标准",
            }
        ]

    def render(result):
        counts["render"] += 1
        if counts["render"] == 1:
            raise RuntimeError("render_failed_once")
        from scripts.daily_report import build_v9_opportunity_section

        return "\n".join(build_v9_opportunity_section(result))

    pipeline = OpportunityPipeline(
        discovery_source=SequenceSource([[_market_row()]]),
        snapshot_builder=_snapshot,
        scorer=_score,
        lifecycle_applier=lifecycle,
        renderer=render,
        run_store=PipelineRunStore(tmp_path / "pipeline.db"),
        artifact_dir=tmp_path / "artifacts",
        sleeper=lambda _: asyncio.sleep(0),
    )

    first = await pipeline.run(
        "2026-07-31",
        available_cash=800,
        total_assets=800,
    )
    second = await pipeline.recover(first["run_id"])

    assert first["stages"]["report_render"]["status"] == "failed"
    assert second["stages"]["report_render"]["status"] == "succeeded"
    assert counts["lifecycle"] == 1
