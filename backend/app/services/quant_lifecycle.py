"""Production candidate/position lifecycle for scheduled trading assistance."""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config import PROJECT_ROOT


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
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp_path.replace(path)


def _clean_code(value: Any) -> str:
    return str(value or "").strip()


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def normalize_alert_level(level: str | None) -> str:
    if level in ("high", "mid", "low"):
        return level
    if level == "medium":
        return "mid"
    return "low"


def _has_data_insufficient_marker(rec: dict[str, Any]) -> bool:
    fields = [rec.get("buy_range"), rec.get("stop_loss"), rec.get("target"), rec.get("reason")]
    return any("数据不足" in str(field or "") or "观望" in str(field or "") for field in fields)


class CandidatePoolStore:
    """File-backed production candidate pool.

    This is separate from the Sentinel/Serenity research pool: research pools hold
    themes; this store holds executable lifecycle state for scheduled scans.
    """

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path is not None else default_candidate_pool_path()

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

    def get(self, code: str) -> dict[str, Any] | None:
        return self.load().get("items", {}).get(_clean_code(code))

    def active_items(self) -> list[dict[str, Any]]:
        items = self.load().get("items", {})
        return [
            item
            for item in items.values()
            if isinstance(item, dict) and item.get("status") not in {"removed", "expired"}
        ]

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
        payload = self.load()
        item = payload.setdefault("items", {}).get(_clean_code(code))
        if not item:
            return
        item["status"] = status
        item["last_scanned_at"] = _now()
        if alert:
            item["last_alert"] = alert
            item.setdefault("decision_history", []).append({"time": _now(), **alert})
            item["decision_history"] = item["decision_history"][-30:]
        self.save(payload)


class TargetPoolStore(CandidatePoolStore):
    """Compatibility wrapper for the production stock lifecycle pool.

    `CandidatePoolStore` remains as the MVP file-backed implementation. The
    business meaning is broader: it is the total target lifecycle pool, where
    "candidate" is only one status.
    """

    VALID_STATUSES = {
        "research_only",
        "candidate",
        "watching",
        "actionable",
        "blocked_chasing",
        "position",
        "removed",
        "expired",
    }

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
    ) -> bool:
        clean = _clean_code(code)
        if not clean:
            return False
        normalized_status = status if status in self.VALID_STATUSES else "candidate"
        payload = self.load()
        items = payload.setdefault("items", {})
        existing = items.get(clean, {})
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
            "evidence": {**(existing.get("evidence") or {}), **(evidence or {})},
            "evidence_ids": merged_evidence_ids,
            "sentinel": {**(existing.get("sentinel") or {}), **(sentinel or {})},
            "serenity": {**(existing.get("serenity") or {}), **(serenity or {})},
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

    def upsert_plan(
        self,
        code: str,
        name: str,
        *,
        stop_loss_price: float | None = None,
        target_price: float | None = None,
        source: str = "manual",
    ) -> None:
        payload = self.load()
        items = payload.setdefault("items", {})
        clean = _clean_code(code)
        existing = items.get(clean, {})
        items[clean] = {
            **existing,
            "code": clean,
            "name": name or existing.get("name") or clean,
            "stop_loss_price": stop_loss_price if stop_loss_price is not None else existing.get("stop_loss_price"),
            "target_price": target_price if target_price is not None else existing.get("target_price"),
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


def _candidate_alert(item: dict[str, Any], quote: dict[str, Any], available_cash: float) -> dict[str, Any] | None:
    price = _to_float(quote.get("price"))
    if price <= 0:
        return None

    change_pct = _to_float(quote.get("change_pct"))
    vol_ratio = _to_float(quote.get("vol_ratio"))
    amount_wan = _to_float(quote.get("amount_wan") or quote.get("amount"))
    lot_value = price * 100
    affordable = lot_value <= available_cash
    code = item.get("code", "")
    name = item.get("name", code)

    base = {
        "stock_code": code,
        "stock_name": name,
        "price": price,
        "change_pct": change_pct,
        "vol_ratio": vol_ratio,
        "amount_wan": amount_wan,
        "lot_value": round(lot_value, 2),
        "affordable": affordable,
    }

    if _is_limit_up_or_chasing(price, quote, change_pct):
        return {
            **base,
            "level": "high",
            "action": "blocked_chasing",
            "message": f"{name}({code}) 涨幅{change_pct:+.2f}%，接近涨停，禁止追高，转入次日观察。",
            "suggestion": "禁止追高，等回落或次日重新评估",
        }

    if affordable and change_pct >= 3.0 and vol_ratio >= 2.0 and amount_wan >= 10000:
        return {
            **base,
            "level": "mid",
            "action": "actionable",
            "message": f"{name}({code}) 放量上涨{change_pct:+.2f}%，量比{vol_ratio:.2f}，成交额{amount_wan:.0f}万。",
            "suggestion": "可试仓，必须人工确认价格和仓位",
        }

    return None


async def evaluate_candidate_pool(
    store: CandidatePoolStore,
    quote_source: Any,
    *,
    available_cash: float,
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
        alert = _candidate_alert(item, quote, available_cash)
        if alert:
            alerts.append(alert)
            store.record_decision(code, alert["action"], alert)
        else:
            store.record_decision(code, "watching", None)
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
