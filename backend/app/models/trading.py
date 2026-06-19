"""交易相关数据模型"""
from datetime import datetime
from sqlalchemy import Column, Integer, String, Float, DateTime, JSON, ForeignKey, Text
from app.database import Base


class SimAccount(Base):
    """模拟账户表（单例）"""
    __tablename__ = "sim_account"

    id = Column(Integer, primary_key=True, index=True)
    cash = Column(Integer, nullable=False, default=10000000, comment="可用资金(分)")
    frozen = Column(Integer, nullable=False, default=0, comment="冻结资金(分)")
    total_value = Column(Integer, nullable=False, default=10000000, comment="总资产(分)")
    initial_capital = Column(Integer, nullable=False, default=10000000, comment="初始资金(分)")
    daily_pnl = Column(Integer, nullable=False, default=0, comment="当日盈亏(分)")
    total_pnl = Column(Integer, nullable=False, default=0, comment="累计盈亏(分)")
    peak_value = Column(Integer, nullable=False, default=10000000, comment="历史峰值(分)")
    created_at = Column(DateTime(timezone=True), default=datetime.now)
    updated_at = Column(DateTime(timezone=True), default=datetime.now, onupdate=datetime.now)

    def to_dict(self):
        return {
            "id": self.id, "cash": self.cash, "frozen": self.frozen,
            "total_value": self.total_value, "initial_capital": self.initial_capital,
            "daily_pnl": self.daily_pnl, "total_pnl": self.total_pnl,
            "peak_value": self.peak_value,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class TradingSignal(Base):
    """策略信号表"""
    __tablename__ = "trading_signals"

    id = Column(Integer, primary_key=True, index=True)
    stock_code = Column(String(8), nullable=False, index=True)
    stock_name = Column(String(50))
    strategy_name = Column(String(30), nullable=False, default="trend_tracker")
    signal_type = Column(String(10), nullable=False, comment="buy / sell")
    price = Column(Integer, comment="触发时参考价格(分)")
    confidence = Column(Float, default=0.5)
    reason = Column(Text, comment="触发条件说明")
    params_json = Column(Text, comment="策略参数快照JSON")
    suggested_qty = Column(Integer, comment="建议买卖股数")
    approved_by = Column(String(20), comment="manual / auto")
    approved_at = Column(DateTime(timezone=True))
    status = Column(String(20), nullable=False, default="pending",
                    comment="pending / approved / rejected / expired / executed")
    code_snippet = Column(Text, nullable=True, comment="AI生成的操盘代码片段")
    code_language = Column(String(20), default="python", comment="代码语言")
    strategy_instance_id = Column(Integer, ForeignKey("strategy_instances.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.now)

    def to_dict(self):
        return {
            "id": self.id, "stock_code": self.stock_code, "stock_name": self.stock_name,
            "strategy_name": self.strategy_name, "signal_type": self.signal_type,
            "price": self.price / 100 if self.price else 0, "confidence": self.confidence,
            "reason": self.reason, "params_json": self.params_json,
            "suggested_qty": self.suggested_qty,
            "approved_by": self.approved_by,
            "approved_at": self.approved_at.isoformat() if self.approved_at else None,
            "status": self.status,
            "code_snippet": self.code_snippet,
            "code_language": self.code_language,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class TradingOrder(Base):
    """交易订单表 — 升级版状态机"""
    __tablename__ = "trading_orders"

    # 订单状态机: pending → submitted → (partial_filled) → filled | rejected | cancelled
    VALID_STATUSES = {"pending", "submitted", "partial_filled", "filled", "rejected", "cancelled"}
    TERMINAL_STATUSES = {"filled", "rejected", "cancelled"}

    id = Column(Integer, primary_key=True, index=True)
    signal_id = Column(Integer, index=True)
    stock_code = Column(String(8), nullable=False, index=True)
    stock_name = Column(String(50))
    board_type = Column(String(20), comment="板块: main/chi_next/star/bei_jiao")
    direction = Column(String(10), nullable=False, comment="buy / sell")
    order_type = Column(String(20), nullable=False, comment="market / limit / stop_loss")
    price = Column(Integer, comment="委托价格(分)")
    quantity = Column(Integer, nullable=False)
    filled_price = Column(Integer, comment="成交价格(分)")
    filled_quantity = Column(Integer, default=0)
    fee = Column(Integer, default=0, comment="总手续费(分)")
    fee_detail = Column(JSON, comment="费用明细: commission/stamp_tax/transfer/regulatory/handling")
    status = Column(String(20), nullable=False, default="pending",
                    comment="pending / submitted / partial_filled / filled / cancelled / rejected")
    rejection_reason = Column(String(200))
    submitted_at = Column(DateTime(timezone=True), comment="提交时间")
    created_at = Column(DateTime(timezone=True), default=datetime.now)
    filled_at = Column(DateTime(timezone=True))

    def to_dict(self):
        return {
            "id": self.id, "signal_id": self.signal_id, "stock_code": self.stock_code,
            "stock_name": self.stock_name, "board_type": self.board_type,
            "direction": self.direction, "order_type": self.order_type,
            "price": self.price, "quantity": self.quantity,
            "filled_price": self.filled_price, "filled_quantity": self.filled_quantity,
            "fee": self.fee, "fee_detail": self.fee_detail,
            "status": self.status, "rejection_reason": self.rejection_reason,
            "submitted_at": self.submitted_at.isoformat() if self.submitted_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "filled_at": self.filled_at.isoformat() if self.filled_at else None,
        }

    def can_transition(self, new_status: str) -> bool:
        """校验状态变迁合法性"""
        valid_transitions = {
            "pending": {"submitted", "cancelled"},
            "submitted": {"partial_filled", "filled", "rejected", "cancelled"},
            "partial_filled": {"filled", "cancelled"},
        }
        allowed = valid_transitions.get(self.status, set())
        return new_status in allowed


class TradeLog(Base):
    """交易日志表"""
    __tablename__ = "trade_logs"

    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, nullable=False, index=True)
    stock_code = Column(String(8), nullable=False)
    stock_name = Column(String(50))
    direction = Column(String(10), nullable=False)
    price = Column(Integer, nullable=False, comment="成交价格(分)")
    quantity = Column(Integer, nullable=False)
    amount = Column(Integer, nullable=False, comment="成交金额(分)")
    fee = Column(Integer, nullable=False, default=0)
    pnl = Column(Integer, comment="盈亏(分)")
    pnl_pct = Column(Float)
    strategy_name = Column(String(30), nullable=False)
    holding_days = Column(Integer)
    signal_id = Column(Integer, nullable=True, index=True, comment="关联信号ID")
    strategy_code_version = Column(Integer, nullable=True, comment="执行时使用的代码版本")
    strategy_instance_id = Column(Integer, ForeignKey("strategy_instances.id"), nullable=True)
    traded_at = Column(DateTime(timezone=True), default=datetime.now)

    def to_dict(self):
        return {
            "id": self.id, "order_id": self.order_id, "stock_code": self.stock_code,
            "stock_name": self.stock_name, "direction": self.direction,
            "price": self.price, "quantity": self.quantity, "amount": self.amount,
            "fee": self.fee, "pnl": self.pnl, "pnl_pct": self.pnl_pct,
            "strategy_name": self.strategy_name, "holding_days": self.holding_days,
            "signal_id": self.signal_id,
            "strategy_code_version": self.strategy_code_version,
            "traded_at": self.traded_at.isoformat() if self.traded_at else None,
        }


class Position(Base):
    """持仓表 — 每次交易同步更新, 不再从 TradeLog 聚合推导"""
    __tablename__ = "positions"

    id = Column(Integer, primary_key=True, index=True)
    stock_code = Column(String(8), nullable=False, unique=True, index=True)
    stock_name = Column(String(50))
    board_type = Column(String(20), comment="板块: main/chi_next/star/bei_jiao")
    quantity = Column(Integer, nullable=False, default=0, comment="持仓数量(股)")
    total_buy_amount = Column(Integer, nullable=False, default=0, comment="总买入金额(分)")
    total_buy_qty = Column(Integer, nullable=False, default=0, comment="总买入数量(股)")
    avg_cost = Column(Integer, nullable=False, default=0, comment="加权平均成本(分/股)")
    market_price = Column(Integer, nullable=False, default=0, comment="最新市价(分)")
    market_value = Column(Integer, nullable=False, default=0, comment="最新市值(分)")
    unrealized_pnl = Column(Integer, nullable=False, default=0, comment="浮动盈亏(分)")
    realized_pnl = Column(Integer, nullable=False, default=0, comment="已实现盈亏(分)")
    today_bought_qty = Column(Integer, nullable=False, default=0, comment="今日买入量(T+1用)")
    today_bought_date = Column(String(10), nullable=True, comment="买入日期 YYYY-MM-DD")
    high_price = Column(Integer, nullable=False, default=0, comment="持仓期间最高价(分)")
    low_price = Column(Integer, nullable=False, default=0, comment="持仓期间最低价(分)")
    open_date = Column(DateTime(timezone=True), comment="首次建仓日期")
    updated_at = Column(DateTime(timezone=True), default=datetime.now, onupdate=datetime.now)

    def to_dict(self):
        return {
            "stock_code": self.stock_code,
            "stock_name": self.stock_name,
            "board_type": self.board_type,
            "quantity": self.quantity,
            "avg_cost": round(self.avg_cost / 100, 2) if self.avg_cost else 0,
            "market_price": round(self.market_price / 100, 2) if self.market_price else 0,
            "market_value": round(self.market_value / 100, 2) if self.market_value else 0,
            "unrealized_pnl": round(self.unrealized_pnl / 100, 2) if self.unrealized_pnl else 0,
            "realized_pnl": round(self.realized_pnl / 100, 2) if self.realized_pnl else 0,
            "today_bought_qty": self.today_bought_qty,
            "open_date": self.open_date.isoformat() if self.open_date else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    @staticmethod
    def classify_board(code: str) -> str:
        """根据股票代码判断板块"""
        if code.startswith("688") or code.startswith("689"):
            return "star"
        elif code.startswith("300") or code.startswith("301"):
            return "chi_next"
        elif code.startswith("8"):
            return "bei_jiao"
        return "main"
