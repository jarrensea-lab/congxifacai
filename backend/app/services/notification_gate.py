"""Local notification gate for quota-safe trading alerts."""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from app.config import PROJECT_ROOT


def default_notification_state_path() -> Path:
    return Path(
        os.environ.get(
            "CONGXI_NOTIFICATION_STATE_PATH",
            os.path.abspath(os.path.join(PROJECT_ROOT, "..", "data", "notification_state.json")),
        )
    )


def _now() -> datetime:
    return datetime.now()


def _parse_time(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


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
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


class NotificationGate:
    """Deduplicate and aggregate alert notifications before Feishu delivery."""

    ACTION_COOLDOWN_MINUTES = {
        "actionable": 30,
        "blocked_chasing": 60,
        "risk_budget_too_small": 60,
        "regime_blocks_dip": 60,
        "stop_loss": 10,
        "take_profit": 30,
    }

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path is not None else default_notification_state_path()

    def filter_alerts(self, alerts: list[dict[str, Any]], *, stage: str) -> list[dict[str, Any]]:
        payload = _read_json(self.path)
        events = payload.setdefault("events", {})
        now = _now()
        accepted: list[dict[str, Any]] = []
        for alert in alerts or []:
            key = self._event_key(alert, stage)
            action = str(alert.get("action") or "")
            cooldown = timedelta(minutes=self.ACTION_COOLDOWN_MINUTES.get(action, 30))
            last_sent = _parse_time((events.get(key) or {}).get("last_sent_at"))
            if last_sent and now - last_sent < cooldown:
                continue
            events[key] = {
                "last_sent_at": now.isoformat(timespec="seconds"),
                "stock_code": alert.get("stock_code"),
                "action": action,
                "stage": stage,
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
