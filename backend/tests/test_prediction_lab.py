import json

import pytest

from app.services.prediction_lab import (
    PredictionLedger,
    build_prediction_records,
    evaluate_prediction_record,
    score_price_action_prediction,
    suggest_strategy_adjustments,
    summarize_prediction_outcomes,
)


def _bars():
    return [
        {"date": f"2026-07-{idx + 1:02d}", "close": 10 + idx * 0.1, "high": 10.05 + idx * 0.1, "low": 9.95 + idx * 0.1}
        for idx in range(20)
    ]


def test_score_price_action_prediction_penalizes_high_position():
    result = score_price_action_prediction(
        {
            "change_pct": 4.0,
            "vol_ratio": 2.5,
            "amount_wan": 20000,
            "range_position_pct": 93,
            "ma5": 11.8,
            "ma10": 11.6,
            "return_5d_pct": 5,
        }
    )

    assert result["expected_direction"] != "up"
    assert "high_position_risk" in result["reasons"]


def test_build_prediction_records_creates_multi_horizon_schema():
    records = build_prediction_records(
        code="000001",
        name="平安银行",
        quote={"price": 11.8, "change_pct": 1.8, "vol_ratio": 2.1, "amount_wan": 30000},
        bars=_bars(),
        prediction_date="2026-07-20",
    )

    assert {item["horizon"] for item in records} == {"T+1", "T+3", "T+5"}
    assert all(item["prediction_id"].startswith("pred_") for item in records)
    assert all(item["features"]["bar_count"] == 20 for item in records)
    assert "api_key" not in json.dumps(records).lower()


def test_prediction_ledger_deduplicates_predictions(tmp_path):
    ledger = PredictionLedger(tmp_path)
    records = build_prediction_records(
        code="000001",
        name="平安银行",
        quote={"price": 11.8, "change_pct": 1.8, "vol_ratio": 2.1, "amount_wan": 30000},
        bars=_bars(),
        prediction_date="2026-07-20",
    )

    assert ledger.append_predictions(records) == 3
    assert ledger.append_predictions(records) == 0

    persisted = ledger.read_jsonl(tmp_path / "predictions" / "2026-07-20.jsonl")
    assert len(persisted) == 3


def test_evaluate_prediction_record_marks_verified_when_horizon_due():
    bars = _bars()
    prediction = build_prediction_records(
        code="000001",
        name="平安银行",
        quote={"price": 10.9, "change_pct": 1.8, "vol_ratio": 2.1, "amount_wan": 30000},
        bars=bars[:10],
        prediction_date="2026-07-10",
        horizons=(3,),
    )[0]
    prediction["expected_direction"] = "up"
    prediction["expected_return_pct"] = 2.0

    outcome = evaluate_prediction_record(prediction, bars=bars, benchmark_return_pct=0.5, as_of="2026-07-13")

    assert outcome["status"] == "verified"
    assert outcome["actual_return_pct"] == pytest.approx(2.75, abs=0.01)
    assert outcome["direction_hit"] is True
    assert outcome["excess_return_pct"] == pytest.approx(2.25)


def test_summarize_prediction_outcomes():
    summary = summarize_prediction_outcomes(
        [
            {"status": "verified", "direction_hit": True, "actual_return_pct": 2, "excess_return_pct": 1},
            {"status": "verified", "direction_hit": False, "actual_return_pct": -1, "excess_return_pct": -2},
            {"status": "pending"},
        ]
    )

    assert summary["verified_count"] == 2
    assert summary["direction_accuracy"] == 0.5
    assert summary["avg_return_pct"] == 0.5


def test_suggest_strategy_adjustments_downweights_bad_reason():
    suggestions = suggest_strategy_adjustments(
        [
            {
                "status": "verified",
                "direction_hit": False,
                "excess_return_pct": -2.0,
                "reasons": ["high_position_risk"],
            },
            {
                "status": "verified",
                "direction_hit": False,
                "excess_return_pct": -1.5,
                "reasons": ["high_position_risk"],
            },
            {
                "status": "verified",
                "direction_hit": True,
                "excess_return_pct": -1.2,
                "reasons": ["high_position_risk"],
            },
        ],
        min_samples=3,
    )

    assert suggestions[0]["trigger_reason"] == "high_position_risk"
    assert suggestions[0]["action"] == "downweight_trigger"
