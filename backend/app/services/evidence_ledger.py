"""Evidence ledger and Sentinel/Serenity target-pool adapters."""
from __future__ import annotations

import hashlib
import json
import os
from contextlib import contextmanager
from datetime import datetime
from fcntl import LOCK_EX, LOCK_SH, LOCK_UN, flock
from pathlib import Path
from threading import RLock
from typing import Any

from app.config import PROJECT_ROOT
from app.services.quant_lifecycle import TargetPoolStore
from app.services.long_horizon_transaction import (
    transaction_journal_path_for_store,
    transaction_lock_path_for_store,
    writer_transaction_guard,
)
from app.utils.a_share_codes import validate_a_share_code

_LEDGER_PROCESS_LOCK = RLock()


def default_evidence_ledger_path() -> Path:
    return Path(
        os.environ.get(
            "CONGXI_EVIDENCE_LEDGER_PATH",
            os.path.abspath(os.path.join(PROJECT_ROOT, "..", "data", "evidence_ledger.jsonl")),
        )
    )


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _stable_id(payload: dict[str, Any]) -> str:
    identity = {
        "type": payload.get("type", ""),
        "date": payload.get("date", ""),
        "theme": payload.get("theme", ""),
        "code": payload.get("code", ""),
        "source_id": payload.get("source_id", ""),
        "summary": payload.get("summary", ""),
    }
    digest = hashlib.sha1(json.dumps(identity, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    return f"ev_{digest[:20]}"


def _long_logical_id(payload: dict[str, Any]) -> str:
    identity = {
        "type": payload.get("type", ""),
        "code": payload.get("code", ""),
        "source_id": payload.get("source_id", ""),
    }
    digest = hashlib.sha1(
        json.dumps(identity, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return f"ev_long_{digest[:20]}"


def _long_semantic_revision(thesis: dict[str, Any]) -> str:
    semantic_payload = {
        "core_thesis": thesis.get("core_thesis", ""),
        "quality_score": thesis.get("quality_score", 0),
        "confidence": thesis.get("confidence", ""),
        "thesis_status": thesis.get("thesis_status", ""),
        "verification_status": thesis.get("verification_status", ""),
        "missing_verification": thesis.get("missing_verification") or [],
        "valuation_anchor": thesis.get("valuation_anchor") or {},
        "assumptions": thesis.get("assumptions") or [],
        "red_lines": thesis.get("red_lines") or [],
        "financial_evidence": thesis.get("financial_evidence") or {},
        "quote_evidence": thesis.get("quote_evidence") or {},
    }
    digest = hashlib.sha1(
        json.dumps(
            semantic_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return f"rev_{digest[:20]}"


class EvidenceLedgerStore:
    """Append-only JSONL ledger with deterministic evidence IDs."""

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        transaction_lock_path: str | Path | None = None,
        transaction_journal_path: str | Path | None = None,
    ):
        self.path = Path(path) if path is not None else default_evidence_ledger_path()
        self.transaction_lock_path = transaction_lock_path_for_store(
            self.path,
            transaction_lock_path,
        )
        self.transaction_journal_path = transaction_journal_path_for_store(
            self.path,
            transaction_journal_path,
        )

    @contextmanager
    def _store_lock(self, *, exclusive: bool):
        lock_path = self.path.with_name(f".{self.path.name}.lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with _LEDGER_PROCESS_LOCK, lock_path.open("a+", encoding="utf-8") as lock_file:
            flock(lock_file.fileno(), LOCK_EX if exclusive else LOCK_SH)
            try:
                yield
            finally:
                flock(lock_file.fileno(), LOCK_UN)

    def _read_with_diagnostics_unlocked(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"ok": True, "records": [], "errors": []}
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            return {
                "ok": False,
                "records": [],
                "errors": [{
                    "line_number": 0,
                    "reason": f"{type(exc).__name__}: {str(exc)[:160]}",
                }],
            }
        records: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for line_number, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                errors.append({
                    "line_number": line_number,
                    "reason": f"invalid_json:{exc.msg}",
                })
                continue
            if not isinstance(record, dict):
                errors.append({
                    "line_number": line_number,
                    "reason": "record_must_be_object",
                })
                continue
            evidence_id = record.get("evidence_id")
            if not isinstance(evidence_id, str) or not evidence_id.strip():
                errors.append({
                    "line_number": line_number,
                    "reason": "evidence_id_invalid",
                })
                continue
            if evidence_id in seen_ids:
                errors.append({
                    "line_number": line_number,
                    "reason": "duplicate_evidence_id",
                })
                continue
            seen_ids.add(evidence_id)
            records.append(record)
        return {"ok": not errors, "records": records, "errors": errors}

    def load_all(self) -> list[dict[str, Any]]:
        with self._store_lock(exclusive=False):
            return self._read_with_diagnostics_unlocked()["records"]

    def read_with_diagnostics(self) -> dict[str, Any]:
        """Read every JSONL row and report malformed history without skipping it."""
        with self._store_lock(exclusive=False):
            return self._read_with_diagnostics_unlocked()

    def append_many(self, evidence: list[dict[str, Any]]) -> int:
        with writer_transaction_guard(
            self.transaction_lock_path,
            self.transaction_journal_path,
        ):
            with self._store_lock(exclusive=True):
                diagnostics = self._read_with_diagnostics_unlocked()
                if not diagnostics["ok"]:
                    raise ValueError("evidence ledger contains invalid history")
                existing = {
                    item.get("evidence_id")
                    for item in diagnostics["records"]
                }
                self.path.parent.mkdir(parents=True, exist_ok=True)
                written = 0
                with self.path.open("a", encoding="utf-8") as fh:
                    for item in evidence:
                        record = dict(item)
                        record.setdefault("created_at", _now())
                        record["evidence_id"] = (
                            record.get("evidence_id") or _stable_id(record)
                        )
                        if record["evidence_id"] in existing:
                            continue
                        fh.write(
                            json.dumps(
                                record,
                                ensure_ascii=False,
                                sort_keys=True,
                            )
                            + "\n"
                        )
                        existing.add(record["evidence_id"])
                        written += 1
                    fh.flush()
                    os.fsync(fh.fileno())
                return written


def build_sentinel_evidence(package: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert a Sentinel package into normalized evidence records."""
    report_date = str(package.get("date") or "")
    records: list[dict[str, Any]] = []

    for item in package.get("top_themes") or []:
        theme = str(item.get("name") or "").strip()
        if not theme:
            continue
        records.append({
            "type": "sentinel_theme",
            "date": report_date,
            "theme": theme,
            "summary": f"主题热度 {theme}: {item.get('count', 0)} 条",
            "theme_count": item.get("count", 0),
            "source": "sentinel",
            "enters_strategy": False,
            "enters_target_pool": False,
        })

    for item in package.get("top_symbols") or []:
        code = str(item.get("name") or "").strip()
        records.append({
            "type": "sentinel_symbol",
            "date": report_date,
            "code": code,
            "summary": f"标的提及 {code}: {item.get('count', 0)} 条",
            "symbol_count": item.get("count", 0),
            "source": "sentinel",
            "valid_a_share": validate_a_share_code(code),
            "enters_strategy": False,
            "enters_target_pool": validate_a_share_code(code),
        })

    for item in package.get("risk_events") or []:
        records.append({
            "type": "sentinel_risk",
            "date": report_date,
            "source_id": str(item.get("id") or ""),
            "summary": str(item.get("excerpt") or "")[:240],
            "risk_tags": item.get("matched_keywords") or [],
            "published_at": item.get("published_at", ""),
            "source": "sentinel",
            "enters_strategy": False,
            "enters_target_pool": False,
        })

    for dive in package.get("serenity_deep_dives") or []:
        theme = str(dive.get("theme") or "").strip()
        for candidate in dive.get("top_candidates") or []:
            code = str(candidate.get("code") or "").strip()
            records.append({
                "type": "serenity_candidate",
                "date": report_date,
                "theme": theme,
                "code": code,
                "name": candidate.get("name", code),
                "summary": str(candidate.get("verify_next") or candidate.get("chokepoint") or "")[:240],
                "score": candidate.get("score", 0),
                "chokepoint": candidate.get("chokepoint", ""),
                "chain_position": candidate.get("chain_position", ""),
                "verify_next": candidate.get("verify_next", ""),
                "learning_report_path": dive.get("learning_report_path", ""),
                "source": "sentinel_serenity",
                "valid_a_share": validate_a_share_code(code),
                "enters_strategy": False,
                "enters_target_pool": validate_a_share_code(code),
            })

    for record in records:
        record["evidence_id"] = _stable_id(record)
    return records


def build_long_horizon_evidence(
    thesis: dict[str, Any],
    *,
    report_date: str | None = None,
    source_report_path: str = "",
    data_cutoff_date: str = "",
) -> list[dict[str, Any]]:
    """Convert a long-horizon thesis into normalized evidence records."""
    symbol = str(thesis.get("symbol") or thesis.get("code") or "").strip()
    name = str(thesis.get("name") or symbol)
    day = report_date or str(thesis.get("date") or "")[:10]
    confidence = str(thesis.get("confidence") or "C")
    common = {
        "date": day,
        "code": symbol,
        "name": name,
        "source": "long_horizon",
        "confidence": confidence,
        "source_report_path": source_report_path or str(thesis.get("source_report_path") or ""),
        "data_cutoff_date": data_cutoff_date or str(thesis.get("data_cutoff_date") or ""),
        "enters_strategy": False,
        "enters_target_pool": validate_a_share_code(symbol),
    }
    semantic_revision = _long_semantic_revision(thesis)
    records: list[dict[str, Any]] = [
        {
            **common,
            "type": "long_thesis",
            "summary": str(thesis.get("core_thesis") or "")[:240],
            "quality_score": thesis.get("quality_score", 0),
            "valuation_anchor": thesis.get("valuation_anchor") or {},
            "thesis_status": thesis.get("thesis_status", "forming"),
        }
    ]

    for item in thesis.get("assumptions") or []:
        if not isinstance(item, dict):
            continue
        assumption_id = str(item.get("id") or item.get("claim") or "")
        records.append({
            **common,
            "type": "long_assumption",
            "source_id": assumption_id,
            "summary": str(item.get("claim") or "")[:240],
            "assumption_status": item.get("status", "unverified"),
            "verification": item.get("verification", ""),
            "frequency": item.get("frequency", ""),
        })

    for item in thesis.get("red_lines") or []:
        if not isinstance(item, dict):
            continue
        red_line_id = str(item.get("id") or item.get("condition") or "")
        records.append({
            **common,
            "type": "long_red_line",
            "source_id": red_line_id,
            "summary": str(item.get("condition") or "")[:240],
            "severity": item.get("severity", "warning"),
            "red_line_status": item.get("status", "clear"),
            "action": item.get("action", "review"),
        })

    for record in records:
        logical_id = _long_logical_id(record)
        record["logical_evidence_id"] = logical_id
        record["semantic_revision"] = semantic_revision
        record["evidence_id"] = f"{logical_id}_{semantic_revision}"
    return records


def build_sentinel_evidence_context(package: dict[str, Any] | None) -> str:
    """Render a compact evidence summary for strategy and debate prompts."""
    if not package:
        return "Sentinel evidence: unavailable"
    lines = [
        "Sentinel evidence:",
        f"- date={package.get('date', '')}; event_count={package.get('event_count', 0)}",
    ]
    themes = package.get("top_themes") or []
    if themes:
        lines.append("- themes: " + "; ".join(f"{i.get('name')}({i.get('count')})" for i in themes[:5]))
    risks = package.get("risk_events") or []
    if risks:
        lines.append("- risks: " + "; ".join(
            f"{'、'.join(i.get('matched_keywords') or [])}:{str(i.get('excerpt') or '')[:60]}" for i in risks[:3]
        ))
    candidates = []
    for dive in package.get("serenity_deep_dives") or []:
        for item in dive.get("top_candidates") or []:
            candidates.append(f"{item.get('name')}({item.get('code')}) score={item.get('score')}")
    if candidates:
        lines.append("- serenity_candidates: " + "; ".join(candidates[:8]))
    return "\n".join(lines)


def upsert_sentinel_evidence_to_target_pool(
    package: dict[str, Any],
    *,
    target_pool: TargetPoolStore | None = None,
    ledger: EvidenceLedgerStore | None = None,
) -> dict[str, Any]:
    """Persist Sentinel evidence and upsert valid A-share candidates into target pool."""
    target_pool = target_pool or TargetPoolStore()
    ledger = ledger or EvidenceLedgerStore()
    evidence = build_sentinel_evidence(package)
    ledger.append_many(evidence)

    by_code: dict[str, dict[str, Any]] = {}
    skipped: list[dict[str, Any]] = []
    evidence_by_code: dict[str, list[str]] = {}

    for item in evidence:
        code = str(item.get("code") or "").strip()
        if not code:
            continue
        if not item.get("valid_a_share"):
            skipped.append({"code": code, "reason": "invalid_a_share_code"})
            continue
        evidence_by_code.setdefault(code, []).append(item["evidence_id"])
        current = by_code.setdefault(code, {"code": code, "name": item.get("name") or code})
        if item["type"] == "serenity_candidate":
            current.update({
                "name": item.get("name") or current["name"],
                "theme": item.get("theme", ""),
                "serenity": {
                    "score": item.get("score", 0),
                    "chokepoint": item.get("chokepoint", ""),
                    "chain_position": item.get("chain_position", ""),
                    "verify_next": item.get("verify_next", ""),
                    "learning_report_path": item.get("learning_report_path", ""),
                },
            })
        elif item["type"] == "sentinel_symbol":
            current.setdefault("theme", "")
            current["symbol_count"] = item.get("symbol_count", 0)

    upserted = 0
    for code, item in by_code.items():
        ok = target_pool.upsert_target(
            code=code,
            name=item.get("name", code),
            status="candidate",
            source="sentinel_serenity",
            evidence_ids=evidence_by_code.get(code, []),
            evidence={"reason": "Sentinel/Serenity evidence candidate"},
            sentinel={"theme": item.get("theme", ""), "symbol_count": item.get("symbol_count", 0)},
            serenity=item.get("serenity", {}),
        )
        if ok:
            upserted += 1

    return {
        "evidence_count": len(evidence),
        "upserted_targets": upserted,
        "skipped": skipped,
    }
