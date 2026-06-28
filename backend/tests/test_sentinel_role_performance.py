"""Tests for Sentinel role prediction and advice performance review."""

import json
from pathlib import Path

import pytest


def _sample_debate_result() -> dict:
    return {
        "debate": {
            "hunter": {
                "market_view": "短线偏强，但只能低仓位试错。",
                "sector_focus": ["机器人"],
                "recommendations": [
                    {"code": "300001", "name": "特锐德", "reason": "放量突破", "level": "中"}
                ],
                "conviction": 7,
                "analysis": "技术信号清楚，量能需要继续确认。",
            },
            "accountant": {
                "market_view": "估值修复仍需财报确认。",
                "sector_focus": ["电网设备"],
                "recommendations": [
                    {"code": "000400", "name": "许继电气", "reason": "估值和订单较稳", "level": "中"}
                ],
                "conviction": 6,
                "analysis": "基本面证据较稳，但需要更新财报。",
            },
            "guardian": {
                "position_advice": "仓位不超过三成，等待确认。",
                "systemic_risks": ["小账户现金安全垫不足"],
                "risk_appetite": "低",
                "conviction": 8,
                "analysis": "风险主要来自追高和现金垫不足。",
            },
            "researcher": {
                "industry_chain_summary": "电网设备存在订单验证机会。",
                "true_bottlenecks": [{"sector": "电网设备", "scarce_resource": "高压设备"}],
                "recommendations": [
                    {"code": "000400", "name": "许继电气", "reason": "产业链卡点候选", "level": "中"}
                ],
                "conviction": 6,
                "analysis": "产业链逻辑成立，但不能等同于股价确定上涨。",
            },
        },
        "final": {
            "final_decision": "观望",
            "confidence": 6,
            "reasoning": "综合看，机会存在但账户约束优先。",
            "top_sectors": ["电网设备", "机器人"],
        },
    }


def _sample_decision() -> dict:
    return {
        "final_decision": "观望",
        "confidence": 6,
        "reasoning": "综合四位辩手，建议等待确认。",
        "top_sectors": ["电网设备", "机器人"],
        "stock_pool": [
            {"code": "000400", "name": "许继电气", "reason": "电网设备候选"},
            {"code": "300001", "name": "特锐德", "reason": "短线技术候选"},
        ],
        "risk_summary": "小账户现金垫不足。",
    }


def test_record_role_predictions_writes_required_schema(tmp_path):
    from app.ai.sentinel_role_performance import record_debate_predictions

    records = record_debate_predictions(
        _sample_debate_result(),
        decision=_sample_decision(),
        prediction_date="2026-06-28",
        strategy_type="daily",
        source_report="/tmp/report.md",
        output_root=tmp_path,
    )

    assert {record["role"] for record in records} == {
        "hunter",
        "accountant",
        "guardian",
        "researcher",
        "judge",
    }
    assert {record["horizon"] for record in records} == {"1d", "3d", "5d", "20d"}
    for record in records:
        assert record["prediction_type"]
        assert record["target"]
        assert record["confidence"] is not None
        assert record["source_report"] == "/tmp/report.md"
        assert "api_key" not in json.dumps(record).lower()

    output_path = tmp_path / "role_predictions" / "2026-06-28.jsonl"
    assert output_path.exists()
    persisted = [json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines()]
    assert len(persisted) == len(records)


def test_evaluate_prediction_marks_due_and_return_error():
    from app.ai.sentinel_role_performance import evaluate_prediction

    prediction = {
        "id": "p1",
        "role": "hunter",
        "prediction_date": "2026-06-20",
        "horizon": "5d",
        "prediction_type": "recommendation",
        "target": "300001",
        "expected_direction": "up",
        "predicted_return_pct": 5.0,
        "confidence": 7,
    }
    actuals = {
        "as_of": "2026-06-25",
        "targets": {"300001": {"direction": "up", "return_pct": 3.0, "benchmark_return_pct": 1.0}},
    }

    outcome = evaluate_prediction(prediction, actuals)

    assert outcome["status"] == "verified"
    assert outcome["direction_hit"] is True
    assert outcome["return_error_pct"] == pytest.approx(2.0)
    assert outcome["excess_return_pct"] == pytest.approx(2.0)


def test_score_roles_separates_user_execution_discipline():
    from app.ai.sentinel_role_performance import score_roles

    outcomes = [
        {
            "role": "guardian",
            "status": "verified",
            "direction_hit": True,
            "excess_return_pct": 0.0,
            "risk_hit": True,
            "duty_score": 88,
            "explanation_score": 80,
            "advice_quality": 90,
            "execution": {
                "advice_given": True,
                "user_executed": False,
                "execution_match": False,
                "execution_deviation_reason": "用户没有按观望建议执行",
            },
        }
    ]

    scorecard = score_roles(outcomes)

    guardian = scorecard["roles"]["guardian"]
    assert guardian["sample_count"] == 1
    assert guardian["execution_discipline"]["role_score_impact"] == "no_penalty"
    assert guardian["score"] > 70
    assert scorecard["execution_discipline"]["items"][0]["discipline_score_impact"] == "user_discipline_only"


def test_advice_performance_distinguishes_paper_and_executed_advice():
    from app.ai.sentinel_role_performance import summarize_advice_performance

    outcomes = [
        {
            "role": "judge",
            "status": "verified",
            "target": "000400",
            "actual_return_pct": 4.0,
            "benchmark_return_pct": 1.0,
            "execution": {"advice_given": True, "user_executed": True, "execution_match": True},
        },
        {
            "role": "hunter",
            "status": "verified",
            "target": "300001",
            "actual_return_pct": -2.0,
            "benchmark_return_pct": -1.0,
            "execution": {"advice_given": True, "user_executed": False, "execution_match": False},
        },
    ]

    summary = summarize_advice_performance(outcomes)

    assert summary["executed"]["count"] == 1
    assert summary["executed"]["win_rate"] == 1.0
    assert summary["paper_only"]["count"] == 1
    assert summary["paper_only"]["avg_return_pct"] == pytest.approx(-2.0)


def test_adjustment_suggestions_use_allowed_actions_only():
    from app.ai.sentinel_role_performance import ALLOWED_ADJUSTMENT_ACTIONS, suggest_role_adjustments

    scorecard = {
        "roles": {
            "hunter": {"score": 82, "sample_count": 8, "accuracy": 0.75},
            "accountant": {"score": 44, "sample_count": 8, "accuracy": 0.25},
            "guardian": {"score": 68, "sample_count": 2, "accuracy": 0.5},
        }
    }

    suggestions = suggest_role_adjustments(scorecard)

    assert suggestions
    assert {item["action"] for item in suggestions}.issubset(ALLOWED_ADJUSTMENT_ACTIONS)
    assert "buy" not in json.dumps(suggestions).lower()
    assert "sell" not in json.dumps(suggestions).lower()


def test_render_scorecard_report_mentions_execution_discipline():
    from app.ai.sentinel_role_performance import render_scorecard_markdown

    markdown = render_scorecard_markdown(
        {
            "date": "2026-06-28",
            "roles": {
                "judge": {
                    "score": 72,
                    "sample_count": 3,
                    "accuracy": 0.67,
                    "summary": "裁判综合判断基本有效。",
                }
            },
            "execution_discipline": {
                "summary": "有 1 条建议未执行，不扣角色分。",
                "items": [],
            },
        },
        advice_performance={"executed": {"count": 1, "win_rate": 1.0}, "paper_only": {"count": 1}},
        suggestions=[{"role": "judge", "action": "keep_weight", "reason": "样本仍少，继续观察。"}],
    )

    assert "Sentinel 角色评分" in markdown
    assert "执行纪律" in markdown
    assert "不扣角色分" in markdown
    assert "keep_weight" in markdown


def test_evaluate_and_persist_outcomes_writes_jsonl(tmp_path):
    from app.ai.sentinel_role_performance import evaluate_and_persist_outcomes

    predictions = [
        {
            "id": "p1",
            "role": "hunter",
            "prediction_date": "2026-06-20",
            "horizon": "5d",
            "prediction_type": "recommendation",
            "target": "300001",
            "expected_direction": "up",
            "confidence": 7,
        }
    ]
    actuals = {
        "as_of": "2026-06-25",
        "targets": {"300001": {"direction": "up", "return_pct": 3.0, "benchmark_return_pct": 1.0}},
    }

    outcomes = evaluate_and_persist_outcomes(
        predictions,
        actuals,
        outcome_date="2026-06-25",
        output_root=tmp_path,
    )

    assert outcomes[0]["status"] == "verified"
    path = tmp_path / "role_outcomes" / "2026-06-25.jsonl"
    assert path.exists()
    persisted = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert persisted[0]["prediction_id"] == "p1"


def test_persist_review_outputs_writes_json_and_markdown(tmp_path):
    from app.ai.sentinel_role_performance import persist_review_outputs

    scorecard = {
        "date": "2026-06-28",
        "roles": {"hunter": {"score": 82, "sample_count": 5, "accuracy": 0.8, "summary": "猎手表现有效。"}},
        "execution_discipline": {"summary": "执行纪律单独归因。", "items": []},
    }
    advice = {
        "date": "2026-06-28",
        "executed": {"count": 1, "win_rate": 1.0, "avg_return_pct": 3.0},
        "paper_only": {"count": 2, "avg_return_pct": -1.0},
    }

    paths = persist_review_outputs(
        scorecard=scorecard,
        advice_performance=advice,
        suggestions=[{"role": "hunter", "action": "increase_weight", "reason": "短线判断近期有效。"}],
        output_root=tmp_path,
    )

    for path in paths.values():
        assert Path(path).exists()
    assert "Sentinel 角色评分" in Path(paths["role_scorecard"]).read_text(encoding="utf-8")
    assert "投资建议绩效" in Path(paths["advice_report"]).read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_run_debate_best_effort_records_sentinel_predictions(monkeypatch):
    import app.ai.debate as debate_module
    import app.engine.debate_tracker as tracker_module
    import app.ai.sentinel_role_performance as performance_module
    from app.engine.workshop import run_debate

    captured = {}

    class FakeEngine:
        async def debate(self, market_data, holdings_data, news, role_performance=""):
            return _sample_debate_result()

    def fake_record(debate_result, **kwargs):
        captured["debate_result"] = debate_result
        captured["kwargs"] = kwargs
        return []

    monkeypatch.setattr(debate_module, "AIDebateEngine", FakeEngine)
    monkeypatch.setattr(tracker_module.DebateTracker, "save", staticmethod(lambda *args, **kwargs: 123))
    monkeypatch.setattr(performance_module, "record_debate_predictions", fake_record)

    result = await run_debate({
        "available_cash": 3000,
        "total_assets": 3000,
        "holdings_str": "无持仓",
        "news": [],
    })

    assert result["decision"]["final_decision"] == "观望"
    assert captured["debate_result"]["final"]["final_decision"] == "观望"
    assert captured["kwargs"]["strategy_type"] == "premarket"
    assert captured["kwargs"]["decision"]["final_decision"] == "观望"


@pytest.mark.asyncio
async def test_run_debate_ignores_sentinel_prediction_recording_failure(monkeypatch):
    import app.ai.debate as debate_module
    import app.engine.debate_tracker as tracker_module
    import app.ai.sentinel_role_performance as performance_module
    from app.engine.workshop import run_debate

    class FakeEngine:
        async def debate(self, market_data, holdings_data, news, role_performance=""):
            return _sample_debate_result()

    def failing_record(*args, **kwargs):
        raise RuntimeError("disk temporarily unavailable")

    monkeypatch.setattr(debate_module, "AIDebateEngine", FakeEngine)
    monkeypatch.setattr(tracker_module.DebateTracker, "save", staticmethod(lambda *args, **kwargs: 123))
    monkeypatch.setattr(performance_module, "record_debate_predictions", failing_record)

    result = await run_debate({
        "available_cash": 3000,
        "total_assets": 3000,
        "holdings_str": "无持仓",
        "news": [],
    })

    assert result["decision"]["final_decision"] == "观望"
