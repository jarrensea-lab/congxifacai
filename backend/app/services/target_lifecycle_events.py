"""Human-readable target lifecycle changes for daily opportunity reports."""
from __future__ import annotations

from typing import Any


REMOVED_STATUSES = frozenset({"removed", "expired", "exit_candidate"})
ACTIVE_STATUSES = frozenset({"executable", "actionable"})

REASON_LABELS = {
    "fund_flow_reversed": "资金方向转弱或由流入转为流出",
    "score_below_entry_threshold": "综合评分未达到入池线",
    "score_below_retention_threshold": "综合评分跌破留池线",
    "missing_required_data": "关键数据缺失，不能维持原操作级别",
    "blocked_chasing": "价格进入追高区，取消当前操作条件",
    "blocked_high_position": "处于阶段高位，风险收益不再合适",
    "long_thesis_broken": "中长期逻辑红线触发",
    "risk_budget_too_small": "一手风险超过账户单笔风险预算",
}


def _score(payload: dict[str, Any]) -> float:
    try:
        return round(float(payload.get("score") or 0), 1)
    except (TypeError, ValueError):
        return 0.0


def _reason(current: dict[str, Any]) -> str:
    block_reason = str(current.get("block_reason") or "").strip()
    if block_reason in REASON_LABELS:
        return REASON_LABELS[block_reason]
    return str(
        current.get("decision_reason")
        or current.get("watch_reason")
        or current.get("reason")
        or "评分与状态按最新数据重新确认"
    ).strip()


def classify_lifecycle_event(
    *,
    previous: dict[str, Any] | None,
    current: dict[str, Any],
) -> dict[str, Any]:
    """Map one before/after decision into added, retained, downgraded, or removed."""
    prior = previous if isinstance(previous, dict) else {}
    old_status = str(prior.get("status") or "").strip().lower()
    new_status = str(current.get("status") or current.get("action") or "").strip().lower()
    old_score = _score(prior)
    new_score = _score(current)
    if not prior:
        event = "added"
    elif new_status in REMOVED_STATUSES:
        event = "removed"
    elif (
        old_status in ACTIVE_STATUSES
        and new_status not in ACTIVE_STATUSES
    ) or new_score <= old_score - 10:
        event = "downgraded"
    else:
        event = "retained"
    return {
        "code": str(current.get("code") or prior.get("code") or "").strip(),
        "name": str(current.get("name") or prior.get("name") or "").strip(),
        "event": event,
        "old_status": old_status,
        "new_status": new_status,
        "old_score": old_score,
        "new_score": new_score,
        "reason": _reason(current),
    }
