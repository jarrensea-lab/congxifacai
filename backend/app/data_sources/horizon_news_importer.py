"""Import Horizon-collected TusharePro news into Sentinel input format."""

from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List

from app.ai.sentinel_contracts import SECRET_FIELD_NAMES


CN_TZ = timezone(timedelta(hours=8))

KEYWORD_SYMBOLS = {
    "许继电气": "000400.SZ",
}

KEYWORD_THEMES = {
    "电网": "电网设备",
    "AI电力": "AI电力",
    "机器人": "机器人",
    "光通信": "CPO光通信",
    "半导体": "AI半导体",
}

REQUIRED_NEWS_FIELDS = [
    "id",
    "source",
    "channel",
    "published_at",
    "fetched_at",
    "content",
    "is_key",
    "symbols",
    "themes",
    "dedupe_key",
    "raw_hash",
]


def _now_iso() -> str:
    return datetime.now(CN_TZ).replace(microsecond=0).isoformat()


def _read_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            payload["_source_file"] = path.name
            payload["_line_number"] = line_number
            yield payload


def _contains_secret_field(value: Any) -> bool:
    if isinstance(value, dict):
        for key, nested in value.items():
            if str(key).lower() in SECRET_FIELD_NAMES:
                return True
            if _contains_secret_field(nested):
                return True
    elif isinstance(value, list):
        return any(_contains_secret_field(item) for item in value)
    return False


def _normalize_event(raw: Dict[str, Any], ingested_at: str) -> Dict[str, Any] | None:
    if _contains_secret_field(raw):
        return None

    event = {field: raw.get(field) for field in REQUIRED_NEWS_FIELDS}
    if any(event.get(field) in (None, "") for field in REQUIRED_NEWS_FIELDS if field not in {"symbols", "themes"}):
        return None

    content = str(event.get("content") or "")
    symbols = list(event.get("symbols") or [])
    themes = list(event.get("themes") or [])

    for keyword, symbol in KEYWORD_SYMBOLS.items():
        if keyword in content and symbol not in symbols:
            symbols.append(symbol)

    for keyword, theme in KEYWORD_THEMES.items():
        if keyword in content and theme not in themes:
            themes.append(theme)

    event["symbols"] = symbols
    event["themes"] = themes
    event["ingested_at"] = ingested_at
    event["evidence_status"] = "enriched"
    return event


def import_horizon_news_events(root_dir: str | Path, date: str) -> List[Dict[str, Any]]:
    """Read Horizon raw news for one date and return de-duplicated events."""
    root = Path(root_dir)
    raw_dir = root / "raw" / date
    if not raw_dir.exists():
        return []

    seen: set[str] = set()
    events: List[Dict[str, Any]] = []
    ingested_at = _now_iso()

    for path in sorted(raw_dir.glob("*.jsonl")):
        for raw in _read_jsonl(path):
            event = _normalize_event(raw, ingested_at)
            if not event:
                continue
            dedupe_key = str(event.get("dedupe_key") or event.get("id"))
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            events.append(event)

    return events


def write_sentinel_news_events(events: List[Dict[str, Any]], output_path: str | Path) -> None:
    """Write normalized news events to Sentinel's news_events.jsonl."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(event, ensure_ascii=False, sort_keys=True) for event in events]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
