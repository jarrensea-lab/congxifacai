"""持仓数据查询 — 从 Position 表获取持仓、账户信息"""
from sqlalchemy.orm import Session
from app.models import Position, SimAccount


def get_holdings_data(db: Session) -> dict:
    """从 Position 表获取持仓数据，用于分析引擎和规划引擎。"""
    positions = db.query(Position).filter(Position.quantity > 0).all()
    holdings = []
    total_cost = 0.0
    for p in positions:
        cost_yuan = (p.avg_cost or 0) / 100
        qty = int(p.quantity or 0)
        market_price_yuan = (p.market_price or p.avg_cost or 0) / 100
        holdings.append({
            "code": p.stock_code, "name": p.stock_name,
            "position": qty, "cost": round(cost_yuan, 2),
            "current_price": round(market_price_yuan, 2),
        })
        total_cost += cost_yuan * qty
    holdings_str = "\n".join(
        f"- {h['name']}({h['code']}): {h['position']}股, 成本¥{h['cost']:.2f}"
        for h in holdings
    ) or "无持仓"
    account = db.query(SimAccount).first()
    available_cash = float(account.cash) if account else 100000.0
    return {
        "holdings": holdings, "holdings_str": holdings_str,
        "total_cost": round(total_cost, 2), "available_cash": round(available_cash, 2),
    }
