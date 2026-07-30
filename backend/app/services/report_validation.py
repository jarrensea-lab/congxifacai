"""Semantic acceptance checks for the v9 actionable strategy report."""
from __future__ import annotations

from typing import Any


ACTIONABLE_ACTIONS = {"buy", "add", "actionable", "executable", "increase"}


def _rows(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def validate_report(
    content: str,
    pipeline_result: dict[str, Any] | None,
) -> dict[str, Any]:
    """Validate the user-visible report against the pipeline result.

    This deliberately checks meaning, not just whether a markdown file exists.
    """
    result = pipeline_result if isinstance(pipeline_result, dict) else {}
    scorecards = _rows(result.get("scorecards"))
    lifecycle_events = _rows(result.get("lifecycle_events"))
    metrics = result.get("metrics") if isinstance(result.get("metrics"), dict) else {}
    errors: list[str] = []
    warnings: list[str] = []

    for marker in ("## 今日可操作结论", "账户：", "### 今日变化", "### 管线状态"):
        if marker not in content:
            errors.append(f"required_section_missing:{marker}")

    actionable = [
        item
        for item in scorecards
        if str(item.get("action") or "").strip().lower() in ACTIONABLE_ACTIONS
    ]
    action_card_count = content.count("最大计划亏损")
    if action_card_count > 3:
        errors.append("too_many_action_cards")
    expected_card_count = min(3, len(actionable))
    if action_card_count != expected_card_count:
        errors.append("action_card_count_mismatch")

    if not actionable and not any(
        marker in content
        for marker in ("没有买入建议的原因", "最接近触发", "完成评分 0 只")
    ):
        errors.append("no_action_reason_missing")

    visible_lifecycle_events = [
        event
        for event in lifecycle_events
        if str(event.get("event") or event.get("event_type") or "").lower()
        in {"added", "downgraded", "removed"}
    ]
    for event in visible_lifecycle_events:
        code = str(event.get("code") or event.get("stock_code") or "").strip()
        if code and code not in content:
            errors.append("lifecycle_change_missing")
            break

    scored_count = int(metrics.get("scored_count") or 0)
    if scored_count > 0 and visible_lifecycle_events and "短线池：暂无" in content:
        errors.append("candidate_visibility_contradiction")
    if scored_count == 0:
        warnings.append("no_scored_candidates")

    return {
        "ok": not errors,
        "errors": list(dict.fromkeys(errors)),
        "warnings": list(dict.fromkeys(warnings)),
    }
