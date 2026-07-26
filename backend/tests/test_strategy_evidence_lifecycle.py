import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from app.services.strategy_evidence_lifecycle import (
    EventIdConflict,
    InvalidStrategyTransition,
    PromotionGateRejected,
    StrategyEvidenceStore,
    evaluate_promotion_gate,
)


def _hypothesis(**overrides):
    payload = {
        "strategy_id": "douyin-breakout-filter",
        "version": "v1",
        "source": "douyin",
        "source_quality": 0.95,
        "ocr_quality": 0.95,
        "falsifiable_claim": "过滤高位放量后，成本后超额收益不低于基准。",
        "feature_class": "risk_filter",
        "data_cutoff": "2026-07-14",
        "failure_condition": "滚动成本后净超额收益小于零。",
        "production_impact": "仅收紧候选过滤，不产生交易指令。",
    }
    payload.update(overrides)
    return payload


def _passing_evaluation(**overrides):
    evaluation = {
        "independent_units": 60,
        "trading_days": 20,
        "outcome_coverage": 0.90,
        "verified_independent_unit_coverage": 0.90,
        "benchmark_coverage": {
            "broad_market": 1.0,
            "tradable_universe": 1.0,
        },
        "malformed_count": 0,
        "cost_estimated": False,
        "tradable_coverage": 1.0,
        "actual_cost_coverage": 1.0,
        "buy_signal_precision": 0.60,
        "abstention_rate": 0.20,
        "signal_coverage": 0.80,
        "average_win_pct": 1.2,
        "average_loss_pct": -0.8,
        "payoff_ratio": 1.5,
        "net_expectancy_after_cost_pct": 0.2,
        "profit_factor": 1.2,
        "holdout_excess_after_cost": [0.2, 0.1],
        "walk_forward_excess_after_cost": [0.2, -0.1, 0.1],
        "ci95_lower_bound": 0.01,
        "max_drawdown_pct": 8.0,
        "profile_max_drawdown_pct": 10.0,
        "profit_concentration": 0.35,
        "benchmark_excess_after_cost": {
            "broad_market": 0.8,
            "tradable_universe": 0.3,
        },
        "worst_single_return_pct": -2.4,
        "paired_comparison": {
            "baseline_version": "price_action_v1",
            "candidate_version": "candidate_v2",
            "paired_unit_count": 60,
            "paired_direction_unit_count": 60,
            "paired_profit_unit_count": 60,
            "identical_independent_units": True,
            "paired_benchmark_coverage_complete": True,
            "paired_tradable_coverage_complete": True,
            "paired_actual_cost_coverage_complete": True,
            "paired_horizon_identity_complete": True,
            "paired_aggregate_outputs_finite": True,
            "accuracy_delta": 0.05,
            "accuracy_delta_ci95": [0.01, 0.09],
            "net_expectancy_delta_after_cost_pct": 0.08,
            "net_expectancy_delta_ci95": [0.02, 0.14],
            "baseline_signal_coverage": 0.80,
            "candidate_signal_coverage": 0.80,
            "improvement": True,
        },
    }
    evaluation.update(overrides)
    return evaluation


def _promote_to_validated(store):
    store.register_hypothesis(_hypothesis(source="internal_trigger"), event_id="register-v1")
    store.transition("douyin-breakout-filter", "v1", "shadow", event_id="shadow-v1")
    store.transition(
        "douyin-breakout-filter",
        "v1",
        "validated_candidate",
        event_id="validated-v1",
        evaluation=_passing_evaluation(),
    )


def test_transition_cannot_skip_directly_to_production(tmp_path):
    store = StrategyEvidenceStore(tmp_path)
    store.register_hypothesis(_hypothesis(), event_id="register-v1")

    with pytest.raises(InvalidStrategyTransition, match="hypothesis -> production"):
        store.transition(
            "douyin-breakout-filter",
            "v1",
            "production",
            event_id="illegal-production",
        )


def test_append_only_events_replay_current_state_and_deduplicate_event_id(tmp_path):
    store = StrategyEvidenceStore(tmp_path)
    store.register_hypothesis(_hypothesis(source="internal_trigger"), event_id="register-v1")
    first = store.transition(
        "douyin-breakout-filter",
        "v1",
        "shadow",
        event_id="shadow-v1",
    )
    duplicate = store.transition(
        "douyin-breakout-filter",
        "v1",
        "shadow",
        event_id="shadow-v1",
    )

    assert first == duplicate
    assert store.current_state("douyin-breakout-filter", "v1") == "shadow"
    events = [json.loads(line) for line in store.events_path.read_text(encoding="utf-8").splitlines()]
    assert [event["event_id"] for event in events] == ["register-v1", "shadow-v1"]
    assert [event["to_state"] for event in events] == ["hypothesis", "shadow"]


def test_register_hypothesis_is_idempotent_under_concurrency(tmp_path):
    worker_count = 16
    start = Barrier(worker_count)

    def register_once():
        start.wait()
        return StrategyEvidenceStore(tmp_path).register_hypothesis(
            _hypothesis(source="internal_trigger"), event_id="register-concurrent"
        )

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        events = list(executor.map(lambda _: register_once(), range(worker_count)))

    assert {event["event_id"] for event in events} == {"register-concurrent"}
    persisted = StrategyEvidenceStore(tmp_path).events_path.read_text(encoding="utf-8").splitlines()
    assert len(persisted) == 1


def test_duplicate_event_id_only_accepts_exact_same_identity_and_payload(tmp_path):
    store = StrategyEvidenceStore(tmp_path)
    original = _hypothesis(source="internal_trigger")
    store.register_hypothesis(original, event_id="shared-id")

    assert store.register_hypothesis(original, event_id="shared-id")["event_id"] == "shared-id"
    with pytest.raises(EventIdConflict, match="shared-id"):
        store.register_hypothesis(
            _hypothesis(source="internal_trigger", source_quality=0.81),
            event_id="shared-id",
        )
    with pytest.raises(EventIdConflict, match="shared-id"):
        store.register_hypothesis(
            _hypothesis(strategy_id="other-strategy", source="internal_trigger"),
            event_id="shared-id",
        )

    transition_store = StrategyEvidenceStore(tmp_path / "transition")
    _promote_to_validated(transition_store)
    with pytest.raises(EventIdConflict, match="validated-v1"):
        transition_store.transition(
            "douyin-breakout-filter",
            "v1",
            "validated_candidate",
            event_id="validated-v1",
        )
    with pytest.raises(EventIdConflict, match="validated-v1"):
        transition_store.transition(
            "douyin-breakout-filter",
            "v1",
            "validated_candidate",
            event_id="validated-v1",
            evaluation=_passing_evaluation(independent_units=61),
        )


def test_production_health_event_id_collision_cannot_return_another_strategy_result(tmp_path):
    store = StrategyEvidenceStore(tmp_path)
    _promote_to_validated(store)
    store.transition(
        "douyin-breakout-filter",
        "v1",
        "production",
        event_id="production-v1",
        approval={
            "actor_type": "human",
            "approved_by": "user:owner",
            "approved_at": "2026-07-15T10:00:00+08:00",
            "scope": "risk_filter_only",
        },
    )
    store.register_hypothesis(
        _hypothesis(strategy_id="other-strategy", source="internal_trigger"),
        event_id="health-collision",
    )
    signals = {
        "rolling_net_excess": -0.01,
        "drawdown_pct": 8.0,
        "profile_max_drawdown_pct": 10.0,
        "data_coverage": 0.95,
        "observed_version": "v1",
        "source_quality": 0.95,
        "hard_risk_conflict": False,
    }

    with pytest.raises(EventIdConflict, match="health-collision"):
        store.evaluate_production_health(
            "douyin-breakout-filter",
            "v1",
            signals,
            event_id="health-collision",
        )

    result = store.evaluate_production_health(
        "douyin-breakout-filter",
        "v1",
        signals,
        event_id="health-valid",
    )
    assert result["degraded"] is True


def test_replay_ignores_tampered_transition_that_skips_state_machine(tmp_path):
    store = StrategyEvidenceStore(tmp_path)
    store.register_hypothesis(_hypothesis(), event_id="register-v1")
    with store.events_path.open("a", encoding="utf-8") as fh:
        fh.write(
            json.dumps(
                {
                    "event_id": "tampered-production",
                    "event_type": "state_transition",
                    "strategy_id": "douyin-breakout-filter",
                    "version": "v1",
                    "from_state": "hypothesis",
                    "to_state": "production",
                }
            )
            + "\n"
        )

    assert store.current_state("douyin-breakout-filter", "v1") == "hypothesis"
    assert store.read_production("douyin-breakout-filter", "v1") is None
    assert store.health()["promotion_count"] == 0


def test_replay_rejects_registration_when_event_identity_disagrees_with_evidence(tmp_path):
    store = StrategyEvidenceStore(tmp_path)
    store.events_path.parent.mkdir(parents=True, exist_ok=True)
    mismatched_evidence = _hypothesis(strategy_id="evidence-strategy", version="v2")
    store.events_path.write_text(
        json.dumps(
            {
                "event_id": "mismatched-registration",
                "event_type": "hypothesis_registered",
                "strategy_id": "event-strategy",
                "version": "v1",
                "from_state": None,
                "to_state": "hypothesis",
                "evidence": mismatched_evidence,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    assert store.replay() == {}
    assert store.current_state("event-strategy", "v1") is None


@pytest.mark.parametrize(
    ("override", "reason"),
    [
        ({"independent_units": 59}, "independent_units"),
        ({"benchmark_coverage": {"broad_market": 1.0}}, "benchmark_coverage"),
        ({"cost_estimated": True}, "cost_estimated"),
        ({"tradable_coverage": None}, "tradable_coverage"),
        ({"actual_cost_coverage": 0.99}, "actual_cost_coverage"),
        ({"net_expectancy_after_cost_pct": 0.0}, "net_expectancy"),
        ({"profit_factor": 1.0}, "profit_factor"),
        ({"ci95_lower_bound": 0.0}, "ci95_lower_bound"),
        ({"paired_comparison": None}, "paired_comparison"),
        ({"paired_comparison": {**_passing_evaluation()["paired_comparison"], "accuracy_delta_ci95": [-0.01, 0.09]}}, "paired_accuracy_ci"),
        ({"paired_comparison": {**_passing_evaluation()["paired_comparison"], "net_expectancy_delta_ci95": [-0.01, 0.14]}}, "paired_profit_ci"),
        ({"paired_comparison": {**_passing_evaluation()["paired_comparison"], "candidate_signal_coverage": 0.50}}, "paired_candidate_coverage"),
        ({"paired_comparison": {**_passing_evaluation()["paired_comparison"], "paired_profit_unit_count": 59}}, "paired_evidence"),
        ({"paired_comparison": {**_passing_evaluation()["paired_comparison"], "paired_actual_cost_coverage_complete": False}}, "paired_evidence"),
        ({"paired_comparison": {**_passing_evaluation()["paired_comparison"], "paired_horizon_identity_complete": False}}, "paired_horizon"),
        ({"ci95_lower_bound": -0.01}, "ci95_lower_bound"),
        ({"profit_concentration": None}, "profit_concentration"),
    ],
)
def test_promotion_gate_fails_closed(override, reason, tmp_path):
    store = StrategyEvidenceStore(tmp_path)
    store.register_hypothesis(_hypothesis(source="internal_trigger"), event_id="register-v1")
    store.transition("douyin-breakout-filter", "v1", "shadow", event_id="shadow-v1")

    with pytest.raises(PromotionGateRejected, match=reason):
        store.transition(
            "douyin-breakout-filter",
            "v1",
            "validated_candidate",
            event_id="blocked-validation",
            evaluation=_passing_evaluation(**override),
        )

    assert store.current_state("douyin-breakout-filter", "v1") == "shadow"


@pytest.mark.parametrize(
    "override",
    [
        {"independent_units": float("nan")},
        {"trading_days": float("inf")},
        {"outcome_coverage": float("nan")},
        {"benchmark_coverage": {"broad_market": float("inf"), "tradable_universe": 1.0}},
        {"malformed_count": float("nan")},
        {"holdout_excess_after_cost": [float("nan"), 0.1]},
        {"walk_forward_excess_after_cost": [float("nan"), 0.2, 0.1]},
        {"ci95_lower_bound": float("-inf")},
        {"max_drawdown_pct": float("nan")},
        {"profile_max_drawdown_pct": float("inf")},
        {"profit_concentration": float("nan")},
    ],
)
def test_promotion_gate_rejects_every_nonfinite_numeric_field(override):
    decision = evaluate_promotion_gate(_passing_evaluation(**override))

    assert decision["allowed"] is False
    assert decision["reasons"]


@pytest.mark.parametrize(
    ("override", "reason"),
    [
        ({"independent_units": 60.5}, "independent_units"),
        ({"outcome_coverage": 1.1}, "outcome_coverage"),
        ({"profit_concentration": -0.1}, "profit_concentration"),
        ({"max_drawdown_pct": -0.1}, "max_drawdown"),
        ({"profile_max_drawdown_pct": 101}, "max_drawdown"),
        ({"payoff_ratio": 1e308}, "payoff_ratio"),
        ({"profit_factor": 1e308}, "profit_factor"),
        ({"worst_single_return_pct": 1e308}, "worst_single_return"),
        ({"benchmark_excess_after_cost": {"broad_market": 1e308, "tradable_universe": 0.1}}, "benchmark_excess"),
        ({"holdout_excess_after_cost": [1e308, 0.1]}, "holdout"),
        ({"paired_comparison": {**_passing_evaluation()["paired_comparison"], "paired_unit_count": 60.5}}, "paired_comparison"),
        ({"paired_comparison": {**_passing_evaluation()["paired_comparison"], "accuracy_delta": 2.0}}, "paired_accuracy"),
        ({"paired_comparison": {**_passing_evaluation()["paired_comparison"], "accuracy_delta_ci95": [0.01, 0.02]}}, "paired_accuracy"),
        ({"paired_comparison": {**_passing_evaluation()["paired_comparison"], "net_expectancy_delta_after_cost_pct": 1e308}}, "paired_profit"),
    ],
)
def test_promotion_gate_rejects_invalid_economic_domains_and_invariants(override, reason):
    decision = evaluate_promotion_gate(_passing_evaluation(**override))

    assert decision["allowed"] is False
    assert any(reason in item for item in decision["reasons"])


def test_unbounded_profit_factor_contract_round_trips_through_lifecycle_gate():
    decision = evaluate_promotion_gate(
        _passing_evaluation(
            average_loss_pct=0.0,
            payoff_ratio=None,
            payoff_ratio_unbounded=True,
            profit_factor=None,
            profit_factor_unbounded=True,
        )
    )

    assert decision == {"allowed": True, "reasons": []}


def test_low_quality_douyin_ocr_stays_hypothesis(tmp_path):
    store = StrategyEvidenceStore(tmp_path)
    store.register_hypothesis(_hypothesis(ocr_quality=0.42), event_id="register-v1")

    with pytest.raises(PromotionGateRejected, match="ocr_quality"):
        store.transition("douyin-breakout-filter", "v1", "shadow", event_id="blocked-shadow")

    assert store.current_state("douyin-breakout-filter", "v1") == "hypothesis"


def test_validated_candidate_requires_auditable_user_approval_for_production(tmp_path):
    store = StrategyEvidenceStore(tmp_path)
    _promote_to_validated(store)

    with pytest.raises(PromotionGateRejected, match="user_approval"):
        store.transition(
            "douyin-breakout-filter",
            "v1",
            "production",
            event_id="blocked-production",
        )

    assert store.current_state("douyin-breakout-filter", "v1") == "validated_candidate"
    assert store.read_production("douyin-breakout-filter", "v1") is None


def test_negative_rolling_net_excess_auto_degrades_and_reader_fails_closed(tmp_path):
    store = StrategyEvidenceStore(tmp_path)
    _promote_to_validated(store)
    store.transition(
        "douyin-breakout-filter",
        "v1",
        "production",
        event_id="production-v1",
        approval={
            "actor_type": "human",
            "approved_by": "user:owner",
            "approved_at": "2026-07-15T10:00:00+08:00",
            "scope": "risk_filter_only",
        },
    )
    assert store.read_production("douyin-breakout-filter", "v1")["state"] == "production"
    assert store.read_production("douyin-breakout-filter", "v2") is None

    result = store.evaluate_production_health(
        "douyin-breakout-filter",
        "v1",
        {
            "rolling_net_excess": -0.01,
            "drawdown_pct": 8.0,
            "profile_max_drawdown_pct": 10.0,
            "data_coverage": 0.95,
            "observed_version": "v1",
            "source_quality": 0.95,
            "hard_risk_conflict": False,
        },
        event_id="degraded-v1",
    )

    assert result["degraded"] is True
    assert "rolling_net_excess_below_zero" in result["reasons"]
    assert store.current_state("douyin-breakout-filter", "v1") == "degraded"
    assert store.read_production("douyin-breakout-filter", "v1") is None


def test_production_can_only_degrade_through_automatic_health_evaluation(tmp_path):
    store = StrategyEvidenceStore(tmp_path)
    _promote_to_validated(store)
    store.transition(
        "douyin-breakout-filter",
        "v1",
        "production",
        event_id="production-v1",
        approval={
            "actor_type": "human",
            "approved_by": "user:owner",
            "approved_at": "2026-07-15T10:00:00+08:00",
            "scope": "risk_filter_only",
        },
    )

    with pytest.raises(InvalidStrategyTransition, match="automatic health evaluation"):
        store.transition(
            "douyin-breakout-filter",
            "v1",
            "degraded",
            event_id="manual-degradation",
        )


@pytest.mark.parametrize(
    "approval",
    [
        {
            "approved_by": "user:owner",
            "approved_at": "2026-07-15T10:00:00+08:00",
            "scope": "risk_filter_only",
        },
        {
            "actor_type": "bot",
            "approved_by": "user:owner",
            "approved_at": "2026-07-15T10:00:00+08:00",
            "scope": "risk_filter_only",
        },
        {
            "actor_type": "human",
            "approved_by": "deploy-service-owner",
            "approved_at": "2026-07-15T10:00:00+08:00",
            "scope": "risk_filter_only",
        },
        {
            "actor_type": "human",
            "approved_by": "system:operator",
            "approved_at": "2026-07-15T10:00:00+08:00",
            "scope": "risk_filter_only",
        },
        {
            "actor_type": "human",
            "approved_by": "automation-owner",
            "approved_at": "2026-07-15T10:00:00+08:00",
            "scope": "risk_filter_only",
        },
        {
            "actor_type": "human",
            "approved_by": "risk-bot-owner",
            "approved_at": "2026-07-15T10:00:00+08:00",
            "scope": "risk_filter_only",
        },
        {
            "actor_type": "human",
            "approved_by": "user:owner",
            "approved_at": "2026-07-15T10:00:00",
            "scope": "risk_filter_only",
        },
    ],
)
def test_production_approval_requires_provably_human_actor_and_aware_timestamp(approval, tmp_path):
    store = StrategyEvidenceStore(tmp_path)
    _promote_to_validated(store)

    with pytest.raises(PromotionGateRejected, match="user_approval"):
        store.transition(
            "douyin-breakout-filter",
            "v1",
            "production",
            event_id="blocked-nonhuman-approval",
            approval=approval,
        )


def test_risk_card_uses_plain_chinese_and_never_implies_automatic_execution(tmp_path):
    store = StrategyEvidenceStore(tmp_path)
    _promote_to_validated(store)

    card = store.build_risk_card("douyin-breakout-filter", "v1")
    rendered = json.dumps(card, ensure_ascii=False)

    assert card["default_recommendation"] == "继续观察"
    assert card["evidence"]["independent_units"] == 60
    assert card["evidence"]["trading_days"] == 20
    assert set(card["benchmark_after_cost"]) == {"broad_market", "tradable_universe"}
    assert card["worst_single_return_pct"] == -2.4
    assert card["max_drawdown_pct"] == 8.0
    assert "不自动下单" in rendered
    assert "不放宽硬止损" in rendered
    assert "继续观察" in card["system_conclusion"]
    assert not {"稳赚", "胜率高", "大概率盈利"} & {term for term in ("稳赚", "胜率高", "大概率盈利") if term in rendered}


def test_risk_card_marks_missing_benchmark_results_instead_of_leaving_blank(tmp_path):
    store = StrategyEvidenceStore(tmp_path)
    store.register_hypothesis(_hypothesis(), event_id="register-v1")

    card = store.build_risk_card("douyin-breakout-filter", "v1")

    assert card["benchmark_after_cost"] == {
        "broad_market": "未提供",
        "tradable_universe": "未提供",
    }


def test_risk_card_sanitizes_misleading_words_from_every_user_controlled_field(tmp_path):
    store = StrategyEvidenceStore(tmp_path)
    strategy_id = "稳赚-胜率高-filter"
    version = "大概率盈利-v1"
    store.register_hypothesis(
        _hypothesis(
            strategy_id=strategy_id,
            version=version,
            source="internal_trigger",
            production_impact="稳赚后改变过滤条件",
        ),
        event_id="register-banned-words",
    )
    store.transition(strategy_id, version, "shadow", event_id="shadow-banned-words")
    store.transition(
        strategy_id,
        version,
        "validated_candidate",
        event_id="validated-banned-words",
        evaluation=_passing_evaluation(),
    )

    rendered = json.dumps(store.build_risk_card(strategy_id, version), ensure_ascii=False)

    for misleading in ("稳赚", "胜率高", "大概率盈利"):
        assert misleading not in rendered


def test_health_reports_gate_coverage_and_audit_diagnostics(tmp_path):
    store = StrategyEvidenceStore(tmp_path)
    _promote_to_validated(store)
    with store.events_path.open("a", encoding="utf-8") as fh:
        fh.write("{malformed-json}\n")

    health = store.health()

    assert health["benchmark_coverage"] == {
        "broad_market": 1.0,
        "tradable_universe": 1.0,
    }
    assert health["outcome_coverage"] == 0.90
    assert health["pending_aging"]["count"] == 1
    assert health["malformed_count"] == 1
    assert health["duplicate_terminal_count"] == 0
    assert health["promotion_count"] == 2
    assert health["degradation_count"] == 0
    assert health["last_evaluation_duration_ms"] >= 0
