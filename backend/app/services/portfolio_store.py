"""User portfolio JSON store and database synchronization helpers."""
from __future__ import annotations

import json
import os
from datetime import datetime
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
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_user_portfolio(portfolio: dict[str, Any], path: str | None = None) -> None:
    path = path or default_portfolio_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(portfolio, f, ensure_ascii=False, indent=2)


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
    portfolio["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return portfolio


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


def apply_trade_to_user_portfolio(
    path: str | None,
    side: str,
    code: str,
    name: str,
    shares: int,
    price: float,
    trade_date: str | None = None,
) -> dict[str, Any]:
    """Apply a manual buy/sell to user_portfolio.json."""
    portfolio = load_user_portfolio(path)
    positions = portfolio.setdefault("positions", [])
    trade_date = trade_date or datetime.now().strftime("%Y-%m-%d")
    shares = int(shares)
    price = float(price)

    pos = next((p for p in positions if p.get("code") == code), None)
    if side == "buy":
        if not pos:
            pos = {"code": code, "name": name or code, "shares": 0, "avg_cost": 0, "trade_history": []}
            positions.append(pos)
        old_shares = int(pos.get("shares", 0) or 0)
        old_cost = float(pos.get("avg_cost", 0) or 0) * old_shares
        new_shares = old_shares + shares
        pos["shares"] = new_shares
        pos["avg_cost"] = round((old_cost + price * shares) / new_shares, 3) if new_shares else 0
        pos["current_price"] = price
        portfolio["available_cash"] = round(float(portfolio.get("available_cash", 0) or 0) - price * shares, 2)
    elif side == "sell":
        if not pos:
            return {"ok": False, "error": f"{code} 无持仓"}
        sell_shares = min(shares, int(pos.get("shares", 0) or 0))
        avg_cost = float(pos.get("avg_cost", 0) or 0)
        pos["shares"] = int(pos.get("shares", 0) or 0) - sell_shares
        pos["current_price"] = price
        portfolio["available_cash"] = round(float(portfolio.get("available_cash", 0) or 0) + price * sell_shares, 2)
        realized = round((price - avg_cost) * sell_shares, 2)
        portfolio["realized_pnl"] = round(float(portfolio.get("realized_pnl", 0) or 0) + realized, 2)
        if pos["shares"] <= 0:
            closed = portfolio.setdefault("closed_positions", [])
            closed.append({
                "code": code,
                "name": name or pos.get("name", code),
                "shares": sell_shares,
                "avg_cost": avg_cost,
                "close_price": price,
                "close_date": trade_date,
                "realized_pnl": realized,
                "realized_pnl_pct": round(realized / (avg_cost * sell_shares) * 100, 2) if avg_cost and sell_shares else 0,
            })
            positions.remove(pos)
    else:
        return {"ok": False, "error": f"unsupported side: {side}"}

    if pos in positions:
        pos.setdefault("trade_history", []).append({
            "date": trade_date,
            "price": price,
            "shares": shares,
            "type": side,
        })

    recalculate_portfolio(portfolio)
    save_user_portfolio(portfolio, path)
    return {"ok": True, "portfolio": portfolio}
