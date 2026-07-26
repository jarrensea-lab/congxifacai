"""Materialize Serenity research into shadow-only long-horizon records."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from app.services.evidence_ledger import (
    EvidenceLedgerStore,
    build_long_horizon_evidence,
)
from app.services.long_thesis import LongThesisStore, evaluate_thesis_status
from app.services.quant_lifecycle import TargetPoolStore


_A_SHARE_CODE = re.compile(
    r"^(?:000|001|002|003|300|301|302|600|601|603|605|688|689|"
    r"430|83[0-9]|87[0-9])\d{3}$"
)


def _summary() -> dict[str, Any]:
    return {
        "status": "success",
        "boundary": "shadow_only",
        "thesis_count": 0,
        "evidence_count": 0,
        "target_count": 0,
        "forming_count": 0,
        "verified_count": 0,
        "diagnostics": [],
    }


def _json_store_diagnostic(store: Any, store_name: str) -> dict[str, str] | None:
    path_value = getattr(store, "path", None)
    if path_value is None:
        return None
    path = Path(path_value)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "reason": "store_history_corrupted",
            "store": store_name,
            "error": f"{type(exc).__name__}: {str(exc)[:160]}",
        }
    if not isinstance(payload, dict) or not isinstance(payload.get("items", {}), dict):
        return {
            "reason": "store_history_corrupted",
            "store": store_name,
            "error": "expected JSON object with an items object",
        }
    return None


def _ledger_diagnostic(ledger: Any) -> dict[str, str] | None:
    path_value = getattr(ledger, "path", None)
    if path_value is None:
        return None
    path = Path(path_value)
    if not path.exists():
        return None
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        return {
            "reason": "store_history_corrupted",
            "store": "evidence_ledger",
            "error": f"{type(exc).__name__}: {str(exc)[:160]}",
        }
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            return {
                "reason": "store_history_corrupted",
                "store": "evidence_ledger",
                "error": f"line {line_number}: {type(exc).__name__}: {str(exc)[:120]}",
            }
        if not isinstance(record, dict):
            return {
                "reason": "store_history_corrupted",
                "store": "evidence_ledger",
                "error": f"line {line_number}: expected JSON object",
            }
    return None


def _status_ok(value: Any) -> bool:
    if isinstance(value, dict):
        value = value.get("status")
    return str(value or "").strip().lower() in {"ok", "success"}


def _quote_evidence_valid(evidence: Any) -> bool:
    if not isinstance(evidence, dict) or not str(evidence.get("fact") or "").strip():
        return False
    metrics = evidence.get("metrics")
    if not isinstance(metrics, dict):
        return False
    try:
        return float(metrics.get("price") or 0) > 0 and evidence.get("strength") != "weak"
    except (TypeError, ValueError):
        return False


def _financial_evidence_valid(evidence: Any) -> bool:
    if not isinstance(evidence, dict) or not str(evidence.get("fact") or "").strip():
        return False
    metrics = evidence.get("metrics")
    if not isinstance(metrics, dict) or evidence.get("strength") == "weak":
        return False
    return any(value not in (None, "") for value in metrics.values())


def _core_thesis(candidate: dict[str, Any]) -> str:
    name = str(candidate.get("name") or candidate.get("code") or "").strip()
    chain_position = str(candidate.get("chain_position") or "待核验产业链位置").strip()
    chokepoint = str(candidate.get("chokepoint") or "待核验瓶颈").strip()
    duration = str(candidate.get("bottleneck_duration") or "待验证").strip()
    return (
        f"{name}位于{chain_position}，关键瓶颈为{chokepoint}；"
        f"瓶颈持续性判断为{duration}，需按季度核验财务传导和替代路径。"
    )


def _build_thesis(
    candidate: dict[str, Any],
    dive: dict[str, Any],
    report_date: str,
) -> tuple[dict[str, Any], bool]:
    quote_verified = (
        _status_ok(dive.get("quote_status"))
        and _quote_evidence_valid(candidate.get("quote_evidence"))
    )
    financial_verified = (
        _status_ok(dive.get("financial_status"))
        and _financial_evidence_valid(candidate.get("financial_evidence"))
    )
    missing_verification = []
    if not quote_verified:
        missing_verification.append("quote")
    if not financial_verified:
        missing_verification.append("financial")

    quote_metrics = (
        candidate.get("quote_evidence", {}).get("metrics", {})
        if isinstance(candidate.get("quote_evidence"), dict)
        else {}
    )
    thesis = {
        "symbol": str(candidate.get("code") or "").strip(),
        "name": str(candidate.get("name") or candidate.get("code") or "").strip(),
        "horizon": "6-36m",
        "core_thesis": _core_thesis(candidate),
        "quality_score": candidate.get("score", 0),
        "assumptions": list(candidate.get("long_assumptions") or []),
        "red_lines": list(candidate.get("red_lines") or []),
        "valuation_anchor": {
            "method": "serenity_questions_with_quote_snapshot",
            "questions": list(candidate.get("valuation_questions") or []),
            "price": quote_metrics.get("price"),
            "pe_ttm": quote_metrics.get("pe_ttm"),
            "pb": quote_metrics.get("pb"),
            "status": "question_only",
        },
        "valuation_questions": list(candidate.get("valuation_questions") or []),
        "quarterly_verification_tasks": list(
            candidate.get("quarterly_verification_tasks") or []
        ),
        "bottleneck_duration": candidate.get("bottleneck_duration", "待验证"),
        "bottleneck_map": dict(candidate.get("bottleneck_map") or {}),
        "quote_evidence": dict(candidate.get("quote_evidence") or {}),
        "financial_evidence": dict(candidate.get("financial_evidence") or {}),
        "source_report_path": str(dive.get("learning_report_path") or ""),
        "data_cutoff_date": report_date,
        "report_date": report_date,
        "source": "serenity_long_horizon",
        "boundary": "shadow_only",
        "research_only": True,
        "verification_status": (
            "verified" if not missing_verification else "incomplete"
        ),
        "missing_verification": missing_verification,
        "confidence": "B" if not missing_verification else "C",
    }
    verified = not missing_verification
    thesis["thesis_status"] = (
        evaluate_thesis_status(thesis, as_of=report_date)["status"]
        if verified
        else "forming"
    )
    return thesis, verified


def materialize_serenity_long_horizon(
    package: dict[str, Any],
    report_date: str,
    thesis_store: LongThesisStore | None = None,
    ledger: EvidenceLedgerStore | None = None,
    target_pool: TargetPoolStore | None = None,
) -> dict[str, Any]:
    """Persist Serenity candidates as inert long-horizon research objects."""
    thesis_store = thesis_store or LongThesisStore()
    ledger = ledger or EvidenceLedgerStore()
    target_pool = target_pool or TargetPoolStore()
    result = _summary()

    diagnostics = [
        diagnostic
        for diagnostic in (
            _json_store_diagnostic(thesis_store, "long_thesis"),
            _ledger_diagnostic(ledger),
            _json_store_diagnostic(target_pool, "target_pool"),
        )
        if diagnostic is not None
    ]
    if diagnostics:
        result["status"] = "failed"
        result["diagnostics"] = diagnostics
        return result

    for dive in package.get("serenity_deep_dives") or []:
        if not isinstance(dive, dict):
            result["diagnostics"].append(
                {"reason": "invalid_deep_dive", "error": "expected object"}
            )
            continue
        for candidate in dive.get("top_candidates") or []:
            if not isinstance(candidate, dict):
                result["diagnostics"].append(
                    {"reason": "invalid_candidate", "error": "expected object"}
                )
                continue
            symbol = str(candidate.get("code") or "").strip()
            if not _A_SHARE_CODE.fullmatch(symbol):
                result["diagnostics"].append(
                    {"symbol": symbol, "reason": "invalid_a_share_code"}
                )
                continue
            try:
                thesis, verified = _build_thesis(candidate, dive, report_date)
                evidence = build_long_horizon_evidence(
                    thesis,
                    report_date=report_date,
                    source_report_path=thesis["source_report_path"],
                    data_cutoff_date=report_date,
                )
                result["evidence_count"] += ledger.append_many(evidence)
                thesis["evidence_ids"] = [item["evidence_id"] for item in evidence]
                stored = thesis_store.upsert(thesis)
                result["thesis_count"] += 1
                if stored.get("thesis_status") == "forming":
                    result["forming_count"] += 1
                if verified:
                    result["verified_count"] += 1

                target_written = target_pool.upsert_target(
                    code=symbol,
                    name=stored["name"],
                    status="long_research",
                    source="long_horizon",
                    evidence_ids=thesis["evidence_ids"],
                    evidence={
                        "stage": "shadow_only",
                        "boundary": "shadow_only",
                        "research_only": True,
                        "verification_status": stored["verification_status"],
                        "thesis_status": stored["thesis_status"],
                        "source_report_path": stored["source_report_path"],
                    },
                    serenity={
                        "quality_score": stored["quality_score"],
                        "bottleneck_duration": stored["bottleneck_duration"],
                        "boundary": "research_only",
                    },
                )
                if not target_written:
                    raise RuntimeError("target_pool_upsert_rejected")
                result["target_count"] += 1
            except Exception as exc:
                result["diagnostics"].append(
                    {
                        "symbol": symbol,
                        "reason": "candidate_materialization_failed",
                        "error": f"{type(exc).__name__}: {str(exc)[:160]}",
                    }
                )

    if result["diagnostics"]:
        result["status"] = "partial"
    return result
