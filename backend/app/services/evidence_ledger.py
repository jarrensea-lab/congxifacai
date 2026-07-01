"""Evidence ledger and Sentinel/Serenity target-pool adapters."""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config import PROJECT_ROOT
from app.services.quant_lifecycle import TargetPoolStore


def default_evidence_ledger_path() -> Path:
    return Path(
        os.environ.get(
            "CONGXI_EVIDENCE_LEDGER_PATH",
            os.path.abspath(os.path.join(PROJECT_ROOT, "..", "data", "evidence_ledger.jsonl")),
        )
    )


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _is_a_share_code(value: Any) -> bool:
    text = str(value or "").strip()
    return len(text) == 6 and text.isdigit() and text[:1] in {"0", "3", "6", "8", "9"}


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


class EvidenceLedgerStore:
    """Append-only JSONL ledger with deterministic evidence IDs."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path is not None else default_evidence_ledger_path()

    def load_all(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        records: list[dict[str, Any]] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                records.append(payload)
        return records

    def append_many(self, evidence: list[dict[str, Any]]) -> int:
        existing = {item.get("evidence_id") for item in self.load_all()}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        written = 0
        with self.path.open("a", encoding="utf-8") as fh:
            for item in evidence:
                record = dict(item)
                record.setdefault("created_at", _now())
                record["evidence_id"] = record.get("evidence_id") or _stable_id(record)
                if record["evidence_id"] in existing:
                    continue
                fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
                existing.add(record["evidence_id"])
                written += 1
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
            "valid_a_share": _is_a_share_code(code),
            "enters_strategy": False,
            "enters_target_pool": _is_a_share_code(code),
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
                "valid_a_share": _is_a_share_code(code),
                "enters_strategy": False,
                "enters_target_pool": _is_a_share_code(code),
            })

    for record in records:
        record["evidence_id"] = _stable_id(record)
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
