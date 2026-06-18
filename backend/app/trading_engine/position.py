"""持仓管理器 — 统一的 Position 表操作入口"""
from datetime import datetime, date
from typing import Optional, List
from sqlalchemy.orm import Session
from app.models import Position
from app.utils.logger import logger


class PositionManager:
    """持仓管理 — 所有持仓查询的单一入口"""

    @staticmethod
    def get_all(db: Session) -> List[Position]:
        """获取所有当前持仓"""
        return db.query(Position).filter(Position.quantity > 0).all()

    @staticmethod
    def get_one(db: Session, stock_code: str) -> Optional[Position]:
        """获取单只持仓"""
        return db.query(Position).filter(Position.stock_code == stock_code).first()

    @staticmethod
    def get_or_create(db: Session, stock_code: str, stock_name: str = "") -> Position:
        """获取或创建持仓记录"""
        pos = db.query(Position).filter(Position.stock_code == stock_code).first()
        if not pos:
            pos = Position(
                stock_code=stock_code,
                stock_name=stock_name or stock_code,
                board_type=Position.classify_board(stock_code),
            )
            db.add(pos)
            db.flush()
        return pos

    @staticmethod
    def update_on_buy(db: Session, stock_code: str, stock_name: str,
                      price_fen: int, quantity: int, amount_fen: int):
        """买入成交后更新持仓"""
        pos = PositionManager.get_or_create(db, stock_code, stock_name)
        today_str = date.today().isoformat()

        # 重置今日买入量 (日期变更时)
        if pos.today_bought_date != today_str:
            pos.today_bought_qty = 0
            pos.today_bought_date = today_str

        # 加权平均成本: (旧成本*旧数量 + 新金额) / 总数量
        old_total = pos.total_buy_amount
        old_qty = pos.total_buy_qty
        new_total = old_total + amount_fen
        new_qty = old_qty + quantity
        pos.total_buy_amount = new_total
        pos.total_buy_qty = new_qty
        pos.avg_cost = round(new_total / new_qty) if new_qty > 0 else 0
        pos.quantity += quantity
        pos.market_value = pos.quantity * price_fen
        pos.market_price = price_fen
        pos.unrealized_pnl = pos.market_value - (pos.avg_cost * pos.quantity)
        pos.today_bought_qty += quantity
        pos.today_bought_date = today_str

        # 首次建仓
        if not pos.open_date or pos.quantity == quantity:
            pos.open_date = datetime.now()

        # 最高/最低价
        if price_fen > pos.high_price or pos.high_price == 0:
            pos.high_price = price_fen
        if price_fen < pos.low_price or pos.low_price == 0:
            pos.low_price = price_fen

        pos.updated_at = datetime.now()
        logger.info(f"Position: BUY {stock_name}({stock_code}) += {quantity}股, "
                     f"持仓={pos.quantity}股, 成本=¥{pos.avg_cost/100:.2f}")

    @staticmethod
    def update_on_sell(db: Session, stock_code: str, stock_name: str,
                       price_fen: int, quantity: int, pnl_fen: int = 0):
        """卖出成交后更新持仓 (quantity参数为正数, 函数内转为负数)"""
        pos = PositionManager.get_one(db, stock_code)
        if not pos:
            logger.warning(f"Position: SELL 失败 — {stock_code} 无持仓")
            return

        sell_qty = min(quantity, pos.quantity)
        pos.quantity -= sell_qty
        pos.realized_pnl = (pos.realized_pnl or 0) + pnl_fen
        pos.market_price = price_fen
        pos.market_value = pos.quantity * price_fen

        # 如果清仓，重置成本和浮动盈亏
        if pos.quantity == 0:
            pos.unrealized_pnl = 0
            pos.avg_cost = 0
            pos.total_buy_amount = 0
            pos.total_buy_qty = 0
            pos.today_bought_qty = 0
        else:
            pos.unrealized_pnl = pos.market_value - (pos.avg_cost * pos.quantity)

        pos.updated_at = datetime.now()
        logger.info(f"Position: SELL {stock_name}({stock_code}) -= {sell_qty}股, "
                     f"剩余={pos.quantity}股, 浮动盈亏=¥{(pos.unrealized_pnl or 0)/100:.2f}")

    @staticmethod
    def refresh_market_prices(db: Session, batch_data: dict):
        """批量刷新持仓市价 (从行情数据)"""
        positions = PositionManager.get_all(db)
        for pos in positions:
            rt = batch_data.get(pos.stock_code, {})
            price = rt.get("price", 0) or 0
            if price > 0:
                pos.market_price = int(price * 100) if price < 10000 else int(price)
                pos.market_value = pos.quantity * pos.market_price
                pos.unrealized_pnl = pos.market_value - (pos.avg_cost * pos.quantity)
                pos.updated_at = datetime.now()

    @staticmethod
    def get_holdings_display(db: Session) -> list:
        """获取持仓展示列表 (用于API返回)"""
        positions = PositionManager.get_all(db)
        return [p.to_dict() for p in positions]

    @staticmethod
    def get_holdings_codes(db: Session) -> List[str]:
        """获取当前持仓的股票代码列表"""
        return [p.stock_code for p in PositionManager.get_all(db)]

    @staticmethod
    def get_total_market_value(db: Session) -> int:
        """获取持仓总市值(分)"""
        positions = PositionManager.get_all(db)
        return sum(p.market_value for p in positions if p.market_value)
