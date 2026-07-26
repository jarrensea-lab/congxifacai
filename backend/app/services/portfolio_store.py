"""User portfolio JSON store and database synchronization helpers."""
from __future__ import annotations

import json
import os
import fcntl
import hashlib
import tempfile
from copy import deepcopy
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

from sqlalchemy.orm import Session

from app.config import PROJECT_ROOT
from app.models import Position, SimAccount


def default_portfolio_path() -> str:
    return os.environ.get(
        "CONGXI_PORTFOLIO_PATH",
        os.path.abspath(os.path.join(PROJECT_ROOT, "..", "data", "user_portfolio.json")),
    )


def load_user_portfolio(path: str | None = None) -> dict[str, Any]:
    path = path or default_portfolio_path()
    return _load_user_portfolio_unlocked(path)


def _load_user_portfolio_unlocked(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


@contextmanager
def _file_lock(lock_path: str):
    os.makedirs(os.path.dirname(os.path.abspath(lock_path)), exist_ok=True)
    with open(lock_path, "a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


@contextmanager
def portfolio_transaction_lock(path: str | None = None):
    """Serialize one bot DB+JSON transaction on a single machine.

    Lock ordering is transaction lock first, portfolio file lock second. This
    is a host-local ``fcntl`` boundary, not a distributed lock.
    """
    path = path or default_portfolio_path()
    with _file_lock(f"{path}.transaction.lock"):
        yield


def _atomic_save_user_portfolio_unlocked(portfolio: dict[str, Any], path: str) -> None:
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    payload = json.dumps(
        portfolio,
        ensure_ascii=False,
        indent=2,
        allow_nan=False,
    )
    fd, temp_path = tempfile.mkstemp(
        prefix=f".{os.path.basename(path)}.",
        suffix=".tmp",
        dir=directory,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as temp_file:
            temp_file.write(payload)
            temp_file.flush()
            os.fsync(temp_file.fileno())
        os.replace(temp_path, path)
        directory_fd = os.open(directory, os.O_RDONLY)
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


def save_user_portfolio(portfolio: dict[str, Any], path: str | None = None) -> None:
    path = path or default_portfolio_path()
    with _file_lock(f"{path}.lock"):
        _atomic_save_user_portfolio_unlocked(portfolio, path)


def portfolio_fingerprint(portfolio: dict[str, Any]) -> str:
    payload = json.dumps(
        portfolio,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def restore_user_portfolio_if_unchanged(
    path: str | None,
    *,
    expected_fingerprint: str,
    replacement: dict[str, Any],
) -> dict[str, Any]:
    """Restore a snapshot only when the file still equals our written state."""
    path = path or default_portfolio_path()
    with _file_lock(f"{path}.lock"):
        current = _load_user_portfolio_unlocked(path)
        current_fingerprint = portfolio_fingerprint(current)
        if current_fingerprint != expected_fingerprint:
            return {
                "ok": False,
                "restored": False,
                "conflict": True,
                "current_fingerprint": current_fingerprint,
            }
        _atomic_save_user_portfolio_unlocked(replacement, path)
        return {
            "ok": True,
            "restored": True,
            "conflict": False,
            "fingerprint": portfolio_fingerprint(replacement),
        }


def _fen(value: float | int | None) -> int:
    return int(round(float(value or 0) * 100))


def _yuan(value_fen: int | float | None) -> float:
    return round(float(value_fen or 0) / 100, 2)


def recalculate_portfolio(portfolio: dict[str, Any]) -> dict[str, Any]:
    positions = portfolio.get("positions", [])
    for p in positions:
        shares = int(p.get("shares", 0) or 0)
        avg_cost = float(p.get("avg_cost", 0) or 0)
        current_price = float(p.get("current_price", avg_cost) or 0)
        p["total_cost"] = round(avg_cost * shares, 2)
        p["current_value"] = round(current_price * shares, 2)
        p["pnl"] = round(p["current_value"] - p["total_cost"], 2)
        p["pnl_pct"] = round(p["pnl"] / p["total_cost"] * 100, 2) if p["total_cost"] else 0

    portfolio["total_cost"] = round(sum(p.get("total_cost", 0) for p in positions), 2)
    portfolio["total_value"] = round(sum(p.get("current_value", 0) for p in positions), 2)
    portfolio["total_pnl"] = round(sum(p.get("pnl", 0) for p in positions), 2)
    portfolio["total_pnl_all"] = round(
        portfolio.get("total_pnl", 0) + portfolio.get("realized_pnl", 0),
        2,
    )
    available_cash = round(float(portfolio.get("available_cash", portfolio.get("cash", 0)) or 0), 2)
    frozen_cash = round(float(portfolio.get("frozen_cash", 0) or 0), 2)
    portfolio["available_cash"] = available_cash
    portfolio["cash"] = available_cash
    portfolio["frozen_cash"] = frozen_cash
    portfolio["total_assets"] = round(
        available_cash + frozen_cash + portfolio["total_value"],
        2,
    )
    portfolio["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return portfolio


REPORT_MARKET_FIELDS = frozenset({
    "quote_source",
    "quote_timestamp",
    "quote_trading_date",
    "quote_captured_at",
    "quote_freshness",
    "quote_status",
    "last_quote_price",
    "last_quote_timestamp",
    "current_price",
    "change_pct",
    "pe_ttm",
    "pb",
    "turnover_pct",
})
REPORT_STATUS_FIELDS = frozenset({"portfolio_sync_failed", "portfolio_sync_status"})


def merge_report_market_snapshot(
    report_snapshot: dict[str, Any],
    path: str | None = None,
) -> dict[str, Any]:
    """Merge quote fields into the latest account truth without overwriting trades."""
    path = path or default_portfolio_path()
    report_positions = {
        str(item.get("code") or ""): item
        for item in report_snapshot.get("positions", [])
        if isinstance(item, dict) and item.get("code")
    }
    with portfolio_transaction_lock(path):
        latest = load_user_portfolio(path)
        for field in REPORT_STATUS_FIELDS:
            if field in report_snapshot:
                latest[field] = report_snapshot[field]
        for position in latest.get("positions", []):
            if not isinstance(position, dict):
                continue
            report_position = report_positions.get(str(position.get("code") or ""))
            if report_position is None:
                continue
            for field in REPORT_MARKET_FIELDS:
                if field in report_position:
                    position[field] = report_position[field]
        recalculate_portfolio(latest)
        save_user_portfolio(latest, path)
        return latest


def sync_db_from_user_portfolio(db: Session, path: str | None = None) -> dict[str, Any]:
    """Make SQL positions/account reflect the user_portfolio.json source of truth."""
    portfolio = recalculate_portfolio(load_user_portfolio(path))
    active_codes = {p.get("code") for p in portfolio.get("positions", [])}

    for pos in db.query(Position).all():
        if pos.stock_code not in active_codes:
            pos.quantity = 0
            pos.market_value = 0
            pos.unrealized_pnl = 0
            pos.updated_at = datetime.now()

    for item in portfolio.get("positions", []):
        code = item["code"]
        pos = db.query(Position).filter(Position.stock_code == code).first()
        if not pos:
            pos = Position(stock_code=code, stock_name=item.get("name", code))
            db.add(pos)
            db.flush()
        shares = int(item.get("shares", 0) or 0)
        avg_cost_fen = _fen(item.get("avg_cost", 0))
        current_price_fen = _fen(item.get("current_price", item.get("avg_cost", 0)))
        pos.stock_name = item.get("name", pos.stock_name or code)
        pos.board_type = Position.classify_board(code)
        pos.quantity = shares
        pos.avg_cost = avg_cost_fen
        pos.total_buy_amount = avg_cost_fen * shares
        pos.total_buy_qty = shares
        pos.market_price = current_price_fen
        pos.market_value = current_price_fen * shares
        pos.unrealized_pnl = pos.market_value - (pos.avg_cost * pos.quantity)
        pos.realized_pnl = _fen(item.get("realized_pnl", 0))
        pos.updated_at = datetime.now()

    acc = db.query(SimAccount).first()
    if not acc:
        acc = SimAccount()
        db.add(acc)
        db.flush()

    if "available_cash" in portfolio:
        acc.cash = _fen(portfolio.get("available_cash", 0))
    elif "cash" in portfolio:
        acc.cash = _fen(portfolio.get("cash", 0))
    if "frozen_cash" in portfolio:
        acc.frozen = _fen(portfolio.get("frozen_cash", 0))

    db.flush()
    market_value_fen = sum(p.market_value for p in db.query(Position).filter(Position.quantity > 0).all())
    acc.total_value = acc.cash + acc.frozen + market_value_fen
    acc.total_pnl = acc.total_value - acc.initial_capital
    if acc.total_value > acc.peak_value:
        acc.peak_value = acc.total_value
    acc.updated_at = datetime.now()
    db.commit()

    total_assets = _yuan(acc.total_value)
    return {
        "positions_synced": len(active_codes),
        "available_cash": _yuan(acc.cash),
        "total_assets": total_assets,
        "total_value": total_assets,
    }


def _strict_decimal(value: Any, field: str) -> tuple[Decimal | None, str | None]:
    if isinstance(value, bool):
        return None, f"{field} must be a finite number"
    try:
        decimal_value = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None, f"{field} must be a finite number"
    if not decimal_value.is_finite():
        return None, f"{field} must be a finite number"
    return decimal_value, None


def _decimal_text(value: Decimal) -> str:
    text = format(value.normalize(), "f")
    return "0" if text == "-0" else text


def _money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _payload_fingerprint(payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _validate_trade_events(
    events: Any,
    *,
    fill_id: str | None,
    payload_fingerprint: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if events is None:
        return None, None
    if not isinstance(events, list):
        return None, {"ok": False, "error": "trade_events must be a list"}
    for index, event in enumerate(events):
        if not isinstance(event, dict):
            return None, {"ok": False, "error": f"trade_events[{index}] is invalid"}
        event_fill_id = event.get("fill_id")
        event_fingerprint = event.get("payload_fingerprint")
        if not isinstance(event_fill_id, str) or not event_fill_id:
            return None, {"ok": False, "error": f"trade_events[{index}].fill_id is invalid"}
        if not isinstance(event_fingerprint, str) or not event_fingerprint:
            return None, {
                "ok": False,
                "error": f"trade_events[{index}].payload_fingerprint is invalid",
            }
        if fill_id and event_fill_id == fill_id:
            if event_fingerprint != payload_fingerprint:
                return None, {
                    "ok": False,
                    "duplicate": False,
                    "conflict": True,
                    "error": f"fill_id conflict: {fill_id}",
                }
            return event, None
    return None, None


def apply_trade_to_user_portfolio(
    path: str | None,
    side: str,
    code: str,
    name: str,
    shares: int,
    price: float,
    trade_date: str | None = None,
    *,
    fill_id: str | None = None,
    source: str | None = None,
    source_event_id: str | None = None,
    occurred_at: str | None = None,
    recommendation_id: str | None = None,
    signal_id: str | None = None,
    fees: float | int | str | None = None,
) -> dict[str, Any]:
    """Apply one audited fill under a host-local exclusive file lock."""
    if side not in {"buy", "sell"}:
        return {"ok": False, "error": f"unsupported side: {side}"}
    if isinstance(shares, bool) or not isinstance(shares, int) or shares <= 0:
        return {"ok": False, "error": "shares must be a positive integer"}

    price_decimal, price_error = _strict_decimal(price, "price")
    if price_error or price_decimal is None or price_decimal <= 0:
        return {"ok": False, "error": price_error or "price must be greater than zero"}

    if fees is None or fees == "pending":
        fees_decimal = None
    else:
        fees_decimal, fees_error = _strict_decimal(fees, "fees")
        if fees_error or fees_decimal is None or fees_decimal < 0:
            return {"ok": False, "error": fees_error or "fees must be non-negative"}
        fees_decimal = _money(fees_decimal)

    payload = {
        "side": side,
        "code": str(code),
        "shares": shares,
        "price": _decimal_text(price_decimal),
        "source": source,
        "source_event_id": source_event_id,
        "recommendation_id": recommendation_id,
        "signal_id": signal_id,
        "fees": _decimal_text(fees_decimal) if fees_decimal is not None else "pending",
    }
    payload_fingerprint = _payload_fingerprint(payload)
    path = path or default_portfolio_path()

    with _file_lock(f"{path}.lock"):
        portfolio = _load_user_portfolio_unlocked(path)
        existing_fill, audit_error = _validate_trade_events(
            portfolio.get("trade_events"),
            fill_id=fill_id,
            payload_fingerprint=payload_fingerprint,
        )
        if audit_error:
            return audit_error
        if existing_fill is not None:
            return {
                "ok": True,
                "duplicate": True,
                "conflict": False,
                "fill": existing_fill,
                "portfolio": portfolio,
                "portfolio_fingerprint": portfolio_fingerprint(portfolio),
            }

        portfolio_before = deepcopy(portfolio)

        positions = portfolio.get("positions", [])
        if not isinstance(positions, list):
            return {"ok": False, "error": "positions must be a list"}
        if any(not isinstance(item, dict) for item in positions):
            return {"ok": False, "error": "positions contains an invalid entry"}
        pos = next((item for item in positions if item.get("code") == code), None)

        if side == "sell":
            if not pos:
                return {"ok": False, "error": f"{code} 无持仓"}
            held_shares = pos.get("shares", 0)
            if isinstance(held_shares, bool) or not isinstance(held_shares, int) or held_shares <= 0:
                return {"ok": False, "error": f"{code} invalid held shares"}
            if shares > held_shares:
                return {
                    "ok": False,
                    "error": f"requested shares {shares} exceeds held shares {held_shares}",
                }

        cash_decimal, cash_error = _strict_decimal(
            portfolio.get("available_cash", portfolio.get("cash", 0)),
            "available_cash",
        )
        if cash_error or cash_decimal is None:
            return {"ok": False, "error": cash_error or "available_cash is invalid"}

        occurred_at = occurred_at or datetime.now().astimezone().isoformat(timespec="seconds")
        trade_date = trade_date or occurred_at[:10]
        fee_amount = fees_decimal or Decimal("0")
        gross_amount = _money(price_decimal * shares)
        price_json = float(price_decimal)
        fees_json = float(fees_decimal) if fees_decimal is not None else None

        if side == "buy":
            if not pos:
                pos = {
                    "code": code,
                    "name": name or code,
                    "shares": 0,
                    "avg_cost": 0,
                    "trade_history": [],
                }
                positions.append(pos)
                portfolio["positions"] = positions
            old_shares = int(pos.get("shares", 0) or 0)
            old_avg_cost, old_cost_error = _strict_decimal(pos.get("avg_cost", 0), "avg_cost")
            if old_cost_error or old_avg_cost is None:
                return {"ok": False, "error": old_cost_error or "avg_cost is invalid"}
            new_shares = old_shares + shares
            new_cost = old_avg_cost * old_shares + price_decimal * shares + fee_amount
            avg_cost = (new_cost / new_shares).quantize(
                Decimal("0.000001"),
                rounding=ROUND_HALF_UP,
            )
            pos["shares"] = new_shares
            pos["avg_cost"] = float(avg_cost)
            pos["current_price"] = price_json
            cash_effect_decimal = -(gross_amount + fee_amount)
            portfolio["available_cash"] = float(_money(cash_decimal + cash_effect_decimal))
            avg_cost_for_close = None
            realized = None
        else:
            avg_cost_for_close, avg_cost_error = _strict_decimal(pos.get("avg_cost", 0), "avg_cost")
            if avg_cost_error or avg_cost_for_close is None:
                return {"ok": False, "error": avg_cost_error or "avg_cost is invalid"}
            pos["shares"] = held_shares - shares
            pos["current_price"] = price_json
            cash_effect_decimal = gross_amount - fee_amount
            portfolio["available_cash"] = float(_money(cash_decimal + cash_effect_decimal))
            realized_decimal = _money((price_decimal - avg_cost_for_close) * shares - fee_amount)
            old_realized, realized_error = _strict_decimal(
                portfolio.get("realized_pnl", 0),
                "realized_pnl",
            )
            if realized_error or old_realized is None:
                return {"ok": False, "error": realized_error or "realized_pnl is invalid"}
            realized = float(realized_decimal)
            portfolio["realized_pnl"] = float(_money(old_realized + realized_decimal))

        cash_effect = float(_money(cash_effect_decimal))
        fill = {
            "fill_id": fill_id,
            "payload_fingerprint": payload_fingerprint,
            "source": source,
            "source_event_id": source_event_id,
            "occurred_at": occurred_at,
            "trade_date": trade_date,
            "recommendation_id": recommendation_id,
            "signal_id": signal_id,
            "fees": fees_json,
            "fee_status": "known" if fees_decimal is not None else "pending",
            "side": side,
            "code": code,
            "name": name or pos.get("name", code),
            "shares": shares,
            "price": price_json,
            "gross_amount": float(gross_amount),
            "cash_effect": cash_effect,
        }
        history_entry = {
            "date": trade_date,
            "price": price_json,
            "shares": shares,
            "type": side,
            "fill_id": fill_id,
            "payload_fingerprint": payload_fingerprint,
            "source": source,
            "source_event_id": source_event_id,
            "occurred_at": occurred_at,
            "recommendation_id": recommendation_id,
            "signal_id": signal_id,
            "fees": fees_json,
            "fee_status": fill["fee_status"],
        }
        trade_history = pos.get("trade_history")
        if trade_history is None:
            trade_history = []
            pos["trade_history"] = trade_history
        if not isinstance(trade_history, list):
            return {"ok": False, "error": "trade_history must be a list"}
        trade_history.append(history_entry)

        if side == "sell" and pos["shares"] == 0:
            closed = portfolio.setdefault("closed_positions", [])
            closed.append({
                "code": code,
                "name": name or pos.get("name", code),
                "shares": shares,
                "avg_cost": float(avg_cost_for_close),
                "close_price": price_json,
                "close_date": trade_date,
                "realized_pnl": realized,
                "realized_pnl_pct": round(
                    realized / (float(avg_cost_for_close) * shares) * 100,
                    2,
                )
                if avg_cost_for_close and shares
                else 0,
                "trade_history": list(trade_history),
            })
            positions.remove(pos)

        if fill_id:
            portfolio.setdefault("trade_events", []).append(fill)

        recalculate_portfolio(portfolio)
        _atomic_save_user_portfolio_unlocked(portfolio, path)
        after_fingerprint = portfolio_fingerprint(portfolio)
        return {
            "ok": True,
            "duplicate": False,
            "conflict": False,
            "fill": fill,
            "portfolio": portfolio,
            "portfolio_fingerprint": after_fingerprint,
            "_portfolio_before": portfolio_before,
        }
