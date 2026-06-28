"""Contract helpers for the Sentinel research evidence layer.

Sentinel is a research-evidence module. These helpers validate the file
boundary before any future engine reads inputs or publishes outputs.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List


REQUIRED_INPUT_FILES = [
    "news_events.jsonl",
    "market_snapshot.json",
    "portfolio_snapshot.json",
    "candidate_pool.json",
    "financial_evidence.json",
    "risk_context.json",
]

SECRET_FIELD_NAMES = {
    "authorization",
    "authorization_header",
    "cookie",
    "cookies",
    "token",
    "api_key",
    "apikey",
    "secret",
    "password",
}

FORBIDDEN_SENTINEL_ACTIONS = {"buy", "sell", "clear", "all_in"}


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _iter_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    if not path.exists():
        return
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            item = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path.name}:{line_number} invalid jsonl: {exc}") from exc
        if not isinstance(item, dict):
            raise ValueError(f"{path.name}:{line_number} must be a JSON object")
        yield item


def _find_secret_fields(value: Any) -> List[str]:
    found: List[str] = []
    if isinstance(value, dict):
        for key, nested in value.items():
            lowered = str(key).lower()
            if lowered in SECRET_FIELD_NAMES:
                found.append(str(key))
            found.extend(_find_secret_fields(nested))
    elif isinstance(value, list):
        for item in value:
            found.extend(_find_secret_fields(item))
    return found


def validate_sentinel_input_bundle(bundle_dir: str | Path) -> Dict[str, Any]:
    """Validate the unified input bundle produced by the data-source engine."""
    root = Path(bundle_dir)
    missing_files = [name for name in REQUIRED_INPUT_FILES if not (root / name).exists()]
    secret_fields: List[str] = []
    errors: List[str] = []
    news_events_count = 0

    try:
        for event in _iter_jsonl(root / "news_events.jsonl"):
            news_events_count += 1
            secret_fields.extend(_find_secret_fields(event))
    except ValueError as exc:
        errors.append(str(exc))

    for name in REQUIRED_INPUT_FILES:
        path = root / name
        if not path.exists() or name.endswith(".jsonl"):
            continue
        try:
            secret_fields.extend(_find_secret_fields(_load_json(path)))
        except json.JSONDecodeError as exc:
            errors.append(f"{name} invalid json: {exc}")

    secret_fields = sorted(set(secret_fields))
    valid = not missing_files and not secret_fields and not errors

    return {
        "valid": valid,
        "missing_files": missing_files,
        "secret_fields": secret_fields,
        "errors": errors,
        "news_events_count": news_events_count,
    }


def validate_sentinel_output_bundle(bundle_dir: str | Path) -> Dict[str, Any]:
    """Validate Sentinel outputs before debate/court/fengkong consumers read them."""
    root = Path(bundle_dir)
    forbidden_actions: List[str] = []
    errors: List[str] = []
    alerts_path = root / "sentinel_intraday_alerts.json"

    if alerts_path.exists():
        try:
            payload = _load_json(alerts_path)
            alerts = payload.get("alerts", []) if isinstance(payload, dict) else []
            for alert in alerts:
                if not isinstance(alert, dict):
                    continue
                action_type = str(alert.get("action_type", "")).lower()
                if action_type in FORBIDDEN_SENTINEL_ACTIONS:
                    forbidden_actions.append(action_type)
        except json.JSONDecodeError as exc:
            errors.append(f"{alerts_path.name} invalid json: {exc}")

    forbidden_actions = sorted(set(forbidden_actions))
    return {
        "valid": not forbidden_actions and not errors,
        "forbidden_actions": forbidden_actions,
        "errors": errors,
    }
