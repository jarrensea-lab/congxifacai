"""Two-pool membership policy shared by reports and broker-watchlist sync."""
from __future__ import annotations

from typing import Any, Mapping


SHORT_TERM_POOL = "short_term"
MID_LONG_TERM_POOL = "mid_long_term"
POOL_KINDS = frozenset({SHORT_TERM_POOL, MID_LONG_TERM_POOL})

SHORT_ENTRY_SCORE = 55.0
SHORT_RETENTION_SCORE = 50.0
MID_LONG_ENTRY_SCORE = 60.0
MID_LONG_RETENTION_SCORE = 55.0

_MID_LONG_ALIASES = frozenset(
    {
        "long",
        "long_term",
        "mid_long",
        "mid_long_term",
        "medium_long_term",
    }
)
_LONG_LIFECYCLE_STATUSES = frozenset(
    {
        "long_research",
        "long_watch",
        "accumulation_zone",
        "tactical_watch",
        "thesis_review",
        "exit_candidate",
    }
)


def _number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def infer_pool_kind(
    item: Mapping[str, Any],
    *,
    long_thesis: Mapping[str, Any] | None = None,
) -> str:
    """Map every formal target into exactly one user-facing pool."""
    explicit = str(item.get("pool_kind") or "").strip().lower()
    if explicit in _MID_LONG_ALIASES:
        return MID_LONG_TERM_POOL
    if explicit == SHORT_TERM_POOL:
        return SHORT_TERM_POOL
    if isinstance(long_thesis, Mapping) and long_thesis:
        return MID_LONG_TERM_POOL
    status = str(item.get("status") or "").strip().lower()
    scoring = (
        item.get("scoring_decision")
        if isinstance(item.get("scoring_decision"), Mapping)
        else {}
    )
    if (
        status in _LONG_LIFECYCLE_STATUSES
        or item.get("thesis_status")
        or item.get("long_quality_score") is not None
        or scoring.get("thesis_status")
        or scoring.get("long_quality_score") is not None
    ):
        return MID_LONG_TERM_POOL
    return SHORT_TERM_POOL


def was_previously_retained(item: Mapping[str, Any]) -> bool:
    scoring = (
        item.get("scoring_decision")
        if isinstance(item.get("scoring_decision"), Mapping)
        else {}
    )
    if isinstance(scoring.get("pool_retained"), bool):
        return scoring["pool_retained"]
    return str(item.get("status") or "").strip().lower() in {
        "executable",
        "watching",
        "actionable",
    }


def decide_pool_membership(
    *,
    pool_kind: str,
    scorecard: Mapping[str, Any],
    previously_retained: bool,
) -> dict[str, Any]:
    """Apply entry/retention thresholds without deleting on incomplete data."""
    normalized_kind = (
        MID_LONG_TERM_POOL
        if str(pool_kind or "").strip().lower() in _MID_LONG_ALIASES
        else SHORT_TERM_POOL
    )
    missing_data = scorecard.get("missing_data")
    has_missing_data = isinstance(missing_data, list) and bool(missing_data)

    if normalized_kind == MID_LONG_TERM_POOL:
        thesis_status = str(scorecard.get("thesis_status") or "").strip().lower()
        threshold = (
            MID_LONG_RETENTION_SCORE
            if previously_retained
            else MID_LONG_ENTRY_SCORE
        )
        score = _number(scorecard.get("long_quality_score"))
        if thesis_status in {"broken", "stale"}:
            return {
                "retained": False,
                "pool_kind": normalized_kind,
                "score": score,
                "threshold": threshold,
                "reason": f"long_thesis_{thesis_status}",
            }
    else:
        threshold = SHORT_RETENTION_SCORE if previously_retained else SHORT_ENTRY_SCORE
        score = _number(scorecard.get("score"))

    if has_missing_data:
        return {
            "retained": previously_retained,
            "pool_kind": normalized_kind,
            "score": score,
            "threshold": threshold,
            "reason": (
                "incomplete_data_preserve_existing"
                if previously_retained
                else "incomplete_data_not_admitted"
            ),
        }

    retained = score >= threshold
    return {
        "retained": retained,
        "pool_kind": normalized_kind,
        "score": score,
        "threshold": threshold,
        "reason": (
            "score_passed"
            if retained
            else "score_below_retention_threshold"
            if previously_retained
            else "score_below_entry_threshold"
        ),
    }
