"""Shared Sentinel freshness and decision-input injection boundary."""

from __future__ import annotations

from collections.abc import Callable, Mapping, MutableMapping
from datetime import date, datetime
from typing import Any


SENTINEL_ACTIVE_MAX_AGE_DAYS = 2


def _parse_iso_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def sentinel_package_age_days(
    package: Mapping[str, Any] | None,
    report_date: Any,
) -> int | None:
    """Return signed report-date minus package-date age."""
    if not isinstance(package, Mapping):
        return None
    current = _parse_iso_date(report_date)
    package_day = _parse_iso_date(package.get("date"))
    if current is None or package_day is None:
        return None
    return (current - package_day).days


def classify_sentinel_package(
    package: Mapping[str, Any] | None,
    report_date: Any,
) -> dict[str, object]:
    """Classify whether Sentinel evidence may enter current AI decisions."""
    if isinstance(report_date, datetime):
        normalized_report_date = report_date.date().isoformat()
    elif isinstance(report_date, date):
        normalized_report_date = report_date.isoformat()
    else:
        normalized_report_date = report_date
    if not isinstance(package, Mapping):
        return {
            "status": "unavailable",
            "active": False,
            "age_days": None,
            "package_date": None,
            "report_date": normalized_report_date,
        }

    package_date = package.get("date")
    age_days = sentinel_package_age_days(package, report_date)
    if age_days is None:
        status = "unknown_date"
    elif age_days < 0:
        status = "future"
    elif age_days > SENTINEL_ACTIVE_MAX_AGE_DAYS:
        status = "stale"
    else:
        status = "active"
    return {
        "status": status,
        "active": status == "active",
        "age_days": age_days,
        "package_date": package_date,
        "report_date": normalized_report_date,
    }


def sentinel_audit_only_message(classification: Mapping[str, object]) -> str:
    """Render the shared reason an inactive package is audit-only."""
    status = classification.get("status")
    package_date = classification.get("package_date")
    report_date = classification.get("report_date")
    age_days = classification.get("age_days")
    if status == "unavailable":
        return "未找到 Sentinel 研究包；不参与当前候选或 AI 决策。"
    if status == "stale":
        return (
            f"研究包日期 {package_date}，已过期 {age_days} 天；"
            "只保留历史审计/复盘，不参与当前候选或 AI 决策。"
        )
    if status == "future":
        return (
            f"研究包日期 {package_date} 晚于报告日 {report_date}（未来日期）；"
            "日期异常，包仅保留历史审计，不参与当前候选或 AI 决策。"
        )
    return (
        "研究包日期缺失或非法，无法验证时效；"
        "包仅保留历史审计，不参与当前候选或 AI 决策。"
    )


def inject_active_sentinel_evidence(
    package: Mapping[str, Any] | None,
    report_date: Any,
    market_data: MutableMapping[str, Any],
    *,
    context_builder: Callable[[Mapping[str, Any]], Any] | None = None,
    target_upserter: Callable[[Mapping[str, Any]], Any] | None = None,
) -> dict[str, object]:
    """Inject Sentinel context and research overlay only for an active package."""
    classification = classify_sentinel_package(package, report_date)
    if classification["active"] is not True:
        return classification

    if context_builder is None or target_upserter is None:
        from app.services.evidence_ledger import (
            build_sentinel_evidence_context,
            upsert_sentinel_evidence_to_target_pool,
        )

        context_builder = context_builder or build_sentinel_evidence_context
        target_upserter = (
            target_upserter or upsert_sentinel_evidence_to_target_pool
        )

    context = context_builder(package)
    ingest_result = target_upserter(package)
    market_data["sentinel_evidence"] = context
    return {
        **classification,
        "ingest_result": ingest_result,
    }
