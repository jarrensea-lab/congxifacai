"""Local notification gate for quota-safe trading alerts."""
from __future__ import annotations

import json
import os
import fcntl
import tempfile
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import RLock
from typing import Any
from zoneinfo import ZoneInfo

from app.config import PROJECT_ROOT


def default_notification_state_path() -> Path:
    return Path(
        os.environ.get(
            "CONGXI_NOTIFICATION_STATE_PATH",
            os.path.abspath(os.path.join(PROJECT_ROOT, "..", "data", "notification_state.json")),
        )
    )


MARKET_TIMEZONE = ZoneInfo("Asia/Shanghai")
_PROCESS_STATE_LOCK = RLock()


@contextmanager
def _state_lock(path: Path):
    lock_path = path.with_name(f".{path.name}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with _PROCESS_STATE_LOCK, lock_path.open("a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_time(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=MARKET_TIMEZONE)
    return parsed.astimezone(timezone.utc)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": 1, "events": {}, "updated_at": ""}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "events": {}, "updated_at": ""}
    if not isinstance(payload, dict):
        return {"version": 1, "events": {}, "updated_at": ""}
    if not isinstance(payload.get("events"), dict):
        payload["events"] = {}
    return payload


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_path = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    except Exception:
        try:
            os.unlink(temp_path)
        except FileNotFoundError:
            pass
        raise


class NotificationGate:
    """Deduplicate and aggregate alert notifications before Feishu delivery."""

    ACTION_COOLDOWN_MINUTES = {
        "actionable": 30,
        "entry_cancelled": 5,
        "blocked_chasing": 60,
        "blocked_high_position": 60,
        "cooldown_after_loss": 240,
        "risk_budget_too_small": 60,
        "regime_blocks_dip": 60,
        "add_position": 30,
        "position_limit_reached": 60,
        "stop_loss": 10,
        "take_profit": 30,
    }

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path is not None else default_notification_state_path()

    def filter_alerts(self, alerts: list[dict[str, Any]], *, stage: str) -> list[dict[str, Any]]:
        with _state_lock(self.path):
            payload = _read_json(self.path)
            events = payload.setdefault("events", {})
            now = _now()
            accepted: list[dict[str, Any]] = []
            for alert in alerts or []:
                key = self._event_key(alert, stage)
                action = str(alert.get("action") or "")
                cooldown = timedelta(minutes=self.ACTION_COOLDOWN_MINUTES.get(action, 30))
                event = events.get(key)
                event = event if isinstance(event, dict) else {}
                last_sent = _parse_time(event.get("last_sent_at"))
                if last_sent and now - last_sent < cooldown:
                    continue
                events[key] = {
                    "last_sent_at": now.isoformat(timespec="seconds"),
                    "stock_code": alert.get("stock_code"),
                    "action": action,
                    "stage": stage,
                    "signal_id": alert.get("signal_id"),
                }
                accepted.append(alert)
            if accepted:
                payload["updated_at"] = now.isoformat(timespec="seconds")
                _write_json(self.path, payload)
            return accepted

    @staticmethod
    def _event_key(alert: dict[str, Any], stage: str) -> str:
        code = str(alert.get("stock_code") or "")
        action = str(alert.get("action") or "")
        playbook = str(alert.get("playbook") or "")
        signal_id = str(alert.get("signal_id") or "").strip()
        if signal_id:
            return f"signal:{signal_id}:{action}"
        return f"{stage}:{code}:{action}:{playbook}"


def build_alert_digest(alerts: list[dict[str, Any]], *, title: str, max_items: int = 8) -> str:
    lines = [f"**{title}**", ""]
    for alert in alerts[:max_items]:
        lines.append(
            f"- {alert.get('stock_name', '')}({alert.get('stock_code', '')}) "
            f"{alert.get('action', '')}: {alert.get('message', '')}"
        )
        suggestion = alert.get("suggestion")
        if suggestion:
            lines.append(f"  建议: {suggestion}")
    if len(alerts) > max_items:
        lines.append(f"- 另有 {len(alerts) - max_items} 条触发已聚合，详见候选池扫描结果。")
    return "\n".join(lines)
