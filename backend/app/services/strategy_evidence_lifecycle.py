"""Fail-closed strategy evidence lifecycle backed by an append-only audit log."""
from __future__ import annotations

import json
import math
import os
import fcntl
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from threading import RLock
from time import perf_counter
from typing import Any

from app.config import PROJECT_ROOT


FEATURE_CLASSES = {
    "eligibility",
    "risk_filter",
    "alpha_signal",
    "execution_feature",
}
HYPOTHESIS_REQUIRED_FIELDS = {
    "strategy_id",
    "version",
    "source",
    "source_quality",
    "falsifiable_claim",
    "feature_class",
    "data_cutoff",
    "failure_condition",
    "production_impact",
}
MISLEADING_RISK_TERMS = ("稳赚", "胜率高", "大概率盈利")

ALLOWED_TRANSITIONS = {
    "hypothesis": {"shadow", "rejected"},
    "shadow": {"validated_candidate", "rejected"},
    "validated_candidate": {"production", "rejected"},
    "production": {"degraded"},
    "degraded": {"shadow", "rejected"},
    "rejected": set(),
}
_PROCESS_EVIDENCE_LOCK = RLock()


@contextmanager
def _evidence_lock(events_path: Path):
    lock_path = events_path.with_name(f".{events_path.name}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with _PROCESS_EVIDENCE_LOCK, lock_path.open("a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _locked_mutation(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with _evidence_lock(self.events_path):
            return method(self, *args, **kwargs)

    return wrapped


class StrategyEvidenceError(ValueError):
    """Base error for invalid evidence and lifecycle operations."""


class InvalidStrategyEvidence(StrategyEvidenceError):
    """Raised when evidence is incomplete or malformed."""


class InvalidStrategyTransition(StrategyEvidenceError):
    """Raised when a lifecycle transition is not allowed."""


class PromotionGateRejected(StrategyEvidenceError):
    """Raised when a fail-closed promotion gate rejects advancement."""


class EventIdConflict(StrategyEvidenceError):
    """Raised when an event ID is reused for different audited content."""


def default_strategy_evidence_root() -> Path:
    prediction_root = Path(
        os.environ.get(
            "CONGXI_PREDICTION_LAB_ROOT",
            os.path.abspath(os.path.join(PROJECT_ROOT, "..", "data", "prediction_lab")),
        )
    )
    return prediction_root / "strategy_evidence"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _nonempty(value: Any) -> bool:
    return value is not None and str(value).strip() != ""


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _integer(value: Any, *, minimum: int = 0) -> int | None:
    numeric = _number(value)
    if numeric is None or not numeric.is_integer() or numeric < minimum:
        return None
    return int(numeric)


def _bounded(value: Any, lower: float, upper: float) -> float | None:
    numeric = _number(value)
    return numeric if numeric is not None and lower <= numeric <= upper else None


def _ordered_ci_contains(
    value: Any,
    point: float | None,
    *,
    lower_domain: float,
    upper_domain: float,
) -> list[float] | None:
    if not isinstance(value, list) or len(value) != 2 or point is None:
        return None
    parsed = [_bounded(item, lower_domain, upper_domain) for item in value]
    if any(item is None for item in parsed):
        return None
    lower, upper = parsed
    return parsed if lower <= point <= upper else None


def _quality_is_sufficient(value: Any) -> bool:
    numeric = _number(value)
    if numeric is not None:
        return numeric >= 0.8
    return str(value or "").strip().lower() in {"high", "verified", "reliable"}


def _sanitize_risk_value(value: Any) -> Any:
    if isinstance(value, str):
        sanitized = value
        for misleading in MISLEADING_RISK_TERMS:
            sanitized = sanitized.replace(misleading, "收益未经证实")
        return sanitized
    if isinstance(value, list):
        return [_sanitize_risk_value(item) for item in value]
    if isinstance(value, dict):
        return {
            _sanitize_risk_value(key): _sanitize_risk_value(item)
            for key, item in value.items()
        }
    return value


def evaluate_promotion_gate(evaluation: dict[str, Any] | None) -> dict[str, Any]:
    """Evaluate the v1 shadow-to-validated gate; missing data always blocks."""
    data = evaluation if isinstance(evaluation, dict) else {}
    reasons: list[str] = []

    independent_units = _integer(data.get("independent_units"))
    if independent_units is None or independent_units < 60:
        reasons.append("independent_units_below_60")

    trading_days = _integer(data.get("trading_days"))
    if trading_days is None or trading_days < 20:
        reasons.append("trading_days_below_20")

    outcome_coverage = _bounded(data.get("outcome_coverage"), 0, 1)
    if outcome_coverage is None or outcome_coverage < 0.90:
        reasons.append("outcome_coverage_below_90pct")

    independent_unit_coverage = _bounded(
        data.get("verified_independent_unit_coverage"), 0, 1
    )
    if independent_unit_coverage is None or independent_unit_coverage < 0.90:
        reasons.append("verified_independent_unit_coverage_below_90pct")

    benchmark_coverage = data.get("benchmark_coverage")
    if not isinstance(benchmark_coverage, dict):
        reasons.append("benchmark_coverage_missing")
    else:
        required_benchmarks = {"broad_market", "tradable_universe"}
        if set(benchmark_coverage) != required_benchmarks or any(
            _number(benchmark_coverage.get(name)) != 1.0 for name in required_benchmarks
        ):
            reasons.append("benchmark_coverage_not_100pct_for_both")

    malformed_count = _integer(data.get("malformed_count"))
    if malformed_count is None or malformed_count != 0:
        reasons.append("malformed_count_not_zero")

    if data.get("cost_estimated") is not False:
        reasons.append("cost_estimated_must_be_false")

    tradable_coverage = _bounded(data.get("tradable_coverage"), 0, 1)
    if tradable_coverage != 1.0:
        reasons.append("tradable_coverage_not_100pct")

    actual_cost_coverage = _bounded(data.get("actual_cost_coverage"), 0, 1)
    if actual_cost_coverage != 1.0:
        reasons.append("actual_cost_coverage_not_100pct")

    buy_precision = _bounded(data.get("buy_signal_precision"), 0, 1)
    if buy_precision is None:
        reasons.append("buy_signal_precision_invalid")

    abstention_rate = _bounded(data.get("abstention_rate"), 0, 1)
    signal_coverage = _bounded(data.get("signal_coverage"), 0, 1)
    if (
        abstention_rate is None
        or signal_coverage is None
        or signal_coverage < 0.5
        or not math.isclose(abstention_rate + signal_coverage, 1.0, abs_tol=0.001)
    ):
        reasons.append("signal_coverage_or_abstention_invalid")

    average_win = _bounded(data.get("average_win_pct"), -100, 1_000_000)
    average_loss = _bounded(data.get("average_loss_pct"), -100, 1_000_000)
    payoff_ratio = _bounded(data.get("payoff_ratio"), 0, 1_000_000)
    payoff_unbounded = data.get("payoff_ratio_unbounded") is True
    profit_factor_unbounded = data.get("profit_factor_unbounded") is True
    if average_win is None or average_win <= 0:
        reasons.append("average_win_must_be_positive")
    if average_loss is None or (average_loss >= 0 and not profit_factor_unbounded):
        reasons.append("average_loss_must_be_negative")
    if (payoff_ratio is None or payoff_ratio <= 0) and not (
        payoff_unbounded and profit_factor_unbounded
    ):
        reasons.append("payoff_ratio_must_be_positive")

    net_expectancy = _bounded(data.get("net_expectancy_after_cost_pct"), -100, 1_000_000)
    if net_expectancy is None or net_expectancy <= 0:
        reasons.append("net_expectancy_after_cost_must_be_positive")

    profit_factor = _bounded(data.get("profit_factor"), 0, 1_000_000)
    if (profit_factor is None or profit_factor <= 1) and not profit_factor_unbounded:
        reasons.append("profit_factor_must_exceed_one")
    if profit_factor_unbounded and not (
        data.get("profit_factor") is None
        and average_loss == 0
        and data.get("payoff_ratio") is None
        and payoff_unbounded
    ):
        reasons.append("unbounded_profit_factor_contract_invalid")

    holdouts = data.get("holdout_excess_after_cost")
    if (
        not isinstance(holdouts, list)
        or len(holdouts) < 2
        or any(
            _bounded(item, -100, 1_000_000) is None
            or _bounded(item, -100, 1_000_000) <= 0
            for item in holdouts[:2]
        )
    ):
        reasons.append("holdout_excess_after_cost_requires_two_positive_results")

    walk_forward = data.get("walk_forward_excess_after_cost")
    if not isinstance(walk_forward, list) or len(walk_forward) < 3:
        reasons.append("walk_forward_requires_three_segments")
    else:
        parsed_walk_forward = [_bounded(item, -100, 1_000_000) for item in walk_forward]
        if any(item is None for item in parsed_walk_forward) or sum(
            item > 0 for item in parsed_walk_forward if item is not None
        ) < 2:
            reasons.append("walk_forward_requires_two_positive_segments")

    ci_lower = _bounded(data.get("ci95_lower_bound"), -100, 1_000_000)
    if ci_lower is None or ci_lower <= 0:
        reasons.append("ci95_lower_bound_below_zero")

    drawdown = _bounded(data.get("max_drawdown_pct"), 0, 100)
    drawdown_limit = _bounded(data.get("profile_max_drawdown_pct"), 0, 100)
    if drawdown is None or drawdown_limit is None or drawdown > drawdown_limit:
        reasons.append("max_drawdown_exceeds_profile_limit")

    concentration = _bounded(data.get("profit_concentration"), 0, 1)
    if concentration is None or concentration > 0.35:
        reasons.append("profit_concentration_above_35pct")

    worst_single_return = _bounded(data.get("worst_single_return_pct"), -100, 1_000_000)
    if worst_single_return is None:
        reasons.append("worst_single_return_invalid")

    benchmark_excess = data.get("benchmark_excess_after_cost")
    if (
        not isinstance(benchmark_excess, dict)
        or set(benchmark_excess) != {"broad_market", "tradable_universe"}
        or any(
            _bounded(benchmark_excess.get(name), -100, 1_000_000) is None
            for name in ("broad_market", "tradable_universe")
        )
    ):
        reasons.append("benchmark_excess_after_cost_invalid")

    paired = data.get("paired_comparison")
    if not isinstance(paired, dict):
        reasons.append("paired_comparison_missing")
    else:
        baseline_version = str(paired.get("baseline_version") or "")
        candidate_version = str(paired.get("candidate_version") or "")
        paired_units = _integer(paired.get("paired_unit_count"))
        paired_direction_units = _integer(paired.get("paired_direction_unit_count"))
        paired_profit_units = _integer(paired.get("paired_profit_unit_count"))
        if (
            baseline_version != "price_action_v1"
            or not candidate_version
            or candidate_version == baseline_version
            or paired_units is None
            or paired_units < 60
            or paired.get("identical_independent_units") is not True
        ):
            reasons.append("paired_comparison_not_identical_60_units")
        if (
            paired_units is None
            or paired_direction_units != paired_units
            or paired_profit_units != paired_units
            or paired.get("paired_benchmark_coverage_complete") is not True
            or paired.get("paired_tradable_coverage_complete") is not True
            or paired.get("paired_actual_cost_coverage_complete") is not True
            or paired.get("paired_aggregate_outputs_finite") is not True
        ):
            reasons.append("paired_evidence_incomplete")
        if paired.get("paired_horizon_identity_complete") is not True:
            reasons.append("paired_horizon_identity_incomplete")

        accuracy_delta = _bounded(paired.get("accuracy_delta"), -1, 1)
        accuracy_ci = paired.get("accuracy_delta_ci95")
        parsed_accuracy_ci = _ordered_ci_contains(
            accuracy_ci,
            accuracy_delta,
            lower_domain=-1,
            upper_domain=1,
        )
        if (
            accuracy_delta is None
            or accuracy_delta <= 0
            or parsed_accuracy_ci is None
            or parsed_accuracy_ci[0] <= 0
        ):
            reasons.append("paired_accuracy_ci_must_exclude_zero")

        profit_delta = _bounded(
            paired.get("net_expectancy_delta_after_cost_pct"), -1_000_000, 1_000_000
        )
        profit_ci = paired.get("net_expectancy_delta_ci95")
        parsed_profit_ci = _ordered_ci_contains(
            profit_ci,
            profit_delta,
            lower_domain=-1_000_000,
            upper_domain=1_000_000,
        )
        if (
            profit_delta is None
            or profit_delta <= 0
            or parsed_profit_ci is None
            or parsed_profit_ci[0] <= 0
        ):
            reasons.append("paired_profit_ci_must_exclude_zero")

        baseline_coverage = _bounded(paired.get("baseline_signal_coverage"), 0, 1)
        candidate_coverage = _bounded(paired.get("candidate_signal_coverage"), 0, 1)
        if (
            baseline_coverage is None
            or candidate_coverage is None
            or candidate_coverage < 0.5
            or candidate_coverage < baseline_coverage
        ):
            reasons.append("paired_candidate_coverage_collapsed")
        if paired.get("improvement") is not True:
            reasons.append("paired_comparison_improvement_not_proven")

    return {"allowed": not reasons, "reasons": reasons}


def _source_quality_gate(evidence: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    if not _quality_is_sufficient(evidence.get("source_quality")):
        reasons.append("source_quality_too_low")
    source = str(evidence.get("source") or "").strip().lower()
    if source in {"douyin", "short_video", "short-video"} and not _quality_is_sufficient(
        evidence.get("ocr_quality")
    ):
        reasons.append("ocr_quality_too_low")
    return reasons


def _user_approval_reasons(approval: dict[str, Any] | None) -> list[str]:
    data = approval if isinstance(approval, dict) else {}
    missing = [
        name
        for name in ("actor_type", "approved_by", "approved_at", "scope")
        if not _nonempty(data.get(name))
    ]
    reasons = [f"user_approval_missing_{name}" for name in missing]
    if str(data.get("actor_type") or "").strip().lower() != "human":
        reasons.append("user_approval_actor_type_must_be_human")
    approved_by = str(data.get("approved_by") or "").strip().lower()
    if any(marker in approved_by for marker in ("bot", "system", "auto", "service")):
        reasons.append("user_approval_approved_by_must_be_human_identity")
    approved_at = str(data.get("approved_at") or "").strip()
    if approved_at:
        try:
            parsed_approval_time = datetime.fromisoformat(approved_at.replace("Z", "+00:00"))
        except ValueError:
            reasons.append("user_approval_approved_at_invalid")
        else:
            if parsed_approval_time.tzinfo is None or parsed_approval_time.utcoffset() is None:
                reasons.append("user_approval_approved_at_timezone_required")
    return reasons


def _validate_hypothesis(payload: dict[str, Any]) -> None:
    missing = sorted(field for field in HYPOTHESIS_REQUIRED_FIELDS if not _nonempty(payload.get(field)))
    source = str(payload.get("source") or "").strip().lower()
    if source in {"douyin", "short_video", "short-video"} and not _nonempty(payload.get("ocr_quality")):
        missing.append("ocr_quality")
    if missing:
        raise InvalidStrategyEvidence(f"missing hypothesis fields: {', '.join(sorted(set(missing)))}")
    if payload.get("feature_class") not in FEATURE_CLASSES:
        raise InvalidStrategyEvidence("invalid feature_class")


class StrategyEvidenceStore:
    """Append-only strategy audit store with deterministic replay semantics."""

    def __init__(self, root: str | Path | None = None):
        self.root = Path(root) if root is not None else default_strategy_evidence_root()
        self.events_path = self.root / "events.jsonl"

    def _read_events(self) -> list[dict[str, Any]]:
        return self._read_events_with_diagnostics()["events"]

    def _read_events_with_diagnostics(self) -> dict[str, Any]:
        if not self.events_path.exists():
            return {"events": [], "malformed_count": 0}
        events: list[dict[str, Any]] = []
        malformed_count = 0
        for line in self.events_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                malformed_count += 1
                continue
            if isinstance(payload, dict):
                events.append(payload)
            else:
                malformed_count += 1
        return {"events": events, "malformed_count": malformed_count}

    def _event_by_id(self, event_id: str) -> dict[str, Any] | None:
        return next((event for event in self._read_events() if event.get("event_id") == event_id), None)

    def _matching_existing_event(
        self,
        event_id: str,
        *,
        event_type: str,
        strategy_id: str,
        version: str,
        payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        existing = self._event_by_id(event_id)
        if existing is None:
            return None
        expected_identity = {
            "event_type": event_type,
            "strategy_id": str(strategy_id),
            "version": str(version),
        }
        actual_identity = {key: existing.get(key) for key in expected_identity}
        if event_type == "hypothesis_registered":
            actual_payload = {"evidence": existing.get("evidence")}
        elif event_type == "state_transition":
            actual_payload = {"to_state": existing.get("to_state")}
            for optional_field in ("evaluation", "user_approval"):
                if optional_field in existing:
                    actual_payload[optional_field] = existing[optional_field]
        elif event_type == "automatic_degradation":
            actual_payload = {"signals": existing.get("signals")}
        else:
            actual_payload = {}
        if actual_identity != expected_identity or actual_payload != payload:
            raise EventIdConflict(f"event_id conflict: {event_id}")
        return existing

    def _append(self, event: dict[str, Any]) -> dict[str, Any]:
        existing = self._event_by_id(str(event["event_id"]))
        if existing is not None:
            if existing != event:
                raise EventIdConflict(f"event_id conflict: {event['event_id']}")
            return existing
        self.events_path.parent.mkdir(parents=True, exist_ok=True)
        with self.events_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        return event

    @_locked_mutation
    def register_hypothesis(
        self,
        payload: dict[str, Any],
        *,
        event_id: str,
        occurred_at: str | None = None,
    ) -> dict[str, Any]:
        existing = self._matching_existing_event(
            event_id,
            event_type="hypothesis_registered",
            strategy_id=str(payload.get("strategy_id") or ""),
            version=str(payload.get("version") or ""),
            payload={"evidence": dict(payload)},
        )
        if existing is not None:
            return existing
        _validate_hypothesis(payload)
        if self.current_state(str(payload["strategy_id"]), str(payload["version"])) is not None:
            raise InvalidStrategyEvidence("strategy version already registered")
        event = {
            "event_id": event_id,
            "event_type": "hypothesis_registered",
            "occurred_at": occurred_at or _now(),
            "strategy_id": str(payload["strategy_id"]),
            "version": str(payload["version"]),
            "from_state": None,
            "to_state": "hypothesis",
            "evidence": dict(payload),
        }
        return self._append(event)

    def replay(self) -> dict[tuple[str, str], dict[str, Any]]:
        snapshots: dict[tuple[str, str], dict[str, Any]] = {}
        seen_event_ids: set[str] = set()
        for event in self._read_events():
            event_id = str(event.get("event_id") or "")
            if not event_id or event_id in seen_event_ids:
                continue
            seen_event_ids.add(event_id)
            strategy_id = str(event.get("strategy_id") or "")
            version = str(event.get("version") or "")
            to_state = str(event.get("to_state") or "")
            if not strategy_id or not version or to_state not in ALLOWED_TRANSITIONS:
                continue
            key = (strategy_id, version)
            if event.get("event_type") == "hypothesis_registered":
                if key in snapshots or event.get("from_state") is not None or to_state != "hypothesis":
                    continue
                evidence = dict(event.get("evidence") or {})
                try:
                    _validate_hypothesis(evidence)
                except InvalidStrategyEvidence:
                    continue
                if (
                    str(evidence.get("strategy_id") or "") != strategy_id
                    or str(evidence.get("version") or "") != version
                ):
                    continue
                snapshots[key] = {
                    "strategy_id": strategy_id,
                    "version": version,
                    "state": "hypothesis",
                    "evidence": evidence,
                    "events": [event],
                }
                continue

            snapshot = snapshots.get(key)
            if snapshot is None or event.get("event_type") not in {"state_transition", "automatic_degradation"}:
                continue
            current = str(snapshot.get("state") or "")
            if event.get("from_state") != current or to_state not in ALLOWED_TRANSITIONS.get(current, set()):
                continue
            if event.get("event_type") == "automatic_degradation" and to_state != "degraded":
                continue
            if current == "hypothesis" and to_state == "shadow" and _source_quality_gate(snapshot["evidence"]):
                continue
            if current == "shadow" and to_state == "validated_candidate":
                if not evaluate_promotion_gate(event.get("evaluation"))["allowed"]:
                    continue
            if current == "validated_candidate" and to_state == "production":
                if _user_approval_reasons(event.get("user_approval")):
                    continue
            snapshot["state"] = to_state
            snapshot["events"].append(event)
        return snapshots

    def snapshot(self, strategy_id: str, version: str) -> dict[str, Any] | None:
        return self.replay().get((str(strategy_id), str(version)))

    def current_state(self, strategy_id: str, version: str) -> str | None:
        snapshot = self.snapshot(strategy_id, version)
        return str(snapshot["state"]) if snapshot is not None else None

    @_locked_mutation
    def transition(
        self,
        strategy_id: str,
        version: str,
        to_state: str,
        *,
        event_id: str,
        occurred_at: str | None = None,
        evaluation: dict[str, Any] | None = None,
        approval: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        started = perf_counter()
        transition_payload: dict[str, Any] = {"to_state": to_state}
        if evaluation is not None:
            transition_payload["evaluation"] = dict(evaluation)
        if approval is not None:
            transition_payload["user_approval"] = dict(approval)
        existing = self._matching_existing_event(
            event_id,
            event_type="state_transition",
            strategy_id=strategy_id,
            version=version,
            payload=transition_payload,
        )
        if existing is not None:
            return existing
        current = self.current_state(strategy_id, version)
        if current is None:
            raise InvalidStrategyTransition("strategy version is not registered")
        if current == "production" and to_state == "degraded":
            raise InvalidStrategyTransition("production degradation requires automatic health evaluation")
        if to_state not in ALLOWED_TRANSITIONS.get(current, set()):
            raise InvalidStrategyTransition(f"invalid transition: {current} -> {to_state}")
        snapshot = self.snapshot(strategy_id, version) or {}
        if current == "hypothesis" and to_state == "shadow":
            reasons = _source_quality_gate(dict(snapshot.get("evidence") or {}))
            if reasons:
                raise PromotionGateRejected("; ".join(reasons))
        if current == "shadow" and to_state == "validated_candidate":
            decision = evaluate_promotion_gate(evaluation)
            if not decision["allowed"]:
                raise PromotionGateRejected("; ".join(decision["reasons"]))
        if current == "validated_candidate" and to_state == "production":
            reasons = _user_approval_reasons(approval)
            if reasons:
                raise PromotionGateRejected("; ".join(reasons))
        event = {
            "event_id": event_id,
            "event_type": "state_transition",
            "occurred_at": occurred_at or _now(),
            "strategy_id": str(strategy_id),
            "version": str(version),
            "from_state": current,
            "to_state": to_state,
        }
        if evaluation is not None:
            event["evaluation"] = dict(evaluation)
            event["evaluation_duration_ms"] = round((perf_counter() - started) * 1000, 3)
        if approval is not None:
            event["user_approval"] = dict(approval)
        return self._append(event)

    def read_production(self, strategy_id: str, version: str) -> dict[str, Any] | None:
        """Return only an exact-version production snapshot."""
        snapshot = self.snapshot(strategy_id, version)
        if snapshot is None or snapshot.get("state") != "production":
            return None
        if snapshot.get("version") != str(version):
            return None
        return snapshot

    @_locked_mutation
    def evaluate_production_health(
        self,
        strategy_id: str,
        version: str,
        signals: dict[str, Any] | None,
        *,
        event_id: str,
        occurred_at: str | None = None,
    ) -> dict[str, Any]:
        """Automatically degrade production when any safety signal fails closed."""
        data = signals if isinstance(signals, dict) else {}
        existing = self._matching_existing_event(
            event_id,
            event_type="automatic_degradation",
            strategy_id=strategy_id,
            version=version,
            payload={"signals": dict(data)},
        )
        if existing is not None:
            return {
                "degraded": existing.get("to_state") == "degraded",
                "reasons": list(existing.get("reasons") or []),
                "event": existing,
            }
        if self.current_state(strategy_id, version) != "production":
            raise InvalidStrategyTransition("production health can only evaluate production")

        started = perf_counter()
        required = {
            "rolling_net_excess",
            "drawdown_pct",
            "profile_max_drawdown_pct",
            "data_coverage",
            "observed_version",
            "source_quality",
            "hard_risk_conflict",
        }
        reasons = [f"missing_degradation_signal:{name}" for name in sorted(required - set(data))]

        rolling_net_excess = _number(data.get("rolling_net_excess"))
        if "rolling_net_excess" in data and (rolling_net_excess is None or rolling_net_excess < 0):
            reasons.append("rolling_net_excess_below_zero")

        drawdown = _number(data.get("drawdown_pct"))
        drawdown_limit = _number(data.get("profile_max_drawdown_pct"))
        if "drawdown_pct" in data and "profile_max_drawdown_pct" in data and (
            drawdown is None or drawdown_limit is None or drawdown > drawdown_limit
        ):
            reasons.append("drawdown_exceeds_profile_limit")

        data_coverage = _number(data.get("data_coverage"))
        if "data_coverage" in data and (data_coverage is None or data_coverage < 0.90):
            reasons.append("data_coverage_below_90pct")

        if "observed_version" in data and str(data.get("observed_version") or "") != str(version):
            reasons.append("version_drift")

        if "source_quality" in data and not _quality_is_sufficient(data.get("source_quality")):
            reasons.append("source_quality_declined")

        if "hard_risk_conflict" in data and data.get("hard_risk_conflict") is not False:
            reasons.append("hard_risk_conflict")

        duration_ms = round((perf_counter() - started) * 1000, 3)
        if not reasons:
            return {"degraded": False, "reasons": [], "duration_ms": duration_ms}

        event = {
            "event_id": event_id,
            "event_type": "automatic_degradation",
            "occurred_at": occurred_at or _now(),
            "strategy_id": str(strategy_id),
            "version": str(version),
            "from_state": "production",
            "to_state": "degraded",
            "reasons": reasons,
            "signals": dict(data),
            "evaluation_duration_ms": duration_ms,
        }
        persisted = self._append(event)
        return {"degraded": True, "reasons": reasons, "event": persisted, "duration_ms": duration_ms}

    def build_risk_card(self, strategy_id: str, version: str) -> dict[str, Any]:
        """Build a plain-Chinese, non-execution risk acknowledgement card."""
        snapshot = self.snapshot(strategy_id, version)
        if snapshot is None:
            raise InvalidStrategyEvidence("strategy version is not registered")
        evaluation: dict[str, Any] = {}
        for event in reversed(snapshot.get("events") or []):
            if isinstance(event.get("evaluation"), dict):
                evaluation = dict(event["evaluation"])
                break

        conclusions = {
            "hypothesis": "目前只是研究假设，证据不足，建议继续观察。",
            "shadow": "目前只在影子环境验证，不能影响报告动作，建议继续观察。",
            "validated_candidate": "门槛已通过，但仍需人工批准；当前建议继续观察。",
            "production": "已人工批准进入生产，但仍受硬风控与自动停用约束，建议继续观察。",
            "degraded": "触发安全条件，已自动降级并停止生产影响，建议继续观察。",
            "rejected": "该假设已被拒绝，不再进入生产评估，建议继续观察。",
        }
        production_impact = str((snapshot.get("evidence") or {}).get("production_impact") or "未提供")
        for misleading in MISLEADING_RISK_TERMS:
            production_impact = production_impact.replace(misleading, "收益未经证实")

        card = {
            "strategy_id": str(strategy_id),
            "version": str(version),
            "state": snapshot.get("state"),
            "default_recommendation": "继续观察",
            "system_conclusion": conclusions.get(str(snapshot.get("state")), "状态不明确，建议继续观察。"),
            "evidence": {
                "independent_units": evaluation.get("independent_units", "未提供"),
                "trading_days": evaluation.get("trading_days", "未提供"),
            },
            "benchmark_after_cost": {
                "broad_market": (evaluation.get("benchmark_excess_after_cost") or {}).get(
                    "broad_market", "未提供"
                ),
                "tradable_universe": (evaluation.get("benchmark_excess_after_cost") or {}).get(
                    "tradable_universe", "未提供"
                ),
            },
            "worst_single_return_pct": evaluation.get("worst_single_return_pct", "未提供"),
            "max_drawdown_pct": evaluation.get("max_drawdown_pct", "未提供"),
            "will_change": production_impact,
            "will_not_change": [
                "不自动下单",
                "不放宽硬止损",
                "不把研究线索直接写成买卖动作",
            ],
            "automatic_disable_conditions": [
                "滚动成本后净超额收益小于零",
                "最大回撤超过策略档位上限",
                "数据覆盖不足或版本不一致",
                "来源质量下降或触发硬风控冲突",
            ],
        }
        return _sanitize_risk_value(card)

    def health(self) -> dict[str, Any]:
        """Summarize lifecycle observability from the local append-only ledger."""
        diagnostics = self._read_events_with_diagnostics()
        events = diagnostics["events"]
        snapshots = self.replay()
        valid_events = [event for snapshot in snapshots.values() for event in snapshot.get("events") or []]

        latest_evaluation: dict[str, Any] = {}
        last_duration = 0.0
        for event in valid_events:
            if isinstance(event.get("evaluation"), dict):
                latest_evaluation = dict(event["evaluation"])
            duration = _number(event.get("evaluation_duration_ms"))
            if duration is not None:
                last_duration = duration

        now = datetime.now(timezone.utc)
        pending_ages: list[int] = []
        pending_states = {"hypothesis", "shadow", "validated_candidate", "degraded"}
        for snapshot in snapshots.values():
            if snapshot.get("state") not in pending_states:
                continue
            registered = next(
                (
                    event.get("occurred_at")
                    for event in snapshot.get("events") or []
                    if event.get("event_type") == "hypothesis_registered"
                ),
                None,
            )
            try:
                created_at = datetime.fromisoformat(str(registered).replace("Z", "+00:00"))
                if created_at.tzinfo is None:
                    created_at = created_at.replace(tzinfo=timezone.utc)
                pending_ages.append(max(0, (now - created_at.astimezone(timezone.utc)).days))
            except (TypeError, ValueError):
                pending_ages.append(0)

        terminal_counts: dict[tuple[str, str, str], int] = {}
        for event in events:
            if event.get("to_state") != "rejected":
                continue
            key = (
                str(event.get("strategy_id") or ""),
                str(event.get("version") or ""),
                "rejected",
            )
            terminal_counts[key] = terminal_counts.get(key, 0) + 1

        return {
            "benchmark_coverage": dict(latest_evaluation.get("benchmark_coverage") or {}),
            "outcome_coverage": latest_evaluation.get("outcome_coverage", 0.0),
            "pending_aging": {
                "count": len(pending_ages),
                "oldest_days": max(pending_ages, default=0),
            },
            "malformed_count": diagnostics["malformed_count"],
            "duplicate_terminal_count": sum(max(0, count - 1) for count in terminal_counts.values()),
            "promotion_count": sum(
                event.get("to_state") in {"shadow", "validated_candidate", "production"}
                for event in valid_events
            ),
            "degradation_count": sum(event.get("to_state") == "degraded" for event in valid_events),
            "last_evaluation_duration_ms": last_duration,
        }
