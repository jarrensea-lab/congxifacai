"""Long-horizon investment thesis store and status evaluation."""
from __future__ import annotations

import json
import os
from datetime import date, datetime
from pathlib import Path
from typing import Any

from app.config import PROJECT_ROOT


def default_long_thesis_path() -> Path:
    return Path(
        os.environ.get(
            "CONGXI_LONG_THESIS_PATH",
            os.path.abspath(os.path.join(PROJECT_ROOT, "..", "data", "long_thesis.json")),
        )
    )


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _clean_symbol(value: Any) -> str:
    return str(value or "").strip()


def _read_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return default
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default
    return raw if isinstance(raw, dict) else default


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp_path.replace(path)


def _parse_day(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def evaluate_thesis_status(
    thesis: dict[str, Any],
    *,
    as_of: str | date | None = None,
    stale_after_days: int = 90,
) -> dict[str, Any]:
    """Classify thesis health from red lines, assumptions, and freshness."""
    red_lines = thesis.get("red_lines") if isinstance(thesis, dict) else []
    if not isinstance(red_lines, list):
        red_lines = []
    for item in red_lines:
        if not isinstance(item, dict):
            continue
        if item.get("status") == "triggered":
            return {
                "status": "broken",
                "red_line_status": "triggered",
                "reason": str(item.get("condition") or item.get("id") or "red_line_triggered"),
            }

    reference_day = as_of if isinstance(as_of, date) else _parse_day(as_of) if as_of else date.today()
    updated_day = _parse_day(thesis.get("updated_at") or thesis.get("created_at"))
    if updated_day and (reference_day - updated_day).days > stale_after_days:
        return {
            "status": "stale",
            "red_line_status": "clear",
            "reason": f"thesis older than {stale_after_days} days",
        }

    assumptions = thesis.get("assumptions") if isinstance(thesis, dict) else []
    if not isinstance(assumptions, list):
        assumptions = []
    if any(isinstance(item, dict) and item.get("status") in {"broken", "failed"} for item in assumptions):
        return {"status": "broken", "red_line_status": "clear", "reason": "core_assumption_broken"}
    if any(isinstance(item, dict) and item.get("status") in {"weakened", "weakening"} for item in assumptions):
        return {"status": "weakened", "red_line_status": "clear", "reason": "assumption_weakened"}
    return {"status": "healthy", "red_line_status": "clear", "reason": "assumptions_intact"}


class LongThesisStore:
    """File-backed long thesis store.

    This store is intentionally separate from the production target pool, while
    target lifecycle state still lives in TargetPoolStore.
    """

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path is not None else default_long_thesis_path()

    def load(self) -> dict[str, Any]:
        payload = _read_json(self.path, {"version": 1, "updated_at": "", "items": {}})
        payload.setdefault("version", 1)
        payload.setdefault("updated_at", "")
        payload.setdefault("items", {})
        if not isinstance(payload["items"], dict):
            payload["items"] = {}
        return payload

    def save(self, payload: dict[str, Any]) -> None:
        payload["updated_at"] = _now()
        _write_json(self.path, payload)

    def get(self, symbol: str) -> dict[str, Any] | None:
        return self.load().get("items", {}).get(_clean_symbol(symbol))

    def upsert(self, thesis: dict[str, Any]) -> dict[str, Any]:
        symbol = _clean_symbol(thesis.get("symbol") or thesis.get("code"))
        if not symbol:
            raise ValueError("long thesis requires symbol")
        payload = self.load()
        items = payload.setdefault("items", {})
        existing = items.get(symbol, {})
        merged = {
            **existing,
            **thesis,
            "symbol": symbol,
            "name": thesis.get("name") or existing.get("name") or symbol,
            "updated_at": _now(),
        }
        merged.setdefault("created_at", existing.get("created_at") or _now())
        merged.setdefault("reviews", existing.get("reviews") or [])
        status = evaluate_thesis_status(merged)
        merged["thesis_status"] = thesis.get("thesis_status") or status["status"]
        items[symbol] = merged
        self.save(payload)
        return merged

    def append_review(self, symbol: str, review: dict[str, Any]) -> dict[str, Any] | None:
        clean = _clean_symbol(symbol)
        payload = self.load()
        item = payload.setdefault("items", {}).get(clean)
        if not isinstance(item, dict):
            return None
        entry = {"created_at": _now(), **review}
        item.setdefault("reviews", []).append(entry)
        item["reviews"] = item["reviews"][-50:]
        if review.get("status"):
            item["thesis_status"] = str(review["status"])
        else:
            item["thesis_status"] = evaluate_thesis_status(item)["status"]
        item["updated_at"] = _now()
        self.save(payload)
        return item
