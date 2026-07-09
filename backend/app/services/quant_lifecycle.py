"""Production candidate/position lifecycle for scheduled trading assistance."""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config import PROJECT_ROOT
from app.services.strategy_profile import get_strategy_profile


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
    executable_budget = max(0.0, min(cash, single_limit))
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
            if isinstance(item, dict)
            and item.get("status")
            not in {
                "removed",
                "expired",
                "research_only",
                "research_reference",
                *LONG_HORIZON_STATUSES,
            }
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
        current_price: float | None = None,
        available_cash: float = 0,
        total_assets: float = 0,
    ) -> bool:
        clean = _clean_code(code)
        if not clean:
            return False
        normalized_status = status if status in self.VALID_STATUSES else "candidate"
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
            "execution": {**(existing.get("execution") or {}), **execution},
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
    stop_loss = round(price * 0.95, 2)
    snapshot = {
        "code": code,
        "name": name,
        "quote": quote,
        "kline": item.get("kline") or {},
        "fund_flow": item.get("fund_flow") or {},
        "market_regime": item.get("market_regime") or {},
    }
    playbook = select_playbook(snapshot)
    regime = evaluate_market_regime(snapshot)
    sizing = calculate_position_size(
        code=code,
        entry_price=price,
        stop_loss=stop_loss,
        available_cash=available_cash,
        total_assets=total_assets,
        profile=get_strategy_profile(),
    )
    held_shares = int(_to_float((position or {}).get("shares")))
    held_value = _to_float((position or {}).get("market_value"))
    is_existing_position = held_shares > 0 or held_value > 0
    profile = get_strategy_profile()
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
                "suggestion": f"加仓前确认未超单票上限；新增仓位止损¥{stop_loss:.2f}",
            }
        return {
            **base,
            "level": "mid",
            "action": "actionable",
            "message": (
                f"{name}({code}) {playbook.get('playbook')} 触发，现价¥{price:.2f}，"
                f"建议{int(sizing.get('shares', 0))}股，风险约¥{sizing.get('risk_amount', 0):.2f}。"
            ),
            "suggestion": f"人工复核后可试仓；止损¥{stop_loss:.2f}",
        }

    return None


async def evaluate_candidate_pool(
    store: CandidatePoolStore,
    quote_source: Any,
    *,
    available_cash: float,
    total_assets: float = 0,
    positions: dict[str, dict[str, Any]] | None = None,
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
        scan_item = item
        if _looks_like_breakout_quote(quote) and not _has_recent_kline(item) and hasattr(quote_source, "fetch_kline"):
            try:
                kline = await quote_source.fetch_kline(code, "day", count=20)
                if isinstance(kline, dict) and kline.get("bars"):
                    scan_item = {**item, "kline": kline}
            except Exception:
                scan_item = item
        alert = _candidate_alert(scan_item, quote, available_cash, total_assets, (positions or {}).get(code))
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
