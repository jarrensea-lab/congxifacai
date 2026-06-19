"""交易执行逻辑 — 买入/卖出/清仓/账户更新"""
from datetime import datetime, date
from typing import Dict, Any
from sqlalchemy.orm import Session
from app.models import SimAccount, Position, TradeLog
from app.trading_engine.position import PositionManager
from app.utils.logger import logger


def update_account(db: Session, total: float, cash: float) -> Dict[str, Any]:
    """更新模拟账户总资产和现金"""
    acc = db.query(SimAccount).first()
    if not acc:
        acc = SimAccount(initial_capital=int(total * 100))
        db.add(acc)
        db.flush()

    old_total = acc.total_value / 100
    acc.cash = int(cash * 100)
    acc.total_value = int(total * 100)
    daily_change = (total - old_total)
    acc.total_pnl = acc.total_value - acc.initial_capital
    if acc.total_value > acc.peak_value:
        acc.peak_value = acc.total_value
    acc.updated_at = datetime.now()

    logger.info(f"账户更新: 总资产 ¥{total:.2f} 现金 ¥{cash:.2f} 变动 ¥{daily_change:+.2f}")
    return {
        "ok": True, "action": "account_updated",
        "total": total, "cash": cash, "change": round(daily_change, 2),
    }


def execute_buy(db: Session, code: str, name: str, qty: int, cost: float) -> Dict[str, Any]:
    """执行买入操作"""
    cost_fen = int(cost * 100)
    amount_fen = cost_fen * qty

    pos = PositionManager.get_or_create(db, code, name)
    today_str = date.today().isoformat()

    if pos.today_bought_date != today_str:
        pos.today_bought_qty = 0
        pos.today_bought_date = today_str

    # 更新持仓
    old_total = pos.total_buy_amount
    old_qty = pos.total_buy_qty
    pos.total_buy_amount = old_total + amount_fen
    pos.total_buy_qty = old_qty + qty
    pos.avg_cost = round(pos.total_buy_amount / pos.total_buy_qty) if pos.total_buy_qty > 0 else 0
    pos.quantity += qty
    pos.market_price = cost_fen
    pos.market_value = pos.quantity * cost_fen
    pos.today_bought_qty += qty
    pos.today_bought_date = today_str
    if not pos.open_date:
        pos.open_date = datetime.now()
    pos.updated_at = datetime.now()

    # 扣除现金
    acc = db.query(SimAccount).first()
    if acc:
        acc.cash -= amount_fen
        acc.updated_at = datetime.now()

    # 交易日志
    log = TradeLog(
        order_id=0, stock_code=code, stock_name=name,
        direction='buy', price=cost_fen, quantity=qty,
        amount=amount_fen, fee=0,
        strategy_name='manual', traded_at=datetime.now()
    )
    db.add(log)

    logger.info(f"机器人指令-BUY: {name}({code}) {qty}股 @¥{cost:.3f} 金额¥{amount_fen/100:.2f}")
    return {"ok": True, "action": "buy", "code": code, "name": name, "qty": qty, "cost": cost}


def execute_sell(db: Session, code: str, name: str, qty: int, price: float) -> Dict[str, Any]:
    """执行卖出操作"""
    price_fen = int(price * 100)
    pos = db.query(Position).filter(Position.stock_code == code).first()
    if not pos or pos.quantity <= 0:
        return {"ok": False, "error": f"{code} 无持仓"}

    sell_qty = min(qty, pos.quantity)
    amount_fen = price_fen * sell_qty
    pnl_fen = (price_fen - pos.avg_cost) * sell_qty

    pos.quantity -= sell_qty
    pos.realized_pnl = (pos.realized_pnl or 0) + pnl_fen
    pos.market_price = price_fen
    pos.market_value = pos.quantity * price_fen

    if pos.quantity == 0:
        pos.unrealized_pnl = 0
        pos.avg_cost = 0
        pos.total_buy_amount = 0
        pos.total_buy_qty = 0
        pos.today_bought_qty = 0
    else:
        pos.unrealized_pnl = pos.market_value - (pos.avg_cost * pos.quantity)

    pos.updated_at = datetime.now()

    # 增加现金
    acc = db.query(SimAccount).first()
    if acc:
        acc.cash += amount_fen
        acc.updated_at = datetime.now()

    log = TradeLog(
        order_id=0, stock_code=code, stock_name=name,
        direction='sell', price=price_fen, quantity=-sell_qty,
        amount=amount_fen, fee=0,
        pnl=pnl_fen, strategy_name='manual', traded_at=datetime.now()
    )
    db.add(log)

    logger.info(f"机器人指令-SELL: {name}({code}) {sell_qty}股 @¥{price:.2f} PnL=¥{pnl_fen/100:.2f}")
    return {"ok": True, "action": "sell", "code": code, "name": name, "qty": sell_qty, "price": price, "pnl": round(pnl_fen/100, 2)}


def execute_clear(db: Session, code: str, name: str) -> Dict[str, Any]:
    """执行清仓操作"""
    pos = db.query(Position).filter(Position.stock_code == code).first()
    if not pos or pos.quantity <= 0:
        return {"ok": False, "error": f"{code} 无持仓"}

    qty = pos.quantity
    price = pos.market_price or pos.avg_cost
    return execute_sell(db, code, name or pos.stock_name, qty, price / 100)
