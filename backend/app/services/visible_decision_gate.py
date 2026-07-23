"""One visible entry-decision gate shared by reports and intraday notifications."""
from __future__ import annotations

import json
import os
import tempfile
import fcntl
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from app.config import PROJECT_ROOT

ENTRY_ACTIONS = frozenset({"actionable", "add_position", "buy", "add", "increase", "executable"})
RISK_ACTIONS = frozenset({
    "sell",
    "stop_loss",
    "entry_cancelled",
    "cancelled",
    "take_profit",
    "reduce_position",
    "exit",
    "close_position",
    "risk_exit",
})
SAFETY_ALERT_ACTIONS = RISK_ACTIONS | frozenset({
    "blocked_chasing",
    "blocked_high_position",
    "cooldown_after_loss",
    "risk_budget_too_small",
    "regime_blocks_dip",
    "position_limit_reached",
})
NON_ENTRY_ACTIONS = frozenset({
    "",
    "watch",
    "watching",
    "hold",
    "research_only",
    "research_reference",
    "remove",
    "removed",
    "avoid",
    "expired",
}) | RISK_ACTIONS
UNRESOLVED_STATES = frozenset({
    "pending",
    "unsynced",
    "unresolved",
    "pending_writeback",
    "writeback_pending",
})
FAILED_SYNC_STATES = frozenset({"failed", "error", "degraded", "unavailable"})
REASON_LABELS = {
    "portfolio_truth_unresolved": "用户确认成交尚未同步持仓真值",
    "main_report_entry_not_triggered": "主报告入场状态明确为“未触发不买”",
    "fresh_stop_breach": "当前持仓的新鲜行情已跌破硬止损",
    "hard_risk_veto": "硬风控已否决新开仓",
    "decision_entry_veto": "决策层已明确否决新开仓",
    "visible_decision_gate_missing": "当日统一入场闸门缺失、损坏或已过期",
    "portfolio_sync_failed": "持仓真值同步失败",
    "portfolio_truth_invalid": "持仓真值数据结构无效",
    "position_watch_unresolved": "真实持仓缺少有效止损或目标计划",
}
ALLOWED_REASONS = frozenset(REASON_LABELS)
PROJECT_TIMEZONE = ZoneInfo("Asia/Shanghai")


def default_visible_decision_gate_path() -> Path:
    return Path(
        os.environ.get(
            "CONGXI_VISIBLE_DECISION_GATE_PATH",
            os.path.abspath(os.path.join(PROJECT_ROOT, "..", "data", "visible_decision_gate.json")),
        )
    )


def _state(value: Any) -> str:
    return str(value or "").strip().lower()


def _contains_unresolved_state(value: Any) -> bool:
    if isinstance(value, dict):
        return any(
            _state(value.get(key)) in UNRESOLVED_STATES
            for key in ("state", "status", "sync_status", "writeback_status")
        )
    return _state(value) in UNRESOLVED_STATES


def _portfolio_truth_unresolved(*sources: dict[str, Any] | None) -> bool:
    for source in sources:
        if not isinstance(source, dict):
            continue
        pending_fills = source.get("pending_user_confirmed_fills")
        if isinstance(pending_fills, list) and pending_fills:
            return True
        for key in (
            "portfolio_truth_state",
            "portfolio_sync_status",
            "portfolio_writeback_status",
            "user_confirmed_fill_status",
            "portfolio_truth",
            "portfolio_writeback",
            "user_confirmed_fill",
        ):
            if key in source and _contains_unresolved_state(source.get(key)):
                return True
        for event in source.get("trade_events") or []:
            if not isinstance(event, dict) or "user_confirmed" not in _state(event.get("source")):
                continue
            if any(
                _state(event.get(key)) in UNRESOLVED_STATES
                for key in ("portfolio_writeback_status", "writeback_status", "sync_status")
            ):
                return True
    return False


def _portfolio_sync_failed(*sources: Any) -> bool:
    for source in sources:
        if not isinstance(source, dict):
            continue
        if source.get("portfolio_sync_failed") is True:
            return True
        if any(
            _state(source.get(key)) in FAILED_SYNC_STATES
            for key in ("portfolio_sync_status", "sync_status", "writeback_status")
        ):
            return True
        for key in ("portfolio_truth", "portfolio_writeback", "user_confirmed_fill"):
            nested = source.get(key)
            if isinstance(nested, dict) and _portfolio_sync_failed(nested):
                return True
    return False


def _portfolio_truth_invalid(*sources: Any) -> bool:
    """Reject declared truth fields whose shape cannot be interpreted safely."""
    for source in sources:
        if source is None:
            continue
        if not isinstance(source, dict):
            return True
        for key in ("pending_user_confirmed_fills", "trade_events"):
            if key not in source:
                continue
            records = source[key]
            if not isinstance(records, list) or any(not isinstance(item, dict) for item in records):
                return True
        for key in ("portfolio_truth", "portfolio_writeback", "user_confirmed_fill"):
            if key in source and not isinstance(source[key], dict):
                return True
    return False


def _report_entry_not_triggered(decision: dict[str, Any]) -> bool:
    values: list[Any] = [
        decision.get(key)
        for key in ("main_report_state", "entry_state", "final_view", "final_decision")
    ]
    for key in ("main_report", "entry"):
        nested = decision.get(key)
        if isinstance(nested, dict):
            values.extend(nested.get(field) for field in ("state", "status", "decision"))
    return any("未触发不买" in str(value or "") for value in values)


def _hard_risk_veto(decision: dict[str, Any]) -> bool:
    if decision.get("hard_risk_blocked") is True or decision.get("hard_risk_veto") is True:
        return True
    if _state(decision.get("hard_risk_state")) in {"blocked", "veto", "stop"}:
        return True
    risk_gate = decision.get("risk_gate")
    return isinstance(risk_gate, dict) and (
        risk_gate.get("allowed") is False
        or _state(risk_gate.get("state") or risk_gate.get("status")) in {"blocked", "veto", "stop"}
    )


def build_visible_decision_gate(
    *,
    report_date: str,
    target_date: str,
    decision: dict[str, Any] | None,
    portfolio_truth: dict[str, Any] | None = None,
    stop_breaches: list[dict[str, Any]] | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Build the deterministic report/notification entry gate without writing state."""
    raw_decision = decision
    decision = decision if isinstance(decision, dict) else {}
    reasons: list[str] = []
    truth_invalid = _portfolio_truth_invalid(raw_decision, portfolio_truth)
    if truth_invalid:
        reasons.append("portfolio_truth_invalid")
    elif _portfolio_sync_failed(decision, portfolio_truth):
        reasons.append("portfolio_sync_failed")
    elif _portfolio_truth_unresolved(decision, portfolio_truth):
        reasons.append("portfolio_truth_unresolved")
    watch_truth = (
        portfolio_truth.get("position_watch_reconciliation")
        if isinstance(portfolio_truth, dict)
        and isinstance(portfolio_truth.get("position_watch_reconciliation"), dict)
        else {}
    )
    if watch_truth.get("healthy") is False:
        reasons.append("position_watch_unresolved")
    if _report_entry_not_triggered(decision):
        reasons.append("main_report_entry_not_triggered")
    if stop_breaches:
        reasons.append("fresh_stop_breach")
    if _hard_risk_veto(decision):
        reasons.append("hard_risk_veto")
    if decision.get("entry_allowed") is False:
        reasons.append("decision_entry_veto")
    reasons = list(dict.fromkeys(reasons))
    return {
        "report_date": report_date,
        "target_date": target_date,
        "generated_at": generated_at or datetime.now().astimezone().isoformat(timespec="seconds"),
        "entry_allowed": not reasons,
        "reasons": reasons,
        "state": "allowed" if not reasons else "blocked",
    }


def format_visible_decision_reasons(gate: dict[str, Any]) -> str:
    return "；".join(REASON_LABELS.get(str(reason), str(reason)) for reason in gate.get("reasons") or [])


def _entry_candidate(item: dict[str, Any], *, outside_pool: bool) -> bool:
    action = _state(item.get("action") or item.get("status"))
    if action in NON_ENTRY_ACTIONS:
        return False
    if action in ENTRY_ACTIONS or item.get("actionable") is True:
        return True
    if action:
        return True
    return outside_pool and not item.get("research_only") and bool(
        item.get("suggested_amount") or item.get("position_amount") or item.get("lot_value")
    )


def apply_visible_decision_gate(
    decision: dict[str, Any],
    gate: dict[str, Any],
) -> dict[str, Any]:
    """Return a report-safe decision copy; never mutate or persist the source decision."""
    visible = dict(decision)
    visible["visible_decision_gate"] = dict(gate)
    if gate.get("entry_allowed") is not False:
        return visible

    reason_text = format_visible_decision_reasons(gate)
    for field, outside_pool in (("target_scores", False), ("outside_pool_scan", True)):
        source_rows = decision.get(field)
        if not isinstance(source_rows, list):
            continue
        safe_rows: list[Any] = []
        for raw in source_rows:
            if not isinstance(raw, dict):
                safe_rows.append(raw)
                continue
            item = dict(raw)
            if _entry_candidate(item, outside_pool=outside_pool):
                item["blocked_entry_action"] = item.get("action") or item.get("status")
                item["action"] = "watching"
                item_status = _state(item.get("status"))
                if item_status and item_status not in NON_ENTRY_ACTIONS:
                    item["status"] = "watching"
                item["actionable"] = False
                item["entry_allowed"] = False
                item["suggested_amount"] = 0
                item["position_amount"] = 0
                existing_reason = str(item.get("decision_reason") or item.get("watch_reason") or "").strip()
                item["decision_reason"] = "；".join(part for part in (reason_text, existing_reason) if part)
            safe_rows.append(item)
        visible[field] = safe_rows
    return visible


def _valid_gate(gate: Any) -> bool:
    if not isinstance(gate, dict):
        return False
    if not all(isinstance(gate.get(field), str) and gate[field].strip() for field in (
        "report_date", "target_date", "generated_at", "state"
    )):
        return False
    if not isinstance(gate.get("entry_allowed"), bool) or not isinstance(gate.get("reasons"), list):
        return False
    reasons = gate["reasons"]
    state = gate["state"]
    entry_allowed = gate["entry_allowed"]
    if entry_allowed is not (state == "allowed" and reasons == []):
        return False
    if not entry_allowed and (state != "blocked" or not reasons):
        return False
    if any(not isinstance(reason, str) or reason not in ALLOWED_REASONS for reason in reasons):
        return False
    try:
        report_day = date.fromisoformat(gate["report_date"])
        target_day = date.fromisoformat(gate["target_date"])
        parsed_at = datetime.fromisoformat(gate["generated_at"].replace("Z", "+00:00"))
    except ValueError:
        return False
    if parsed_at.tzinfo is None or parsed_at.utcoffset() is None:
        return False
    generated_local_day = parsed_at.astimezone(PROJECT_TIMEZONE).date()
    return report_day <= generated_local_day <= target_day


def _gate_version(gate: dict[str, Any]) -> tuple[date, datetime]:
    generated_at = datetime.fromisoformat(gate["generated_at"].replace("Z", "+00:00"))
    return date.fromisoformat(gate["target_date"]), generated_at.astimezone(timezone.utc)


def _read_valid_gate(path: Path) -> dict[str, Any] | None:
    try:
        gate = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None
    return gate if _valid_gate(gate) else None


def build_runtime_blocked_gate(reason: str, *, today: date | None = None) -> dict[str, Any]:
    """Build a schema-valid fail-closed runtime override for a known reason."""
    if reason not in ALLOWED_REASONS:
        raise ValueError("visible_decision_gate_reason_invalid")
    effective_day = today or datetime.now().astimezone().date()
    day = effective_day.isoformat()
    return {
        "report_date": day,
        "target_date": day,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "entry_allowed": False,
        "reasons": [reason],
        "state": "blocked",
    }


def write_visible_decision_gate(
    gate: dict[str, Any],
    *,
    path: str | Path | None = None,
) -> Path:
    """Atomically persist one validated gate for the report generation chain."""
    if not _valid_gate(gate):
        raise ValueError("visible_decision_gate_invalid")
    target = Path(path) if path is not None else default_visible_decision_gate_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    lock_path = target.with_name(f".{target.name}.lock")
    with lock_path.open("a+", encoding="utf-8") as lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        try:
            existing = _read_valid_gate(target)
            if existing is not None and _gate_version(existing) >= _gate_version(gate):
                return target
            encoded = json.dumps(gate, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
            descriptor, temp_path = tempfile.mkstemp(
                prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
            )
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    handle.write(encoded + "\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp_path, target)
                directory_fd = os.open(target.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            except Exception:
                try:
                    os.unlink(temp_path)
                except FileNotFoundError:
                    pass
                raise
        finally:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
    return target


def load_effective_visible_decision_gate(
    *,
    path: str | Path | None = None,
    today: date | None = None,
) -> dict[str, Any] | None:
    """Load today's target-date gate; unresolved portfolio truth may carry forward."""
    target = Path(path) if path is not None else default_visible_decision_gate_path()
    gate = _read_valid_gate(target)
    if gate is None:
        return None
    effective_day = today or datetime.now().astimezone().date()
    if gate["target_date"] == effective_day.isoformat():
        return gate
    if gate.get("entry_allowed") is False and "portfolio_truth_unresolved" in gate.get("reasons", []):
        return gate
    return None


def load_runtime_visible_decision_gate(
    *,
    path: str | Path | None = None,
    today: date | None = None,
) -> dict[str, Any]:
    """Load an effective gate for main runtime, failing closed when unavailable."""
    effective_day = today or datetime.now().astimezone().date()
    gate = load_effective_visible_decision_gate(path=path, today=effective_day)
    if gate is not None:
        return gate
    return build_runtime_blocked_gate("visible_decision_gate_missing", today=effective_day)


def filter_alerts_by_visible_decision_gate(
    alerts: list[dict[str, Any]],
    gate: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    if not gate or gate.get("entry_allowed") is not False:
        return list(alerts)
    return [item for item in alerts if _state(item.get("action")) in SAFETY_ALERT_ACTIONS]
