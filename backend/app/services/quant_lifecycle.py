"""Production candidate/position lifecycle for scheduled trading assistance."""
from __future__ import annotations

import hashlib
import json
import math
import os
import fcntl
import tempfile
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path
from threading import RLock
from typing import Any
from zoneinfo import ZoneInfo

from app.config import PROJECT_ROOT
from app.services.strategy_profile import (
    calculate_stop_loss_price,
    calculate_target_price,
    get_strategy_profile,
)


def default_candidate_pool_path() -> Path:
    return Path(
        os.environ.get(
            "CONGXI_CANDIDATE_POOL_PATH",
            os.path.abspath(os.path.join(PROJECT_ROOT, "..", "data", "candidate_pool.json")),
        )
    )


def default_position_watch_path() -> Path:
    return Path(
        os.environ.get(
            "CONGXI_POSITION_WATCH_PATH",
            os.path.abspath(os.path.join(PROJECT_ROOT, "..", "data", "position_watch.json")),
        )
    )


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


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


def _clean_code(value: Any) -> str:
    return str(value or "").strip()


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _trigger_price_from_item(item: dict[str, Any]) -> float | None:
    evidence = item.get("evidence") if isinstance(item.get("evidence"), dict) else {}
    for candidate in (item.get("trigger_price"), evidence.get("trigger_price")):
        value = _to_float(candidate)
        if math.isfinite(value) and value > 0:
            return value
    return None


def lot_size_for_code(code: str) -> int:
    """Return minimum A-share board lot size used by this project."""
    clean = _clean_code(code).replace("sh", "").replace("sz", "").replace("bj", "")
    if clean.startswith(("688", "689")):
        return 200
    return 100


def _execution_gate(
    code: str,
    *,
    current_price: float | None = None,
    available_cash: float = 0,
    total_assets: float = 0,
) -> dict[str, Any]:
    """Classify account executability before a target reaches the trading pool."""
    lot_size = lot_size_for_code(code)
    price = _to_float(current_price)
    lot_value = round(price * lot_size, 2) if price > 0 else 0.0
    profile = get_strategy_profile()
    assets = _to_float(total_assets, _to_float(available_cash))
    cash = _to_float(available_cash)
    single_limit_pct = _to_float(profile.get("single_position_limit_pct"), 50)
    single_limit = round(assets * (single_limit_pct / 100), 2) if assets else cash
    reserve_cash = assets * (_to_float(profile.get("cash_reserve_pct"), 10) / 100) if assets else 0
    executable_budget = max(0.0, min(cash - reserve_cash, single_limit))
    result = {
        "lot_size": lot_size,
        "current_price": price,
        "lot_value": lot_value,
        "available_cash": round(cash, 2),
        "total_assets": round(assets, 2),
        "executable_budget": round(executable_budget, 2),
        "executable": False,
        "block_reason": "",
    }
    if price <= 0:
        result["block_reason"] = "price_missing"
    elif lot_value > cash or lot_value > executable_budget:
        result["block_reason"] = "lot_size_exceeded"
    else:
        result["executable"] = True
    return result


def normalize_alert_level(level: str | None) -> str:
    if level in ("high", "mid", "low"):
        return level
    if level == "medium":
        return "mid"
    return "low"


def _has_data_insufficient_marker(rec: dict[str, Any]) -> bool:
    fields = [rec.get("buy_range"), rec.get("stop_loss"), rec.get("target"), rec.get("reason")]
    return any("数据不足" in str(field or "") or "观望" in str(field or "") for field in fields)


LONG_HORIZON_STATUSES = {
    "long_research",
    "long_watch",
    "accumulation_zone",
    "tactical_watch",
    "thesis_review",
    "exit_candidate",
}

ENTRY_ACTIONS = {"actionable", "add_position"}
ENTRY_CONFIRMATIONS_REQUIRED = 2
ENTRY_SIGNAL_TTL_MINUTES = 30
REQUIRED_SCORING_SOURCES = {"quote", "kline", "fund_flow", "financial"}
MARKET_TIMEZONE = ZoneInfo("Asia/Shanghai")
_PROCESS_POOL_LOCK = RLock()


@contextmanager
def _pool_lock(path: Path):
    lock_path = path.with_name(f".{path.name}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with _PROCESS_POOL_LOCK, lock_path.open("a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _locked_store_mutation(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with _pool_lock(self.path):
            return method(self, *args, **kwargs)

    return wrapped


def _normalize_scoring_decision(value: dict[str, Any] | None) -> dict[str, Any]:
    decision = dict(value) if isinstance(value, dict) else {}
    source_status = decision.get("source_status")
    source_status = source_status if isinstance(source_status, dict) else {}
    missing_data = decision.get("missing_data")
    missing_data = missing_data if isinstance(missing_data, list) else []
    valid = (
        decision.get("action") in {"buy", "add"}
        and _to_float(decision.get("score")) >= 70
        and not missing_data
        and all(source_status.get(key) == "ok" for key in REQUIRED_SCORING_SOURCES)
    )
    return {
        **decision,
        "source_status": source_status,
        "missing_data": missing_data,
        "authorization_valid": valid,
        "authorization_reason": "full_scorecard_passed" if valid else "scorecard_missing_or_incomplete",
    }


class CandidatePoolStore:
    """File-backed production candidate pool.

    This is separate from the Sentinel/Serenity research pool: research pools hold
    themes; this store holds executable lifecycle state for scheduled scans.
    """

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        execution_ledger_path: str | Path | None = None,
    ):
        self.path = Path(path) if path is not None else default_candidate_pool_path()
        if execution_ledger_path is not None:
            self.execution_ledger_path = Path(execution_ledger_path)
        elif path is not None:
            self.execution_ledger_path = self.path.parent / "execution_ledger.jsonl"
        else:
            from app.services.execution_ledger import default_execution_ledger_path

            self.execution_ledger_path = default_execution_ledger_path()

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

    @_locked_store_mutation
    def update(self, updater) -> Any:
        """Run one read-modify-write transaction under the pool lock."""
        payload = self.load()
        result = updater(payload)
        if result:
            self.save(payload)
        return result

    def get(self, code: str) -> dict[str, Any] | None:
        return self.load().get("items", {}).get(_clean_code(code))

    def active_items(self) -> list[dict[str, Any]]:
        items = self.load().get("items", {})
        return [
            item
            for item in items.values()
            if isinstance(item, dict)
            and item.get("status")
            not in {
                "removed",
                "expired",
                "research_only",
                "research_reference",
                "cooldown_after_loss",
                *LONG_HORIZON_STATUSES,
            }
        ]

    @_locked_store_mutation
    def upsert_recommendations(self, recommendations: list[dict[str, Any]], source: str) -> int:
        payload = self.load()
        items = payload.setdefault("items", {})
        count = 0
        for rec in recommendations or []:
            if not isinstance(rec, dict):
                continue
            code = _clean_code(rec.get("code"))
            if not code:
                continue
            existing = items.get(code, {})
            status = existing.get("status", "watching")
            watch_reason = existing.get("watch_reason", "")
            if _has_data_insufficient_marker(rec):
                status = "watching"
                watch_reason = "data_insufficient"
            item = {
                **existing,
                "code": code,
                "name": rec.get("name") or existing.get("name") or code,
                "status": status,
                "watch_reason": watch_reason or "new_signal",
                "source": source,
                "evidence": {
                    "reason": rec.get("reason", ""),
                    "buy_range": rec.get("buy_range", ""),
                    "stop_loss": rec.get("stop_loss", ""),
                    "target": rec.get("target", ""),
                    "level": rec.get("level", ""),
                    "data_source": rec.get("data_source", ""),
                },
                "last_recommendation": rec,
                "updated_at": _now(),
            }
            item.setdefault("created_at", _now())
            item.setdefault("decision_history", [])
            items[code] = item
            count += 1
        if count:
            self.save(payload)
        return count

    def record_decision(self, code: str, status: str, alert: dict[str, Any] | None = None) -> None:
        self.record_scan(code, status=status, alert=alert)

    @_locked_store_mutation
    def record_scan(
        self,
        code: str,
        *,
        status: str | None = None,
        alert: dict[str, Any] | None = None,
        entry_signal: dict[str, Any] | None = None,
    ) -> None:
        """Persist a scan without conflating lifecycle authorization with signal state."""
        payload = self.load()
        item = payload.setdefault("items", {}).get(_clean_code(code))
        if not item:
            return
        if status is not None:
            item["status"] = status
        item["last_scanned_at"] = _now()
        if entry_signal is not None:
            item["entry_signal"] = {**entry_signal, "updated_at": _now()}
        if alert:
            item["last_alert"] = alert
            item.setdefault("decision_history", []).append({"time": _now(), **alert})
            item["decision_history"] = item["decision_history"][-30:]
        self.save(payload)


def target_production_eligibility(
    target: dict[str, Any] | None,
    *,
    previous: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the durable research provenance gate for a target-pool item."""
    current = target if isinstance(target, dict) else {}
    prior = previous if isinstance(previous, dict) else {}
    prior_provenance = prior.get("provenance") if isinstance(prior.get("provenance"), dict) else {}
    prior_gate = (
        prior.get("production_eligibility")
        if isinstance(prior.get("production_eligibility"), dict)
        else {}
    )

    def auditable_approval(payload: dict[str, Any]) -> dict[str, Any]:
        # Candidate payloads are untrusted. Manual production approval requires
        # an authenticated approval ledger, which this store does not yet have.
        # Fail closed instead of accepting caller-provided identity strings.
        return {}

    def research_only_marker(payload: dict[str, Any]) -> bool:
        provenance = payload.get("provenance") if isinstance(payload.get("provenance"), dict) else {}
        if payload.get("research_only") is True or provenance.get("research_only") is True:
            return True
        status = str(payload.get("status") or "").strip().lower().replace("-", "_")
        if status in {"research_only", "research_reference", "long_research", "hypothesis", "shadow"}:
            return True
        source = str(payload.get("source") or "").strip().lower()
        if any(marker in source for marker in ("gbrain", "sentinel", "serenity")):
            return True
        evidence = payload.get("evidence") if isinstance(payload.get("evidence"), dict) else {}
        stage_values = [
            payload.get("stage"),
            payload.get("state"),
            payload.get("mode"),
            payload.get("lifecycle_stage"),
            evidence.get("stage"),
            evidence.get("state"),
            evidence.get("mode"),
            evidence.get("lifecycle_stage"),
        ]
        normalized_stages = {
            str(value or "").strip().lower().replace("-", "_")
            for value in stage_values
            if str(value or "").strip()
        }
        return bool(normalized_stages & {"hypothesis", "shadow", "shadow_mode", "paper_only", "unpromoted"})

    research_only = (
        prior_gate.get("eligible") is False
        or research_only_marker(prior)
        or research_only_marker(current)
    )
    approval = auditable_approval(current) or auditable_approval(prior)
    status_values = {
        str(current.get("status") or "").strip().lower(),
        str(prior.get("status") or "").strip().lower(),
        str(prior_provenance.get("original_status") or "").strip().lower(),
    }
    cooldown_after_loss = "cooldown_after_loss" in status_values
    original_status = str(
        prior_provenance.get("original_status")
        or prior.get("status")
        or current.get("status")
        or "candidate"
    )
    original_source = str(
        prior_provenance.get("original_source")
        or prior.get("source")
        or current.get("source")
        or "manual"
    )
    return {
        "eligible": bool(approval) or (not research_only and not cooldown_after_loss),
        "research_only": research_only,
        "approved": bool(approval),
        "original_status": original_status,
        "original_source": original_source,
        "reason": (
            "auditable_production_approval"
            if approval
            else "cooldown_after_loss"
            if cooldown_after_loss
            else "research_only_provenance"
            if research_only
            else "production_eligible"
        ),
        "approval": approval,
    }


class TargetPoolStore(CandidatePoolStore):
    """Compatibility wrapper for the production stock lifecycle pool.

    `CandidatePoolStore` remains as the MVP file-backed implementation. The
    business meaning is broader: it is the total target lifecycle pool, where
    "candidate" is only one status.
    """

    VALID_STATUSES = {
        "research_only",
        "research_reference",
        "candidate",
        "watching",
        "executable",
        "actionable",
        "blocked_chasing",
        "blocked_high_position",
        "cooldown_after_loss",
        "risk_budget_too_small",
        "regime_blocks_dip",
        *LONG_HORIZON_STATUSES,
        "position",
        "removed",
        "expired",
    }

    @staticmethod
    def production_eligibility_for(
        target: dict[str, Any] | None,
        *,
        previous: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return target_production_eligibility(target, previous=previous)

    @_locked_store_mutation
    def upsert_target(
        self,
        *,
        code: str,
        name: str,
        status: str = "candidate",
        source: str = "manual",
        evidence_ids: list[str] | None = None,
        evidence: dict[str, Any] | None = None,
        sentinel: dict[str, Any] | None = None,
        serenity: dict[str, Any] | None = None,
        production_approval: dict[str, Any] | None = None,
        scoring_decision: dict[str, Any] | None = None,
        current_price: float | None = None,
        available_cash: float = 0,
        total_assets: float = 0,
    ) -> bool:
        clean = _clean_code(code)
        if not clean:
            return False
        payload = self.load()
        items = payload.setdefault("items", {})
        existing = items.get(clean, {})
        existing_evidence = existing.get("evidence") if isinstance(existing.get("evidence"), dict) else {}
        incoming_evidence = evidence if isinstance(evidence, dict) else {}
        normalized_status = status if status in self.VALID_STATUSES else "candidate"
        normalized_scoring = (
            _normalize_scoring_decision(scoring_decision)
            if scoring_decision is not None or source == "target_scoring"
            else existing.get("scoring_decision") or {}
        )
        if (
            source == "target_scoring"
            and normalized_status == "executable"
            and normalized_scoring.get("authorization_valid") is not True
        ):
            normalized_status = "watching"
        execution = _execution_gate(
            clean,
            current_price=current_price,
            available_cash=available_cash,
            total_assets=total_assets,
        )
        if source == "sentinel_serenity" and normalized_status in {"candidate", "watching", "executable"}:
            normalized_status = "research_reference"
        if current_price is not None and execution["executable"] and normalized_status == "candidate":
            normalized_status = "watching"
        elif current_price is not None and not execution["executable"] and normalized_status in {"candidate", "watching", "executable"}:
            normalized_status = "research_reference"
        gate = self.production_eligibility_for(
            {
                "status": status,
                "source": source,
                "evidence": incoming_evidence,
                "production_approval": production_approval or {},
            },
            previous=existing,
        )
        if existing.get("status") == "cooldown_after_loss" and not gate["approved"]:
            normalized_status = "cooldown_after_loss"
        elif not gate["eligible"] and normalized_status in {"candidate", "watching", "executable", "actionable"}:
            normalized_status = "research_reference"
        merged_evidence_ids = list(dict.fromkeys([
            *(existing.get("evidence_ids") or []),
            *(evidence_ids or []),
        ]))
        item = {
            **existing,
            "code": clean,
            "name": name or existing.get("name") or clean,
            "status": normalized_status,
            "source": source,
            "evidence": {**existing_evidence, **incoming_evidence},
            "evidence_ids": merged_evidence_ids,
            "sentinel": {**(existing.get("sentinel") or {}), **(sentinel or {})},
            "serenity": {**(existing.get("serenity") or {}), **(serenity or {})},
            "execution": {**(existing.get("execution") or {}), **execution},
            "provenance": {
                "original_status": gate["original_status"],
                "original_source": gate["original_source"],
                "research_only": gate["research_only"],
            },
            "production_eligibility": gate,
            "production_approval": gate["approval"],
            "scoring_decision": normalized_scoring,
            "updated_at": _now(),
        }
        item.setdefault("created_at", _now())
        item.setdefault("decision_history", [])
        items[clean] = item
        self.save(payload)
        return True


class PositionWatchStore:
    """File-backed stop-loss/take-profit plan store for real positions."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path is not None else default_position_watch_path()

    def load(self) -> dict[str, Any]:
        payload = _read_json(self.path, {"version": 1, "updated_at": "", "items": {}})
        payload.setdefault("version", 1)
        payload.setdefault("updated_at", "")
        payload.setdefault("items", {})
        return payload

    def save(self, payload: dict[str, Any]) -> None:
        payload["updated_at"] = _now()
        _write_json(self.path, payload)

    def get(self, code: str) -> dict[str, Any] | None:
        return self.load().get("items", {}).get(_clean_code(code))

    @_locked_store_mutation
    def upsert_plan(
        self,
        code: str,
        name: str,
        *,
        stop_loss_price: float | None = None,
        target_price: float | None = None,
        entry_price: float | None = None,
        source: str = "manual",
    ) -> None:
        payload = self.load()
        items = payload.setdefault("items", {})
        clean = _clean_code(code)
        existing = items.get(clean, {})
        resolved_stop = stop_loss_price if stop_loss_price is not None else existing.get("stop_loss_price")
        resolved_target = target_price if target_price is not None else existing.get("target_price")
        if resolved_target is None:
            entry = _to_float(entry_price)
            stop = _to_float(resolved_stop)
            if entry <= 0 and stop > 0:
                entry = stop / 0.95
            if entry > 0:
                resolved_target = round(entry * 1.08, 2)
        items[clean] = {
            **existing,
            "code": clean,
            "name": name or existing.get("name") or clean,
            "stop_loss_price": resolved_stop,
            "target_price": resolved_target,
            "source": source,
            "updated_at": _now(),
        }
        items[clean].setdefault("created_at", _now())
        self.save(payload)


def _is_limit_up_or_chasing(price: float, quote: dict[str, Any], change_pct: float) -> bool:
    limit_up = _to_float(quote.get("limit_up"))
    if limit_up > 0 and price >= limit_up * 0.995:
        return True
    return change_pct >= 9.0


def _looks_like_breakout_quote(quote: dict[str, Any]) -> bool:
    return (
        _to_float(quote.get("change_pct")) >= 3
        and _to_float(quote.get("vol_ratio")) >= 2
        and _to_float(quote.get("amount_wan") or quote.get("amount")) >= 10000
    )


def _has_recent_kline(item: dict[str, Any], min_bars: int = 10) -> bool:
    bars = ((item.get("kline") or {}).get("bars") or [])
    return isinstance(bars, list) and len(bars) >= min_bars


def _quote_is_usable_for_entry(quote: dict[str, Any]) -> bool:
    if _to_float(quote.get("price")) <= 0:
        return False
    freshness = str(quote.get("freshness") or "").strip().lower()
    return freshness == "fresh"


def _quote_scan_id(quote: dict[str, Any]) -> str:
    return str(quote.get("quote_timestamp") or quote.get("captured_at") or _now()[:16])


def _stable_dip_setup_price(item: dict[str, Any]) -> float | None:
    kline = item.get("kline") if isinstance(item.get("kline"), dict) else {}
    bars = kline.get("bars") if isinstance(kline.get("bars"), list) else []
    recent = [bar for bar in bars[-5:] if isinstance(bar, dict)]
    lows = [_to_float(bar.get("low") or bar.get("close")) for bar in recent]
    lows = [value for value in lows if math.isfinite(value) and value > 0]
    if len(lows) >= 3:
        support = max(lows[:-1] or lows)
        if support > 0:
            return support
    evidence = item.get("evidence") if isinstance(item.get("evidence"), dict) else {}
    for candidate in (
        item.get("stop_loss_price"),
        evidence.get("stop_loss_price"),
        evidence.get("stop_loss"),
    ):
        stop_price = _to_float(candidate)
        if math.isfinite(stop_price) and stop_price > 0:
            return stop_price
    return None


def _stable_entry_setup_price(item: dict[str, Any], playbook: dict[str, Any]) -> float | None:
    trigger_price = _trigger_price_from_item(item)
    if trigger_price is not None:
        return trigger_price
    if str(playbook.get("playbook") or "") == "dip_entry":
        return _stable_dip_setup_price(item)
    return None


def _candidate_alert(
    item: dict[str, Any],
    quote: dict[str, Any],
    available_cash: float,
    total_assets: float = 0,
    position: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    from app.services.market_regime import evaluate_market_regime
    from app.services.playbook_engine import select_playbook
    from app.services.position_sizing import calculate_position_size

    price = _to_float(quote.get("price"))
    if price <= 0:
        return None

    code = item.get("code", "")
    name = item.get("name", code)
    change_pct = _to_float(quote.get("change_pct"))
    vol_ratio = _to_float(quote.get("vol_ratio"))
    amount_wan = _to_float(quote.get("amount_wan") or quote.get("amount"))
    lot_size = lot_size_for_code(code)
    lot_value = price * lot_size
    affordable = lot_value <= available_cash
    profile = get_strategy_profile()
    stop_loss = calculate_stop_loss_price(price, profile)
    target_price = calculate_target_price(price, profile)
    snapshot = {
        "code": code,
        "name": name,
        "quote": quote,
        "kline": item.get("kline") or {},
        "fund_flow": item.get("fund_flow") or {},
        "market_regime": item.get("market_regime") or {},
        "trigger_price": _trigger_price_from_item(item),
    }
    playbook = select_playbook(snapshot)
    setup_price = _stable_entry_setup_price(item, playbook)
    regime = evaluate_market_regime(snapshot)
    sizing = calculate_position_size(
        code=code,
        entry_price=price,
        stop_loss=stop_loss,
        available_cash=available_cash,
        total_assets=total_assets,
        profile=profile,
    )
    held_shares = int(_to_float((position or {}).get("shares")))
    held_value = _to_float((position or {}).get("market_value"))
    is_existing_position = held_shares > 0 or held_value > 0
    single_limit = _to_float(total_assets) * (_to_float(profile.get("single_position_limit_pct"), 50) / 100)

    base = {
        "stock_code": code,
        "stock_name": name,
        "price": price,
        "change_pct": change_pct,
        "vol_ratio": vol_ratio,
        "amount_wan": amount_wan,
        "lot_value": round(lot_value, 2),
        "lot_size": lot_size,
        "affordable": affordable,
        "playbook": playbook.get("playbook", "watch"),
        "position_amount": sizing.get("position_amount", 0),
        "position_shares": sizing.get("shares", 0),
        "risk_budget": sizing.get("risk_budget", 0),
        "risk_amount": sizing.get("risk_amount", 0),
        "stop_loss": stop_loss,
        "target_price": target_price,
        "trigger_price": _trigger_price_from_item(item),
        "setup_price": setup_price,
        "quote_timestamp": quote.get("quote_timestamp"),
        "quote_freshness": quote.get("freshness"),
        "scan_id": _quote_scan_id(quote),
    }

    if _is_limit_up_or_chasing(price, quote, change_pct):
        return {
            **base,
            "level": "high",
            "action": "blocked_chasing",
            "message": f"{name}({code}) 涨幅{change_pct:+.2f}%，接近涨停，禁止追高，转入次日观察。",
            "suggestion": "禁止追高，等回落或次日重新评估",
        }

    if playbook.get("block_reason") == "blocked_high_position":
        return {
            **base,
            "level": "low",
            "action": "blocked_high_position",
            "message": f"{name}({code}) {playbook.get('reason')}",
            "suggestion": playbook.get("next_signal") or "不追买；等待回踩确认后重新评分",
        }

    if playbook.get("playbook") == "dip_entry" and not regime.get("can_dip", True):
        return {
            **base,
            "level": "low",
            "action": "regime_blocks_dip",
            "message": f"{name}({code}) 低吸形态出现，但{regime.get('reason')} 暂不买入。",
            "suggestion": "等大盘止跌、板块相对强度修复后再复核",
        }

    if playbook.get("triggered") and sizing.get("block_reason") == "risk_budget_too_small":
        return {
            **base,
            "level": "low",
            "action": "risk_budget_too_small",
            "message": f"{name}({code}) {playbook.get('playbook')} 触发，但一手风险超过预算。",
            "suggestion": f"等待价格回落或止损距离收窄，单笔风险预算约¥{sizing.get('risk_budget', 0):.2f}",
        }

    if affordable and playbook.get("triggered") and sizing.get("position_amount", 0) > 0:
        if setup_price is None:
            return None
        if is_existing_position:
            next_lot_value = price * lot_size
            if single_limit > 0 and held_value + next_lot_value > single_limit:
                return {
                    **base,
                    "level": "low",
                    "action": "position_limit_reached",
                    "message": (
                        f"{name}({code}) 已持仓且{playbook.get('playbook')}触发，但加一手后"
                        f"仓位约¥{held_value + next_lot_value:.2f}，超过单票仓位上限¥{single_limit:.2f}。"
                    ),
                    "suggestion": "不加仓；等待仓位降下来或总资产提升后再复核",
                }
            return {
                **base,
                "level": "mid",
                "action": "add_position",
                "message": (
                    f"{name}({code}) 已持仓，{playbook.get('playbook')} 触发，现价¥{price:.2f}，"
                    f"可人工复核加仓{int(sizing.get('shares', 0))}股，风险约¥{sizing.get('risk_amount', 0):.2f}。"
                ),
                "suggestion": f"加仓前确认未超单票上限；新增仓位止损¥{stop_loss:.2f}，目标¥{target_price:.2f}",
            }
        return {
            **base,
            "level": "mid",
            "action": "actionable",
            "message": (
                f"{name}({code}) {playbook.get('playbook')} 触发，现价¥{price:.2f}，"
                f"建议{int(sizing.get('shares', 0))}股，风险约¥{sizing.get('risk_amount', 0):.2f}。"
            ),
            "suggestion": f"人工复核后可试仓；止损¥{stop_loss:.2f}，目标¥{target_price:.2f}",
        }

    return None


def _entry_is_authorized(item: dict[str, Any]) -> bool:
    """Only a full target-scoring pass or audited approval may authorize entry alerts."""
    if str(item.get("status") or "") != "executable":
        return False
    gate = item.get("production_eligibility")
    if not isinstance(gate, dict) or gate.get("eligible") is not True:
        return False
    if str(item.get("source") or "") == "target_scoring":
        scorecard = item.get("scoring_decision")
        return isinstance(scorecard, dict) and scorecard.get("authorization_valid") is True
    return False


def _prior_entry_signal(item: dict[str, Any]) -> dict[str, Any]:
    signal = item.get("entry_signal")
    if isinstance(signal, dict):
        return signal
    last_alert = item.get("last_alert")
    if str(item.get("status") or "") in ENTRY_ACTIONS or (
        isinstance(last_alert, dict) and last_alert.get("action") in ENTRY_ACTIONS
    ):
        return {
            "state": "active",
            "action": (last_alert or {}).get("action") or item.get("status"),
            "playbook": (last_alert or {}).get("playbook") or "entry",
            "confirmations": ENTRY_CONFIRMATIONS_REQUIRED,
            "legacy": True,
        }
    return {}


def _entry_side(action: Any) -> str:
    return "add" if str(action or "") == "add_position" else "buy"


def _entry_observed_at(alert: dict[str, Any]) -> str:
    return str(alert.get("quote_timestamp") or alert.get("scan_id") or _now())


def _parse_signal_time(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=MARKET_TIMEZONE)
    return parsed.astimezone(timezone.utc)


def _entry_expires_at(created_at: str) -> str:
    created = _parse_signal_time(created_at) or datetime.now(timezone.utc)
    return (created + timedelta(minutes=ENTRY_SIGNAL_TTL_MINUTES)).isoformat()


def _entry_setup_price(alert: dict[str, Any]) -> float | None:
    for candidate in (alert.get("trigger_price"), alert.get("setup_price")):
        value = _to_float(candidate)
        if math.isfinite(value) and value > 0:
            return value
    return None


def _signal_identity_payload(
    alert: dict[str, Any],
    *,
    first_scan_id: str,
) -> dict[str, Any]:
    setup_price = _entry_setup_price(alert)
    return {
        "code": str(alert.get("stock_code") or ""),
        "playbook": str(alert.get("playbook") or "entry"),
        "side": _entry_side(alert.get("action")),
        "setup_price": round(setup_price, 4) if setup_price is not None else None,
        "first_scan_id": first_scan_id,
    }


def _signal_id(alert: dict[str, Any], *, first_scan_id: str) -> str:
    canonical = json.dumps(
        _signal_identity_payload(alert, first_scan_id=first_scan_id),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"sig_{hashlib.sha256(canonical.encode('utf-8')).hexdigest()[:20]}"


def _same_entry_setup(prior: dict[str, Any], alert: dict[str, Any]) -> bool:
    prior_setup = _entry_setup_price(prior)
    alert_setup = _entry_setup_price(alert)
    return (
        prior_setup is not None
        and alert_setup is not None
        and prior.get("action") == alert.get("action")
        and prior.get("playbook") == alert.get("playbook")
        and math.isclose(prior_setup, alert_setup, rel_tol=0, abs_tol=0.0001)
    )


def _entry_signal_time_state(prior: dict[str, Any], alert: dict[str, Any]) -> str:
    expires = _parse_signal_time(prior.get("expires_at") or prior.get("valid_until"))
    observed = _parse_signal_time(_entry_observed_at(alert))
    if expires is None or observed is None:
        return "invalid"
    return "expired" if observed >= expires else "valid"


def _is_strictly_new_observation(prior: dict[str, Any], alert: dict[str, Any]) -> bool:
    previous = _parse_signal_time(prior.get("observed_at") or prior.get("scan_id"))
    current = _parse_signal_time(_entry_observed_at(alert))
    return previous is not None and current is not None and current > previous


def _entry_signal_payload(
    alert: dict[str, Any],
    *,
    state: str,
    confirmations: int,
    reason: str = "",
    prior: dict[str, Any] | None = None,
) -> dict[str, Any]:
    prior = prior if isinstance(prior, dict) else {}
    candidate_observed_at = _entry_observed_at(alert)
    observation_advanced = not prior or _is_strictly_new_observation(prior, alert)
    observed_at = (
        candidate_observed_at
        if observation_advanced
        else str(prior.get("observed_at") or prior.get("scan_id") or candidate_observed_at)
    )
    first_scan_id = str(prior.get("first_scan_id") or alert.get("scan_id") or observed_at)
    created_at = str(prior.get("created_at") or observed_at)
    trigger_price = _to_float(prior.get("trigger_price"), _to_float(alert.get("trigger_price")))
    setup_price = _entry_setup_price(prior) or _entry_setup_price(alert)
    return {
        "state": state,
        "action": prior.get("action") or alert.get("action"),
        "playbook": prior.get("playbook") or alert.get("playbook"),
        "side": prior.get("side") or _entry_side(alert.get("action")),
        "trigger_price": trigger_price if trigger_price > 0 else None,
        "setup_price": setup_price,
        "signal_id": prior.get("signal_id") or _signal_id(alert, first_scan_id=first_scan_id),
        "first_scan_id": first_scan_id,
        "created_at": created_at,
        "observed_at": observed_at,
        "expires_at": prior.get("expires_at") or _entry_expires_at(created_at),
        "confirmations": confirmations,
        "last_price": alert.get("price"),
        "scan_id": alert.get("scan_id") if observation_advanced else prior.get("scan_id"),
        "reason": reason,
    }


def _entry_cancelled_alert(
    item: dict[str, Any],
    quote: dict[str, Any],
    prior: dict[str, Any],
    *,
    reason: str,
) -> dict[str, Any]:
    code = str(item.get("code") or "")
    name = str(item.get("name") or code)
    price = _to_float(quote.get("price"))
    return {
        "stock_code": code,
        "stock_name": name,
        "price": price,
        "level": "high",
        "action": "entry_cancelled",
        "playbook": prior.get("playbook") or "entry",
        "signal_id": prior.get("signal_id"),
        "trigger_price": prior.get("trigger_price"),
        "scan_id": _quote_scan_id(quote),
        "quote_timestamp": quote.get("quote_timestamp"),
        "message": f"{name}({code}) 买入信号已失效（{reason}），撤销未成交买入计划。",
        "suggestion": "若尚未买入，不再执行原计划；若已经成交，立即转入持仓止损/止盈管理。",
    }


async def evaluate_candidate_pool(
    store: CandidatePoolStore,
    quote_source: Any,
    *,
    available_cash: float,
    total_assets: float = 0,
    positions: dict[str, dict[str, Any]] | None = None,
    entry_gate: dict[str, Any] | None = None,
) -> dict[str, Any]:
    items = store.active_items()
    codes = [item["code"] for item in items if item.get("code")]
    if not codes:
        return {"scanned": 0, "alerts": []}
    quotes = await quote_source.fetch_batch(codes)
    alerts: list[dict[str, Any]] = []
    for item in items:
        code = item.get("code", "")
        quote = quotes.get(code) or {}
        prior_signal = _prior_entry_signal(item)
        prior_state = str(prior_signal.get("state") or "")
        if isinstance(entry_gate, dict) and entry_gate.get("entry_allowed") is False:
            gate_audit = {
                "state": entry_gate.get("state"),
                "reasons": list(entry_gate.get("reasons") or []),
                "target_date": entry_gate.get("target_date"),
            }
            if prior_state == "active":
                cancelled = _entry_cancelled_alert(
                    item,
                    quote,
                    prior_signal,
                    reason="统一入场闸门已阻断",
                )
                cancelled["reason"] = "visible_decision_gate_blocked"
                alerts.append(cancelled)
                store.record_scan(
                    code,
                    status="watching" if item.get("status") in ENTRY_ACTIONS else None,
                    alert=cancelled,
                    entry_signal={
                        **_entry_signal_payload(
                            cancelled,
                            state="cancelled",
                            confirmations=0,
                            reason="visible_decision_gate_blocked",
                            prior=prior_signal,
                        ),
                        "decision_gate": gate_audit,
                    },
                )
            elif prior_state == "pending":
                store.record_scan(
                    code,
                    entry_signal={
                        **prior_signal,
                        "state": "cancelled",
                        "confirmations": 0,
                        "last_price": _to_float(quote.get("price")),
                        "scan_id": _quote_scan_id(quote),
                        "observed_at": str(quote.get("quote_timestamp") or _quote_scan_id(quote)),
                        "reason": "visible_decision_gate_blocked",
                        "decision_gate": gate_audit,
                    },
                )
            else:
                store.record_scan(
                    code,
                    entry_signal={
                        **prior_signal,
                        "state": "decision_gate_blocked",
                        "action": prior_signal.get("action"),
                        "playbook": prior_signal.get("playbook"),
                        "confirmations": 0,
                        "last_price": _to_float(quote.get("price")),
                        "scan_id": _quote_scan_id(quote),
                        "observed_at": str(quote.get("quote_timestamp") or _quote_scan_id(quote)),
                        "reason": "visible_decision_gate_blocked",
                        "decision_gate": gate_audit,
                    },
                )
            continue
        if prior_state == "active" and not _entry_is_authorized(item):
            cancelled = _entry_cancelled_alert(
                item,
                quote,
                prior_signal,
                reason="缺少完整评分的可执行授权",
            )
            alerts.append(cancelled)
            store.record_scan(
                code,
                status="watching" if item.get("status") in ENTRY_ACTIONS else None,
                alert=cancelled,
                entry_signal=_entry_signal_payload(
                    cancelled,
                    state="cancelled",
                    confirmations=0,
                    reason="authorization_lost",
                    prior=prior_signal,
                ),
            )
            continue
        if not _quote_is_usable_for_entry(quote):
            if prior_state == "active":
                unavailable_signal = {
                    **prior_signal,
                    "reason": "quote_unavailable",
                }
            elif prior_state == "pending":
                unavailable_signal = {
                    **prior_signal,
                    "state": "cancelled",
                    "confirmations": 0,
                    "reason": "quote_unavailable_before_confirmation",
                }
            else:
                unavailable_signal = {
                    "state": "data_unavailable",
                    "action": None,
                    "playbook": None,
                    "confirmations": 0,
                    "last_price": _to_float(quote.get("price")),
                    "scan_id": _quote_scan_id(quote),
                    "reason": "quote_unavailable",
                }
            store.record_scan(code, entry_signal=unavailable_signal)
            continue
        scan_item = item
        if _looks_like_breakout_quote(quote) and not _has_recent_kline(item) and hasattr(quote_source, "fetch_kline"):
            try:
                kline = await quote_source.fetch_kline(code, "day", count=20)
                if isinstance(kline, dict) and kline.get("bars"):
                    scan_item = {**item, "kline": kline}
            except Exception:
                scan_item = item
        alert = _candidate_alert(scan_item, quote, available_cash, total_assets, (positions or {}).get(code))
        observation = alert or {
            "stock_code": code,
            "action": prior_signal.get("action"),
            "playbook": prior_signal.get("playbook"),
            "price": quote.get("price"),
            "trigger_price": prior_signal.get("trigger_price"),
            "scan_id": _quote_scan_id(quote),
            "quote_timestamp": quote.get("quote_timestamp"),
        }
        signal_time_state = (
            _entry_signal_time_state(prior_signal, observation)
            if prior_state in {"pending", "active"}
            else "valid"
        )
        if prior_state in {"pending", "active"} and signal_time_state in {"expired", "invalid"}:
            cancellation_reason = (
                "signal_expired" if signal_time_state == "expired" else "signal_time_invalid"
            )
            expired_signal = _entry_signal_payload(
                observation,
                state="cancelled",
                confirmations=0,
                reason=cancellation_reason,
                prior=prior_signal,
            )
            if prior_state == "active":
                cancelled = _entry_cancelled_alert(
                    item,
                    quote,
                    prior_signal,
                    reason=(
                        "信号已超过有效期"
                        if signal_time_state == "expired"
                        else "信号时间不可安全解析"
                    ),
                )
                alerts.append(cancelled)
                store.record_scan(
                    code,
                    status=(
                        "watching"
                        if signal_time_state == "invalid" and item.get("status") in ENTRY_ACTIONS
                        else None
                    ),
                    alert=cancelled,
                    entry_signal=expired_signal,
                )
            else:
                store.record_scan(code, entry_signal=expired_signal)
            continue
        if alert and alert.get("action") in ENTRY_ACTIONS:
            if not _entry_is_authorized(item):
                store.record_scan(
                    code,
                    entry_signal=_entry_signal_payload(
                        alert,
                        state="authorization_required",
                        confirmations=0,
                        reason="full_score_executable_required",
                    ),
                )
                continue

            same_signal = (
                prior_state in {"pending", "active"}
                and _same_entry_setup(prior_signal, alert)
            )
            if prior_state == "active" and not same_signal:
                changed_field = (
                    "playbook_changed"
                    if prior_signal.get("playbook") != alert.get("playbook")
                    else "trigger_changed"
                )
                cancelled = _entry_cancelled_alert(
                    item,
                    quote,
                    prior_signal,
                    reason="交易剧本或触发价已变化",
                )
                alerts.append(cancelled)
                store.record_scan(
                    code,
                    alert=cancelled,
                    entry_signal=_entry_signal_payload(
                        cancelled,
                        state="cancelled",
                        confirmations=0,
                        reason=changed_field,
                        prior=prior_signal,
                    ),
                )
                continue
            new_snapshot = _is_strictly_new_observation(prior_signal, alert)
            confirmations = (
                int(_to_float(prior_signal.get("confirmations"))) + 1
                if same_signal and new_snapshot
                else int(_to_float(prior_signal.get("confirmations")))
                if same_signal
                else 1
            )
            if prior_state == "active" and same_signal:
                store.record_scan(
                    code,
                    entry_signal=_entry_signal_payload(
                        alert,
                        state="active",
                        confirmations=ENTRY_CONFIRMATIONS_REQUIRED,
                        prior=prior_signal,
                    ),
                )
            elif confirmations >= ENTRY_CONFIRMATIONS_REQUIRED:
                scorecard = dict(item.get("scoring_decision") or {})
                score_value = _to_float(scorecard.get("score"))
                decision_basis = (
                    f"完整评分{score_value:.1f}，quote/kline/fund_flow/financial 四类必需数据齐全，"
                    f"盘中连续{confirmations}次确认；该授权不等同于 AI 辩论共识。"
                )
                active_signal = _entry_signal_payload(
                    alert,
                    state="active",
                    confirmations=confirmations,
                    prior=prior_signal if same_signal else None,
                )
                confirmed_alert = {
                    **alert,
                    "signal_id": active_signal["signal_id"],
                    "entry_authorization": "full_score_executable",
                    "confirmations": confirmations,
                    "confirmations_required": ENTRY_CONFIRMATIONS_REQUIRED,
                    "decision_basis": decision_basis,
                    "scorecard": scorecard,
                    "suggestion": f"{alert.get('suggestion', '')}；依据：{decision_basis}",
                }
                from app.services.execution_ledger import ExecutionLedger

                audit = ExecutionLedger(store.execution_ledger_path).append_entry_authorization_chain(
                    alert=confirmed_alert,
                    signal=active_signal,
                )
                if not audit.get("ok"):
                    store.record_scan(
                        code,
                        entry_signal=_entry_signal_payload(
                            alert,
                            state="authorization_required",
                            confirmations=confirmations,
                            reason="execution_audit_failed",
                            prior=active_signal,
                        ),
                    )
                    continue
                confirmed_alert["recommendation_id"] = audit["recommendation_id"]
                alerts.append(confirmed_alert)
                store.record_scan(
                    code,
                    alert=confirmed_alert,
                    entry_signal=_entry_signal_payload(
                        confirmed_alert,
                        state="active",
                        confirmations=confirmations,
                        prior=active_signal,
                    ),
                )
            else:
                store.record_scan(
                    code,
                    entry_signal=_entry_signal_payload(
                        alert,
                        state="pending",
                        confirmations=confirmations,
                        prior=prior_signal if same_signal else None,
                    ),
                )
            continue

        if prior_state == "active":
            reason = "盘中触发条件不再成立"
            if alert:
                reason = alert.get("message") or reason
            cancelled = _entry_cancelled_alert(item, quote, prior_signal, reason=reason)
            alerts.append(cancelled)
            store.record_scan(
                code,
                status=(
                    alert.get("action")
                    if alert
                    else "watching"
                    if item.get("status") in ENTRY_ACTIONS
                    else None
                ),
                alert=cancelled,
                entry_signal=_entry_signal_payload(
                    cancelled,
                    state="cancelled",
                    confirmations=0,
                    reason=alert.get("action") if alert else "trigger_lost",
                    prior=prior_signal,
                ),
            )
        elif alert:
            alerts.append(alert)
            store.record_scan(
                code,
                status=alert["action"],
                alert=alert,
                entry_signal=(
                    _entry_signal_payload(
                        alert,
                        state="cancelled",
                        confirmations=0,
                        reason="blocked_before_confirmation",
                        prior=prior_signal,
                    )
                    if prior_state == "pending"
                    else None
                ),
            )
        elif prior_state == "pending":
            store.record_scan(
                code,
                entry_signal={
                    **prior_signal,
                    "state": "cancelled",
                    "confirmations": 0,
                    "observed_at": _entry_observed_at(observation),
                    "reason": "trigger_lost_before_confirmation",
                },
            )
        else:
            store.record_scan(code)
    return {"scanned": len(items), "alerts": alerts}


def evaluate_position_watch(store: PositionWatchStore, quotes: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    payload = store.load()
    alerts: list[dict[str, Any]] = []
    for code, plan in payload.get("items", {}).items():
        quote = quotes.get(code) or {}
        price = _to_float(quote.get("price"))
        if price <= 0:
            continue
        stop_loss = _to_float(plan.get("stop_loss_price"))
        target = _to_float(plan.get("target_price"))
        name = plan.get("name", code)
        if stop_loss > 0 and price <= stop_loss:
            alerts.append({
                "stock_code": code,
                "stock_name": name,
                "level": "high",
                "action": "stop_loss",
                "message": f"{name}({code}) 现价¥{price:.2f} 跌破止损¥{stop_loss:.2f}",
                "suggestion": "立即处理止损或减仓",
            })
        elif target > 0 and price >= target:
            alerts.append({
                "stock_code": code,
                "stock_name": name,
                "level": "mid",
                "action": "take_profit",
                "message": f"{name}({code}) 现价¥{price:.2f} 触及目标¥{target:.2f}",
                "suggestion": "考虑分批止盈",
            })
    return alerts
