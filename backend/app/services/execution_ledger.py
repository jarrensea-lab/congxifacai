"""Append-only execution audit ledger and recommendation attribution joins."""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from app.config import PROJECT_ROOT

VALID_EVENT_TYPES = frozenset({"signal", "authorization", "recommendation", "fill", "outcome"})
MARKET_TIMEZONE = ZoneInfo("Asia/Shanghai")


def default_execution_ledger_path() -> Path:
    return Path(
        os.environ.get(
            "CONGXI_EXECUTION_LEDGER_PATH",
            os.path.abspath(os.path.join(PROJECT_ROOT, "..", "data", "execution_ledger.jsonl")),
        )
    )


@contextmanager
def _ledger_lock(path: Path, *, exclusive: bool):
    lock_path = Path(f"{path}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _event_fingerprint(event: dict[str, Any]) -> str:
    identity = {key: value for key, value in event.items() if key != "event_fingerprint"}
    return hashlib.sha256(_canonical_json(identity).encode("utf-8")).hexdigest()


def _aware_iso(value: Any) -> str:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        parsed = datetime.now(MARKET_TIMEZONE)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=MARKET_TIMEZONE)
    return parsed.isoformat()


def recommendation_id_for_signal(signal_id: str) -> str:
    digest = hashlib.sha256(str(signal_id).encode("utf-8")).hexdigest()[:20]
    return f"rec_{digest}"


def _normalize_event(event: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    if not isinstance(event, dict):
        return None, "event_must_be_object"
    event_id = event.get("event_id")
    event_type = event.get("event_type")
    occurred_at = event.get("occurred_at")
    code = event.get("code")
    source = event.get("source")
    payload = event.get("payload")
    if not isinstance(event_id, str) or not event_id.strip():
        return None, "event_id_invalid"
    if event_type not in VALID_EVENT_TYPES:
        return None, "event_type_invalid"
    if not isinstance(occurred_at, str) or not occurred_at.strip():
        return None, "occurred_at_invalid"
    try:
        parsed_at = datetime.fromisoformat(occurred_at.replace("Z", "+00:00"))
    except ValueError:
        return None, "occurred_at_invalid"
    if parsed_at.tzinfo is None or parsed_at.utcoffset() is None:
        return None, "occurred_at_timezone_required"
    if not isinstance(code, str) or not code.strip():
        return None, "code_invalid"
    if not isinstance(source, str) or not source.strip():
        return None, "source_invalid"
    if not isinstance(payload, dict):
        return None, "payload_invalid"
    required_ids = {
        "signal": ("signal_id",),
        "authorization": ("signal_id",),
        "recommendation": ("signal_id", "recommendation_id"),
        "fill": ("signal_id", "recommendation_id", "fill_id"),
        "outcome": ("signal_id", "recommendation_id", "fill_id"),
    }[event_type]
    for field in required_ids:
        value = event.get(field)
        if not isinstance(value, str) or not value.strip():
            return None, f"{field}_required"
    normalized_payload = dict(payload)
    if event_type == "outcome":
        outcome_state = str(payload.get("state") or payload.get("status") or "").strip().lower()
        if outcome_state not in {"closed", "terminal", "open", "pending"}:
            try:
                _canonical_json(payload)
            except (TypeError, ValueError):
                return None, "event_not_json_serializable"
            return None, "outcome_state_invalid"
        if outcome_state in {"closed", "terminal"}:
            for field in ("pnl", "return_pct"):
                value = payload.get(field)
                if isinstance(value, bool):
                    return None, f"outcome_{field}_invalid"
                try:
                    numeric = Decimal(str(value))
                except (InvalidOperation, TypeError, ValueError):
                    return None, f"outcome_{field}_invalid"
                if not numeric.is_finite():
                    return None, f"outcome_{field}_invalid"
                normalized_payload[field] = float(numeric)
    normalized = {
        "event_id": event_id.strip(),
        "event_type": event_type,
        "occurred_at": parsed_at.astimezone(timezone.utc).isoformat(),
        "code": code.strip(),
        "signal_id": event.get("signal_id"),
        "recommendation_id": event.get("recommendation_id"),
        "fill_id": event.get("fill_id"),
        "source": source.strip(),
        "payload": normalized_payload,
    }
    try:
        normalized["event_fingerprint"] = _event_fingerprint(normalized)
    except (TypeError, ValueError):
        return None, "event_not_json_serializable"
    return normalized, None


class ExecutionLedger:
    """Host-local, locked JSONL store for execution lifecycle events."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path is not None else default_execution_ledger_path()

    def _read_unlocked(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"ok": True, "events": [], "errors": []}
        events: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        seen_event_ids: set[str] = set()
        for line_number, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError as exc:
                errors.append({"line_number": line_number, "reason": f"invalid_json:{exc.msg}"})
                continue
            normalized, error = _normalize_event(raw)
            if error or normalized is None:
                errors.append({"line_number": line_number, "reason": error or "event_invalid"})
                continue
            if raw.get("event_fingerprint") != normalized["event_fingerprint"]:
                errors.append({"line_number": line_number, "reason": "event_fingerprint_invalid"})
                continue
            if normalized["event_id"] in seen_event_ids:
                errors.append({"line_number": line_number, "reason": "duplicate_event_id"})
                continue
            seen_event_ids.add(normalized["event_id"])
            events.append(normalized)
        return {"ok": not errors, "events": events, "errors": errors}

    def read_with_diagnostics(self) -> dict[str, Any]:
        with _ledger_lock(self.path, exclusive=False):
            return self._read_unlocked()

    def append_event(self, event: dict[str, Any]) -> dict[str, Any]:
        normalized, error = _normalize_event(event)
        if error or normalized is None:
            return {"ok": False, "written": False, "error": error or "event_invalid"}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with _ledger_lock(self.path, exclusive=True):
            history = self._read_unlocked()
            if not history["ok"]:
                return {
                    "ok": False,
                    "written": False,
                    "error": "execution_ledger_history_invalid",
                    "diagnostics": history["errors"],
                }
            existing = next(
                (item for item in history["events"] if item["event_id"] == normalized["event_id"]),
                None,
            )
            if existing is not None:
                if existing["event_fingerprint"] == normalized["event_fingerprint"]:
                    return {
                        "ok": True,
                        "written": False,
                        "duplicate": True,
                        "conflict": False,
                        "event": existing,
                    }
                return {
                    "ok": False,
                    "written": False,
                    "duplicate": False,
                    "conflict": True,
                    "error": f"event_id conflict: {normalized['event_id']}",
                }
            encoded = json.dumps(
                normalized,
                ensure_ascii=False,
                sort_keys=True,
                allow_nan=False,
            )
            with self.path.open("a", encoding="utf-8") as ledger_file:
                ledger_file.write(encoded + "\n")
                ledger_file.flush()
                os.fsync(ledger_file.fileno())
            return {
                "ok": True,
                "written": True,
                "duplicate": False,
                "conflict": False,
                "event": normalized,
            }

    def join_attribution(
        self,
        *,
        portfolio_trade_events: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        history = self.read_with_diagnostics()
        if not history["ok"]:
            return {
                "ok": False,
                "error": "execution_ledger_history_invalid",
                "diagnostics": history["errors"],
                "chains": [],
                "attributed_count": 0,
                "unattributed_count": len(portfolio_trade_events or []),
                "strategy_metrics": _strategy_metrics([]),
            }
        return build_execution_attribution(
            history["events"],
            portfolio_trade_events=portfolio_trade_events,
        )

    def append_entry_authorization_chain(
        self,
        *,
        alert: dict[str, Any],
        signal: dict[str, Any],
        source: str = "target_scoring",
    ) -> dict[str, Any]:
        """Audit one deterministic signal/authorization/recommendation chain."""
        signal_id = str(signal.get("signal_id") or alert.get("signal_id") or "").strip()
        code = str(alert.get("stock_code") or alert.get("code") or "").strip()
        recommendation_id = recommendation_id_for_signal(signal_id)
        signal_time = _aware_iso(signal.get("created_at") or alert.get("quote_timestamp"))
        authorized_time = _aware_iso(signal.get("observed_at") or alert.get("quote_timestamp"))
        events = [
            {
                "event_id": f"evt_signal_{signal_id}",
                "event_type": "signal",
                "occurred_at": signal_time,
                "code": code,
                "signal_id": signal_id,
                "source": source,
                "payload": {
                    "action": alert.get("action"),
                    "playbook": alert.get("playbook"),
                    "setup_price": signal.get("setup_price"),
                    "trigger_price": signal.get("trigger_price"),
                },
            },
            {
                "event_id": f"evt_authorization_{signal_id}",
                "event_type": "authorization",
                "occurred_at": authorized_time,
                "code": code,
                "signal_id": signal_id,
                "source": source,
                "payload": {
                    "approved": True,
                    "state": "authorized",
                    "scope": "single_entry_signal",
                    "confirmations": signal.get("confirmations"),
                    "scorecard": alert.get("scorecard") or {},
                },
            },
            {
                "event_id": f"evt_recommendation_{recommendation_id}",
                "event_type": "recommendation",
                "occurred_at": authorized_time,
                "code": code,
                "signal_id": signal_id,
                "recommendation_id": recommendation_id,
                "source": source,
                "payload": {
                    "action": alert.get("action"),
                    "position_shares": alert.get("position_shares"),
                    "stop_loss": alert.get("stop_loss"),
                    "target_price": alert.get("target_price"),
                    "decision_basis": alert.get("decision_basis"),
                },
            },
        ]
        results = [self.append_event(event) for event in events]
        if not all(result.get("ok") for result in results):
            return {
                "ok": False,
                "recommendation_id": recommendation_id,
                "results": results,
            }
        return {
            "ok": True,
            "recommendation_id": recommendation_id,
            "results": results,
        }


def _single_index(events: list[dict[str, Any]], event_type: str, key: str) -> dict[str, list[dict[str, Any]]]:
    index: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        value = event.get(key)
        if event.get("event_type") == event_type and value:
            index.setdefault(str(value), []).append(event)
    return index


def _strategy_metrics(chains: list[dict[str, Any]]) -> dict[str, Any]:
    complete = [item for item in chains if item.get("status") == "attributed"]
    outcomes = [
        event.get("payload") or {}
        for item in complete
        for event in item.get("events", [])
        if event.get("event_type") == "outcome" and _outcome_is_terminal(event)
    ]
    pnls = [float(item.get("pnl") or 0) for item in outcomes]
    returns = [float(item.get("return_pct") or 0) for item in outcomes]
    return {
        "closed_count": len(outcomes),
        "total_pnl": round(sum(pnls), 2),
        "avg_return_pct": round(sum(returns) / len(returns), 2) if returns else 0.0,
        "win_rate_pct": round(sum(value > 0 for value in pnls) / len(pnls) * 100, 1)
        if pnls
        else 0.0,
        "metric_scope": "complete_execution_attribution_only",
    }


def _authorization_approved(event: dict[str, Any]) -> bool:
    payload = event.get("payload") or {}
    if payload.get("approved") is True:
        return True
    state = str(payload.get("state") or payload.get("status") or "").strip().lower()
    return state in {"approved", "authorized"}


def _event_time(event: dict[str, Any]) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(event.get("occurred_at") or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _outcome_state(event: dict[str, Any]) -> str:
    payload = event.get("payload") or {}
    return str(payload.get("state") or payload.get("status") or "").strip().lower()


def _outcome_is_terminal(event: dict[str, Any]) -> bool:
    return _outcome_state(event) in {"closed", "terminal"}


def _terminal_outcome_metrics_valid(event: dict[str, Any]) -> bool:
    payload = event.get("payload") or {}
    for field in ("pnl", "return_pct"):
        value = payload.get(field)
        if isinstance(value, bool):
            return False
        try:
            numeric = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            return False
        if not numeric.is_finite():
            return False
    return True


def _portfolio_event_fingerprint(event: dict[str, Any]) -> str | None:
    try:
        return hashlib.sha256(_canonical_json(event).encode("utf-8")).hexdigest()
    except (TypeError, ValueError):
        return None


def build_execution_attribution(
    events: list[dict[str, Any]],
    *,
    portfolio_trade_events: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    signal_index = _single_index(events, "signal", "signal_id")
    authorization_index = _single_index(events, "authorization", "signal_id")
    recommendation_index = _single_index(events, "recommendation", "recommendation_id")
    fill_index = _single_index(events, "fill", "fill_id")
    outcome_index = _single_index(events, "outcome", "fill_id")

    candidates: dict[str, dict[str, Any]] = {
        fill_id: rows[0] for fill_id, rows in fill_index.items()
    }
    portfolio_is_authoritative = portfolio_trade_events is not None
    portfolio_groups: dict[str, list[dict[str, Any]]] = {}
    for item in portfolio_trade_events or []:
        if isinstance(item, dict) and item.get("fill_id"):
            portfolio_groups.setdefault(str(item["fill_id"]), []).append(item)

    portfolio_conflicts: set[str] = set()
    portfolio_invalid_payloads: set[str] = set()
    for portfolio_fill_id, rows in portfolio_groups.items():
        fingerprints = {_portfolio_event_fingerprint(item) for item in rows}
        if None in fingerprints:
            portfolio_invalid_payloads.add(portfolio_fill_id)
        if len(fingerprints) > 1:
            portfolio_conflicts.add(portfolio_fill_id)
        candidates[portfolio_fill_id] = rows[0]

    portfolio_fill_ids = set(portfolio_groups)

    chains: list[dict[str, Any]] = []
    for fill_id, candidate in candidates.items():
        signal_id = candidate.get("signal_id")
        recommendation_id = candidate.get("recommendation_id")
        reasons: list[str] = []
        if not signal_id:
            reasons.append("signal_id_missing")
        if not recommendation_id:
            reasons.append("recommendation_id_missing")
        if portfolio_is_authoritative and fill_id not in portfolio_fill_ids:
            reasons.append("portfolio_fill_missing")
        if fill_id in portfolio_conflicts:
            reasons.extend(["portfolio_fill_conflict", "portfolio_fill_ambiguous"])
        if fill_id in portfolio_invalid_payloads:
            reasons.append("portfolio_fill_payload_invalid")

        signal_rows = signal_index.get(str(signal_id), []) if signal_id else []
        authorization_rows = authorization_index.get(str(signal_id), []) if signal_id else []
        recommendation_rows = (
            recommendation_index.get(str(recommendation_id), []) if recommendation_id else []
        )
        ledger_fill_rows = fill_index.get(fill_id, [])
        all_outcome_rows = outcome_index.get(fill_id, [])
        terminal_outcome_rows = [item for item in all_outcome_rows if _outcome_is_terminal(item)]
        pending_outcome_rows = [item for item in all_outcome_rows if not _outcome_is_terminal(item)]
        outcome_rows: list[dict[str, Any]] = []
        if len(terminal_outcome_rows) == 1:
            outcome_rows = terminal_outcome_rows
            if not _terminal_outcome_metrics_valid(terminal_outcome_rows[0]):
                reasons.append("outcome_metrics_invalid")
        elif len(terminal_outcome_rows) > 1:
            outcome_rows = terminal_outcome_rows[:1]
            reasons.append("outcome_event_ambiguous")
        elif pending_outcome_rows:
            outcome_rows = [
                max(
                    pending_outcome_rows,
                    key=lambda item: _event_time(item) or datetime.min.replace(tzinfo=timezone.utc),
                )
            ]
        else:
            reasons.append("outcome_event_missing")
        for label, rows in [
            ("signal_event", signal_rows),
            ("authorization_event", authorization_rows),
            ("recommendation_event", recommendation_rows),
            ("fill_event", ledger_fill_rows),
        ]:
            if not rows:
                reasons.append(f"{label}_missing")
            elif len(rows) > 1:
                reasons.append(f"{label}_ambiguous")

        if recommendation_rows and recommendation_rows[0].get("signal_id") != signal_id:
            reasons.append("recommendation_signal_mismatch")
        if ledger_fill_rows:
            ledger_fill = ledger_fill_rows[0]
            if ledger_fill.get("signal_id") != signal_id:
                reasons.append("fill_signal_mismatch")
            if ledger_fill.get("recommendation_id") != recommendation_id:
                reasons.append("fill_recommendation_mismatch")
            if fill_id in portfolio_fill_ids:
                if candidate.get("code") != ledger_fill.get("code"):
                    reasons.append("portfolio_fill_code_mismatch")
                if candidate.get("signal_id") != ledger_fill.get("signal_id"):
                    reasons.append("portfolio_fill_signal_mismatch")
                if candidate.get("recommendation_id") != ledger_fill.get("recommendation_id"):
                    reasons.append("portfolio_fill_recommendation_mismatch")

        if authorization_rows and not _authorization_approved(authorization_rows[0]):
            reasons.append("authorization_not_approved")

        selected_rows = [
            rows[0]
            for rows in [
                signal_rows,
                authorization_rows,
                recommendation_rows,
                ledger_fill_rows,
                outcome_rows,
            ]
            if rows
        ]
        ledger_code = ledger_fill_rows[0].get("code") if ledger_fill_rows else candidate.get("code")
        if any(event.get("code") != ledger_code for event in selected_rows):
            reasons.append("event_code_mismatch")

        if signal_rows and signal_rows[0].get("signal_id") != signal_id:
            reasons.append("signal_id_mismatch")
        if authorization_rows and authorization_rows[0].get("signal_id") != signal_id:
            reasons.append("authorization_signal_mismatch")
        if recommendation_rows:
            recommendation_event = recommendation_rows[0]
            if recommendation_event.get("recommendation_id") != recommendation_id:
                reasons.append("recommendation_id_mismatch")
        if ledger_fill_rows:
            ledger_fill = ledger_fill_rows[0]
            if ledger_fill.get("fill_id") != fill_id:
                reasons.append("fill_id_mismatch")
        if outcome_rows:
            outcome = outcome_rows[0]
            if outcome.get("signal_id") != signal_id:
                reasons.append("outcome_signal_mismatch")
            if outcome.get("recommendation_id") != recommendation_id:
                reasons.append("outcome_recommendation_mismatch")
            if outcome.get("fill_id") != fill_id:
                reasons.append("outcome_fill_mismatch")

        if len(selected_rows) == 5:
            event_times = [_event_time(event) for event in selected_rows]
            if any(event_time is None for event_time in event_times):
                reasons.append("lifecycle_timestamp_invalid")
            elif any(
                event_times[index] > event_times[index + 1]
                for index in range(len(event_times) - 1)
            ):
                reasons.append("lifecycle_time_not_monotonic")

        chain_events = selected_rows[:-1] + all_outcome_rows if outcome_rows else selected_rows
        if reasons:
            chain_status = "unattributed"
        elif terminal_outcome_rows:
            chain_status = "attributed"
        else:
            chain_status = "pending"
        chains.append({
            "fill_id": fill_id,
            "code": candidate.get("code"),
            "signal_id": signal_id,
            "recommendation_id": recommendation_id,
            "status": chain_status,
            "reasons": sorted(set(reasons)),
            "events": chain_events,
        })

    attributed_count = sum(item["status"] == "attributed" for item in chains)
    pending_count = sum(item["status"] == "pending" for item in chains)
    return {
        "ok": True,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "chains": chains,
        "attributed_count": attributed_count,
        "pending_count": pending_count,
        "unattributed_count": len(chains) - attributed_count - pending_count,
        "strategy_metrics": _strategy_metrics(chains),
    }
