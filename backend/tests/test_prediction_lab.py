import json
import math
import random

import pytest

from app.services.prediction_lab import (
    PredictionLedger,
    build_prediction_records,
    compare_paired_prediction_versions,
    evaluate_prediction_record,
    filter_execution_attributed_outcomes,
    score_price_action_prediction,
    suggest_strategy_adjustments,
    summarize_execution_attributed_outcomes,
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


def test_neutral_prediction_is_an_abstention_not_an_automatic_hit():
    bars = _bars()
    prediction = build_prediction_records(
        code="000001",
        name="平安银行",
        quote={"price": 10.9, "change_pct": 0, "vol_ratio": 1, "amount_wan": 30000},
        bars=bars[:10],
        prediction_date="2026-07-10",
        horizons=(1,),
    )[0]
    prediction["expected_direction"] = "neutral"

    outcome = evaluate_prediction_record(prediction, bars=bars, as_of="2026-07-11")
    summary = summarize_prediction_outcomes([outcome])

    assert outcome["actual_direction"] == "up"
    assert outcome["direction_hit"] is None
    assert summary["direction_evaluated_count"] == 0
    assert summary["abstention_count"] == 1
    assert summary["direction_accuracy"] == 0.0


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


def _complete_metric_outcome(idx, *, direction="up", hit=True, net_return=1.0, version="price_action_v1"):
    return {
        "prediction_id": f"pred_metric_{version}_{idx}",
        "prediction_date": f"2026-07-{idx + 1:02d}",
        "code": f"{idx:06d}",
        "model_version": version,
        "horizon": "T+1",
        "status": "verified",
        "expected_direction": direction,
        "direction_hit": hit if direction != "neutral" else None,
        "actual_return_pct": net_return + 0.2,
        "excess_return_pct": net_return,
        "broad_benchmark_id": "000300.SH",
        "tradable_universe_snapshot_id": "target_pool:sha256:fixture",
        "data_cutoff": "2026-07-01T15:00:00+08:00",
        "broad_benchmark_return_pct": 0.1,
        "tradable_universe_return_pct": 0.05,
        "tradable": True,
        "cost_estimated": False,
        "round_trip_cost_pct": 0.2,
        "net_tradable_return_pct": net_return,
    }


def test_profit_summary_exposes_coverage_adjusted_buy_metrics_and_actual_cost_returns():
    outcomes = [
        _complete_metric_outcome(0, hit=True, net_return=2.0),
        _complete_metric_outcome(1, hit=False, net_return=-1.0),
        _complete_metric_outcome(2, hit=True, net_return=3.0),
        _complete_metric_outcome(3, hit=False, net_return=-2.0),
        _complete_metric_outcome(4, direction="neutral", net_return=0.0),
    ]

    summary = summarize_prediction_outcomes(outcomes)

    assert summary["buy_signal_count"] == 4
    assert summary["buy_signal_precision"] == 0.5
    assert summary["abstention_rate"] == 0.2
    assert summary["signal_coverage"] == 0.8
    assert summary["average_win_pct"] == 2.5
    assert summary["average_loss_pct"] == -1.5
    assert summary["payoff_ratio"] == pytest.approx(1.667, abs=0.001)
    assert summary["net_expectancy_after_cost_pct"] == 0.5
    assert summary["profit_factor"] == pytest.approx(1.667, abs=0.001)
    assert summary["max_drawdown_pct"] == 2.0
    assert summary["actual_cost_coverage_complete"] is True


@pytest.mark.parametrize("missing_field", ["round_trip_cost_pct", "net_tradable_return_pct"])
def test_missing_actual_cost_or_net_return_blocks_summary_promotion(missing_field):
    outcomes = _promotion_ready_outcomes(cost_estimated=False)
    outcomes[0].pop(missing_field)

    summary = summarize_prediction_outcomes(outcomes)

    assert summary["actual_cost_coverage_complete"] is False
    assert summary["promotion_eligible"] is False


def _paired_outcome(idx, version, *, hit, direction="up"):
    return _complete_metric_outcome(
        idx,
        version=version,
        direction=direction,
        hit=hit,
        net_return=1.0 if hit else -1.0,
    )


def test_paired_version_comparison_requires_ci_excluding_zero_on_identical_units():
    outcomes = []
    for idx in range(10):
        outcomes.append(_paired_outcome(idx, "price_action_v1", hit=idx >= 6))
        outcomes.append(_paired_outcome(idx, "candidate_v2", hit=idx < 6))

    comparison = compare_paired_prediction_versions(outcomes, candidate_version="candidate_v2")

    assert comparison["paired_unit_count"] == 10
    assert comparison["identical_independent_units"] is True
    assert comparison["accuracy_delta"] == 0.2
    assert comparison["accuracy_delta_ci95"][0] < 0 < comparison["accuracy_delta_ci95"][1]
    assert comparison["net_expectancy_delta_after_cost_pct"] == 0.4
    assert comparison["net_expectancy_delta_ci95"][0] < 0 < comparison["net_expectancy_delta_ci95"][1]
    assert comparison["improvement"] is False
    assert "accuracy_ci_overlaps_zero" in comparison["reasons"]
    assert "net_profit_ci_overlaps_zero" in comparison["reasons"]


def test_paired_version_comparison_rejects_precision_gain_from_collapsed_coverage():
    outcomes = []
    for idx in range(10):
        outcomes.append(_paired_outcome(idx, "price_action_v1", hit=idx >= 5))
        outcomes.append(
            _paired_outcome(
                idx,
                "candidate_v2",
                hit=True,
                direction="up" if idx < 5 else "neutral",
            )
        )

    comparison = compare_paired_prediction_versions(outcomes, candidate_version="candidate_v2")

    assert comparison["candidate"]["buy_signal_precision"] == 1.0
    assert comparison["candidate"]["signal_coverage"] == 0.5
    assert comparison["baseline"]["signal_coverage"] == 1.0
    assert comparison["coverage_collapsed"] is True
    assert comparison["improvement"] is False
    assert "candidate_coverage_below_baseline" in comparison["reasons"]


def test_paired_version_accuracy_gain_cannot_substitute_for_worse_net_profit_after_cost():
    outcomes = []
    for idx in range(10):
        baseline = _paired_outcome(idx, "price_action_v1", hit=False)
        baseline.update(actual_return_pct=-0.1, net_tradable_return_pct=-0.2)
        candidate = _paired_outcome(idx, "candidate_v2", hit=True)
        candidate.update(actual_return_pct=0.1, net_tradable_return_pct=-1.0)
        outcomes.extend([baseline, candidate])

    comparison = compare_paired_prediction_versions(outcomes, candidate_version="candidate_v2")

    assert comparison["accuracy_delta_ci95"][0] > 0
    assert comparison["net_expectancy_delta_after_cost_pct"] == -0.8
    assert comparison["net_expectancy_delta_ci95"][1] < 0
    assert comparison["improvement"] is False
    assert "net_profit_delta_not_positive" in comparison["reasons"]


def test_paired_version_comparison_rejects_missing_units_or_mismatched_horizons():
    outcomes = []
    for idx in range(3):
        outcomes.append(_paired_outcome(idx, "price_action_v1", hit=False))
        if idx < 2:
            outcomes.append(_paired_outcome(idx, "candidate_v2", hit=True))
    outcomes[0]["horizon"] = "T+3"

    comparison = compare_paired_prediction_versions(outcomes, candidate_version="candidate_v2")

    assert comparison["paired_unit_count"] == 2
    assert comparison["identical_independent_units"] is False
    assert comparison["improvement"] is False
    assert "version_unit_or_horizon_sets_not_identical" in comparison["reasons"]


def test_paired_comparison_fails_when_one_side_has_incomplete_profit_or_market_evidence():
    outcomes = []
    for idx in range(60):
        outcomes.append(_paired_outcome(idx, "price_action_v1", hit=False))
        outcomes.append(_paired_outcome(idx, "candidate_v2", hit=True))
    candidate = next(
        item
        for item in outcomes
        if item["model_version"] == "candidate_v2" and item["code"] == "000000"
    )
    candidate["tradable"] = False
    candidate["cost_estimated"] = True
    candidate.pop("broad_benchmark_return_pct")

    comparison = compare_paired_prediction_versions(outcomes, candidate_version="candidate_v2")

    assert comparison["paired_unit_count"] == 60
    assert comparison["paired_direction_unit_count"] == 60
    assert comparison["paired_profit_unit_count"] == 59
    assert comparison["paired_benchmark_coverage_complete"] is False
    assert comparison["paired_tradable_coverage_complete"] is False
    assert comparison["paired_actual_cost_coverage_complete"] is False
    assert comparison["improvement"] is False
    assert "paired_evidence_incomplete" in comparison["reasons"]


def test_paired_comparison_rejects_matching_rows_when_both_horizons_are_missing():
    outcomes = []
    for idx in range(60):
        baseline = _paired_outcome(idx, "price_action_v1", hit=False)
        candidate = _paired_outcome(idx, "candidate_v2", hit=True)
        baseline.pop("horizon")
        candidate.pop("horizon")
        outcomes.extend([baseline, candidate])

    comparison = compare_paired_prediction_versions(outcomes, candidate_version="candidate_v2")

    assert comparison["paired_unit_count"] == 60
    assert comparison["paired_horizon_identity_complete"] is False
    assert comparison["identical_independent_units"] is False
    assert comparison["improvement"] is False
    assert "paired_horizon_identity_incomplete" in comparison["reasons"]


def test_paired_comparison_requires_sixty_complete_units_even_with_perfect_deltas():
    outcomes = []
    for idx in range(10):
        outcomes.append(_paired_outcome(idx, "price_action_v1", hit=False))
        outcomes.append(_paired_outcome(idx, "candidate_v2", hit=True))

    comparison = compare_paired_prediction_versions(outcomes, candidate_version="candidate_v2")

    assert comparison["accuracy_delta_ci95"][0] > 0
    assert comparison["net_expectancy_delta_ci95"][0] > 0
    assert comparison["improvement"] is False
    assert "paired_units_below_60" in comparison["reasons"]


def test_execution_attributed_summary_excludes_unlinked_outcomes_without_changing_shadow_summary():
    outcomes = [
        {
            "prediction_id": "pred-linked",
            "code": "000100",
            "signal_id": "signal-linked",
            "recommendation_id": "rec-linked",
            "fill_id": "fill-linked",
            "status": "verified",
            "expected_direction": "up",
            "direction_hit": True,
            "actual_return_pct": 1.0,
            "excess_return_pct": 0.5,
        },
        {
            "prediction_id": "pred-unlinked",
            "code": "002131",
            "signal_id": "signal-unlinked",
            "recommendation_id": "rec-unlinked",
            "fill_id": "fill-unlinked",
            "status": "verified",
            "expected_direction": "up",
            "direction_hit": True,
            "actual_return_pct": 99.0,
            "excess_return_pct": 98.0,
        },
    ]
    attribution = {
        "chains": [
            {
                "fill_id": "fill-linked",
                "code": "000100",
                "signal_id": "signal-linked",
                "recommendation_id": "rec-linked",
                "status": "attributed",
                "events": [{"event_type": "outcome", "payload": {"state": "closed"}}],
            },
            {
                "fill_id": "fill-unlinked",
                "code": "002131",
                "signal_id": "signal-unlinked",
                "recommendation_id": "rec-unlinked",
                "status": "unattributed",
                "events": [],
            },
        ]
    }

    filtered = filter_execution_attributed_outcomes(outcomes, attribution=attribution)
    attributed_summary = summarize_execution_attributed_outcomes(outcomes, attribution=attribution)
    shadow_summary = summarize_prediction_outcomes(outcomes)

    assert [item["prediction_id"] for item in filtered] == ["pred-linked"]
    assert attributed_summary["attributed_count"] == 1
    assert attributed_summary["excluded_unattributed_count"] == 1
    assert attributed_summary["avg_return_pct"] == 1.0
    assert shadow_summary["verified_count"] == 2
    assert shadow_summary["avg_return_pct"] == 50.0


def test_execution_attributed_filter_requires_unique_terminal_chain_and_all_four_ids():
    outcome = {
        "prediction_id": "pred-1",
        "code": "000100",
        "signal_id": "signal-1",
        "recommendation_id": "rec-1",
        "fill_id": "fill-1",
        "status": "verified",
        "actual_return_pct": 2.0,
    }
    terminal_chain = {
        "code": "000100",
        "signal_id": "signal-1",
        "recommendation_id": "rec-1",
        "fill_id": "fill-1",
        "status": "attributed",
        "events": [{"event_type": "outcome", "payload": {"status": "terminal"}}],
    }

    assert filter_execution_attributed_outcomes([outcome], attribution={"chains": [terminal_chain]}) == [outcome]
    for mismatch in [
        {**outcome, "code": "002131"},
        {**outcome, "signal_id": "signal-other"},
        {**outcome, "recommendation_id": None},
        {**outcome, "fill_id": None},
    ]:
        assert filter_execution_attributed_outcomes(
            [mismatch],
            attribution={"chains": [terminal_chain]},
        ) == []
    assert filter_execution_attributed_outcomes(
        [outcome],
        attribution={"chains": [terminal_chain, dict(terminal_chain)]},
    ) == []
    multiple_terminal_chain = {
        **terminal_chain,
        "events": [
            {"event_type": "outcome", "payload": {"state": "closed"}},
            {"event_type": "outcome", "payload": {"status": "terminal"}},
        ],
    }
    assert filter_execution_attributed_outcomes(
        [outcome],
        attribution={"chains": [multiple_terminal_chain]},
    ) == []


def test_summary_selects_one_canonical_terminal_outcome_per_prediction():
    summary = summarize_prediction_outcomes(
        [
            {
                "prediction_id": "pred_same",
                "status": "pending",
                "as_of": "2026-07-15",
                "prediction_date": "2026-07-10",
                "code": "000001",
                "model_version": "model_v1",
                "horizon": "T+1",
            },
            {
                "prediction_id": "pred_same",
                "status": "verified",
                "as_of": "2026-07-12",
                "expected_direction": "up",
                "direction_hit": True,
                "actual_return_pct": 1,
                "excess_return_pct": 0.5,
                "prediction_date": "2026-07-10",
                "code": "000001",
                "model_version": "model_v1",
                "horizon": "T+1",
            },
            {
                "prediction_id": "pred_same",
                "status": "verified",
                "evaluated_at": "2026-07-13T09:00:00",
                "expected_direction": "up",
                "direction_hit": False,
                "actual_return_pct": -2,
                "excess_return_pct": -2.5,
                "prediction_date": "2026-07-10",
                "code": "000001",
                "model_version": "model_v1",
                "horizon": "T+1",
            },
        ]
    )

    assert summary["terminal_count"] == 1
    assert summary["verified_count"] == 1
    assert summary["direction_evaluated_count"] == 1
    assert summary["direction_accuracy"] == 0.0
    assert summary["avg_return_pct"] == -2.0
    assert summary["outcome_coverage"] == 1.0


def test_jsonl_diagnostics_reports_malformed_line_and_blocks_promotion(tmp_path):
    path = tmp_path / "outcomes.jsonl"
    path.write_text(
        '{"prediction_id":"pred_ok","status":"verified"}\n'
        '{not-json}\n'
        '["not", "an", "object"]\n',
        encoding="utf-8",
    )

    diagnostics = PredictionLedger.read_jsonl_with_diagnostics(path)
    summary = summarize_prediction_outcomes(diagnostics["rows"], diagnostics=diagnostics)

    assert diagnostics["rows"] == [{"prediction_id": "pred_ok", "status": "verified"}]
    assert diagnostics["malformed_line_count"] == 2
    assert [item["line_number"] for item in diagnostics["malformed_lines"]] == [2, 3]
    assert all(item["reason"] for item in diagnostics["malformed_lines"])
    assert summary["malformed_line_count"] == 2
    assert summary["promotion_eligible"] is False


def test_multi_horizon_outcomes_count_as_one_independent_unit():
    outcomes = [
        {
            "prediction_id": f"pred_{horizon}",
            "prediction_date": "2026-07-10",
            "code": "000001",
            "model_version": "model_v1",
            "horizon": horizon,
            "status": "verified",
            "expected_direction": "up",
            "direction_hit": True,
            "actual_return_pct": 1,
            "excess_return_pct": 0.5,
        }
        for horizon in ("T+1", "T+3", "T+5")
    ]

    summary = summarize_prediction_outcomes(outcomes)

    assert summary["terminal_count"] == 3
    assert summary["verified_count"] == 3
    assert summary["independent_unit_count"] == 1
    assert set(summary["by_horizon"]) == {"T+1", "T+3", "T+5"}


def test_pending_independent_units_do_not_make_summary_promotion_eligible():
    outcomes = [
        {
            "prediction_id": f"pred_pending_{idx}",
            "prediction_date": f"2026-05-{idx + 1:02d}" if idx < 31 else f"2026-06-{idx - 30:02d}",
            "code": "000001",
            "model_version": "model_v1",
            "status": "pending",
        }
        for idx in range(60)
    ]

    summary = summarize_prediction_outcomes(outcomes)

    assert summary["terminal_count"] == 60
    assert summary["independent_unit_count"] == 60
    assert summary["verified_independent_unit_count"] == 0
    assert summary["promotion_eligible"] is False


def test_sixty_verified_among_six_hundred_pending_units_cannot_pass_coverage_gate():
    verified = _promotion_ready_outcomes(cost_estimated=False)
    pending = [
        {
            "prediction_id": f"pred_pending_adversarial_{idx}",
            "prediction_date": "2026-07-16",
            "code": f"9{idx:05d}",
            "model_version": "model_v1",
            "horizon": "T+1",
            "status": "pending",
        }
        for idx in range(600)
    ]

    summary = summarize_prediction_outcomes([*verified, *pending])

    assert summary["verified_independent_unit_count"] == 60
    assert summary["independent_unit_count"] == 660
    assert summary["outcome_coverage"] < 0.10
    assert summary["verified_independent_unit_coverage"] < 0.10
    assert summary["promotion_eligible"] is False


def test_cross_version_rows_do_not_double_thirty_real_units_into_sixty_samples():
    outcomes = []
    for idx in range(30):
        outcomes.append(_paired_outcome(idx, "price_action_v1", hit=True))
        outcomes.append(_paired_outcome(idx, "candidate_v2", hit=True))

    summary = summarize_prediction_outcomes(outcomes)

    assert summary["independent_unit_count"] == 30
    assert summary["verified_independent_unit_count"] == 30
    assert summary["model_versions"] == ["candidate_v2", "price_action_v1"]
    assert summary["mixed_model_versions"] is True
    assert set(summary["by_version"]) == {"candidate_v2", "price_action_v1"}
    assert summary["promotion_eligible"] is False


def test_extreme_finite_economic_values_are_malformed_and_never_overflow_aggregates():
    outcomes = _promotion_ready_outcomes(cost_estimated=False)
    outcomes[0]["actual_return_pct"] = 1e308
    outcomes[1]["round_trip_cost_pct"] = 1e308
    outcomes[2]["net_tradable_return_pct"] = -1e308

    summary = summarize_prediction_outcomes(outcomes)

    assert summary["malformed_outcome_count"] == 3
    assert summary["promotion_eligible"] is False
    assert all(
        math.isfinite(value)
        for value in summary.values()
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    )


def test_extreme_finite_paired_value_cannot_overflow_ci_or_claim_improvement():
    outcomes = []
    for idx in range(60):
        outcomes.append(_paired_outcome(idx, "price_action_v1", hit=False))
        outcomes.append(_paired_outcome(idx, "candidate_v2", hit=True))
    candidate = next(
        item
        for item in outcomes
        if item["model_version"] == "candidate_v2" and item["code"] == "000000"
    )
    candidate["net_tradable_return_pct"] = 1e308

    comparison = compare_paired_prediction_versions(outcomes, candidate_version="candidate_v2")

    assert comparison["paired_unit_count"] == 59
    assert comparison["paired_aggregate_outputs_finite"] is True
    assert all(
        math.isfinite(value)
        for value in (
            comparison["accuracy_delta"],
            *comparison["accuracy_delta_ci95"],
            comparison["net_expectancy_delta_after_cost_pct"],
            *comparison["net_expectancy_delta_ci95"],
        )
    )
    assert comparison["improvement"] is False


def test_drawdown_is_permutation_invariant_and_uses_equal_weight_daily_net_returns():
    outcomes = [
        _complete_metric_outcome(0, net_return=4.0),
        {**_complete_metric_outcome(1, net_return=-2.0), "prediction_date": "2026-07-01"},
        {**_complete_metric_outcome(2, net_return=-3.0), "prediction_date": "2026-07-02"},
        {**_complete_metric_outcome(3, net_return=1.0), "prediction_date": "2026-07-03"},
    ]
    shuffled = list(outcomes)
    random.Random(42).shuffle(shuffled)

    ordered_summary = summarize_prediction_outcomes(outcomes)
    shuffled_summary = summarize_prediction_outcomes(shuffled)

    assert ordered_summary["max_drawdown_pct"] == shuffled_summary["max_drawdown_pct"]
    assert ordered_summary["max_drawdown_pct"] == 3.0
    assert ordered_summary["max_drawdown_basis"] == (
        "equal_weight_buy_signal_net_return_by_prediction_date_compounded;"
        "overlapping_horizons_equal_weighted_within_date"
    )


def test_suggest_strategy_adjustments_downweights_bad_reason():
    suggestions = suggest_strategy_adjustments(
        [
            {
                "status": "verified",
                "direction_hit": False,
                "excess_return_pct": -2.0,
                "benchmark_coverage_complete": True,
                "broad_benchmark_id": "000300.SH",
                "tradable_universe_snapshot_id": "target_pool:sha256:fixture",
                "data_cutoff": "2026-07-15T15:00:00+08:00",
                "broad_benchmark_return_pct": 1.0,
                "tradable_universe_return_pct": 0.5,
                "tradable": True,
                "cost_estimated": False,
                "reasons": ["high_position_risk"],
            },
            {
                "status": "verified",
                "direction_hit": False,
                "excess_return_pct": -1.5,
                "benchmark_coverage_complete": True,
                "broad_benchmark_id": "000300.SH",
                "tradable_universe_snapshot_id": "target_pool:sha256:fixture",
                "data_cutoff": "2026-07-15T15:00:00+08:00",
                "broad_benchmark_return_pct": 1.0,
                "tradable_universe_return_pct": 0.5,
                "tradable": True,
                "cost_estimated": False,
                "reasons": ["high_position_risk"],
            },
            {
                "status": "verified",
                "direction_hit": True,
                "excess_return_pct": -1.2,
                "benchmark_coverage_complete": True,
                "broad_benchmark_id": "000300.SH",
                "tradable_universe_snapshot_id": "target_pool:sha256:fixture",
                "data_cutoff": "2026-07-15T15:00:00+08:00",
                "broad_benchmark_return_pct": 1.0,
                "tradable_universe_return_pct": 0.5,
                "tradable": True,
                "cost_estimated": False,
                "reasons": ["high_position_risk"],
            },
        ],
        min_samples=3,
    )

    assert suggestions[0]["trigger_reason"] == "high_position_risk"
    assert suggestions[0]["action"] == "downweight_trigger"


def test_suggest_strategy_adjustments_defaults_to_research_only_below_sixty_samples():
    suggestions = suggest_strategy_adjustments(
        [
            {
                "prediction_id": f"pred_{idx}",
                "status": "verified",
                "expected_direction": "up",
                "direction_hit": False,
                "excess_return_pct": -2.0,
                "reasons": ["high_position_risk"],
            }
            for idx in range(3)
        ]
    )

    assert len(suggestions) == 1
    assert suggestions[0]["sample_count"] == 3
    assert suggestions[0]["minimum_samples"] == 60
    assert suggestions[0]["research_only"] is True
    assert suggestions[0]["promotion_eligible"] is False
    assert suggestions[0]["action"] == "research_only"


def test_neutral_abstentions_do_not_trigger_low_accuracy_downweight():
    suggestions = suggest_strategy_adjustments(
        [
            {
                "prediction_id": f"pred_neutral_{idx}",
                "prediction_date": "2026-07-15",
                "code": f"{idx:06d}",
                "model_version": "model_v1",
                "status": "verified",
                "expected_direction": "neutral",
                "direction_hit": None,
                "excess_return_pct": 0.0,
                "benchmark_coverage_complete": True,
                "broad_benchmark_id": "000300.SH",
                "tradable_universe_snapshot_id": "target_pool:sha256:fixture",
                "data_cutoff": "2026-07-15T15:00:00+08:00",
                "broad_benchmark_return_pct": 0.0,
                "tradable_universe_return_pct": 0.0,
                "tradable": True,
                "cost_estimated": False,
                "reasons": ["neutral_signal"],
            }
            for idx in range(60)
        ]
    )

    assert len(suggestions) == 1
    assert suggestions[0]["sample_count"] == 60
    assert suggestions[0]["promotion_eligible"] is False
    assert suggestions[0]["direction_evaluated_count"] == 0
    assert suggestions[0]["action"] == "research_only"
    assert suggestions[0]["insufficient_direction_evidence"] is True


def _promotion_ready_outcomes(*, cost_estimated: bool) -> list[dict]:
    return [
        {
            "prediction_id": f"pred_ready_{idx}",
            "prediction_date": "2026-07-15",
            "code": f"{idx:06d}",
            "model_version": "model_v1",
            "status": "verified",
            "expected_direction": "up",
            "direction_hit": True,
            "actual_return_pct": 2.0,
            "excess_return_pct": 1.0,
            "broad_benchmark_id": "000300.SH",
            "tradable_universe_snapshot_id": "target_pool:sha256:fixture",
            "data_cutoff": "2026-07-15T15:00:00+08:00",
            "broad_benchmark_return_pct": 1.0,
            "tradable_universe_return_pct": 0.5,
            "benchmark_coverage_complete": True,
            "tradable": True,
            "cost_estimated": cost_estimated,
            "round_trip_cost_pct": 0.2,
            "net_tradable_return_pct": 1.8,
            "reasons": ["ready_signal"],
        }
        for idx in range(60)
    ]


def test_estimated_cost_blocks_summary_promotion():
    summary = summarize_prediction_outcomes(_promotion_ready_outcomes(cost_estimated=True))

    assert summary["cost_coverage_complete"] is False
    assert summary["promotion_eligible"] is False


def test_estimated_cost_blocks_adjustment_promotion():
    suggestion = suggest_strategy_adjustments(_promotion_ready_outcomes(cost_estimated=True))[0]

    assert suggestion["cost_coverage_complete"] is False
    assert suggestion["promotion_eligible"] is False
    assert suggestion["action"] == "research_only"


def test_summary_rejects_claimed_benchmark_coverage_with_unavailable_identity():
    outcomes = _promotion_ready_outcomes(cost_estimated=False)
    outcomes[0]["broad_benchmark_id"] = "unavailable"

    summary = summarize_prediction_outcomes(outcomes)

    assert summary["benchmark_coverage_complete"] is False
    assert summary["promotion_eligible"] is False


def test_adjustment_rejects_claimed_benchmark_coverage_with_missing_cutoff():
    outcomes = _promotion_ready_outcomes(cost_estimated=False)
    outcomes[0]["data_cutoff"] = None

    suggestion = suggest_strategy_adjustments(outcomes)[0]

    assert suggestion["benchmark_coverage_complete"] is False
    assert suggestion["promotion_eligible"] is False


def test_nonfinite_verified_outcome_is_malformed_excluded_and_blocks_sixty_unit_promotion():
    outcomes = _promotion_ready_outcomes(cost_estimated=False)
    outcomes[0]["actual_return_pct"] = float("nan")
    outcomes[1]["excess_return_pct"] = float("inf")
    outcomes[2]["broad_benchmark_return_pct"] = "NaN"
    outcomes[3]["round_trip_cost_pct"] = "Infinity"

    summary = summarize_prediction_outcomes(outcomes)
    suggestion = suggest_strategy_adjustments(outcomes)[0]

    assert summary["verified_count"] == 56
    assert summary["malformed_outcome_count"] == 4
    assert summary["promotion_eligible"] is False
    assert suggestion["sample_count"] == 56
    assert suggestion["malformed_outcome_count"] == 4
    assert suggestion["promotion_eligible"] is False
    assert all(
        math.isfinite(value)
        for value in summary.values()
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    )


def test_finite_numeric_strings_remain_promotion_eligible():
    outcomes = _promotion_ready_outcomes(cost_estimated=False)
    for outcome in outcomes:
        outcome.update({
            "actual_return_pct": "2.0",
            "excess_return_pct": "1.0",
            "broad_benchmark_return_pct": "1.0",
            "tradable_universe_return_pct": "0.5",
            "round_trip_cost_pct": "0.1",
        })

    summary = summarize_prediction_outcomes(outcomes)

    assert summary["verified_count"] == 60
    assert summary["malformed_outcome_count"] == 0
    assert summary["avg_return_pct"] == 2.0
    assert summary["profit_factor"] is None
    assert summary["profit_factor_unbounded"] is True
    assert summary["promotion_eligible"] is True


def test_complete_but_consistently_losing_buy_signals_cannot_be_promoted():
    outcomes = _promotion_ready_outcomes(cost_estimated=False)
    for outcome in outcomes:
        outcome.update(
            direction_hit=False,
            actual_return_pct=-1.0,
            net_tradable_return_pct=-1.2,
        )

    summary = summarize_prediction_outcomes(outcomes)
    suggestion = suggest_strategy_adjustments(outcomes)[0]

    assert summary["benchmark_coverage_complete"] is True
    assert summary["actual_cost_coverage_complete"] is True
    assert summary["net_expectancy_after_cost_pct"] == -1.2
    assert summary["profit_factor"] == 0.0
    assert summary["promotion_eligible"] is False
    assert suggestion["promotion_eligible"] is False
