"""订单管理器 — 状态机 + Position同步"""
from datetime import datetime
from typing import Optional, List
from sqlalchemy.orm import Session
from app.database import SessionLocal
from app.models import TradingOrder, TradingSignal, TradeLog, SimAccount
from app.trading_engine.account import SimAccountManager
from app.trading_engine.broker import SimBroker
from app.trading_engine.position import PositionManager
from app.trading_engine.fee_schedule import get_board_type, round_lot, get_price_limit_pct
from app.trading_engine.risk_guard import RiskGuard
from app.trading_engine.signal_engine import SignalEngine
from app.utils.logger import logger


class OrderManager:
    """订单生命周期管理器 — 统一Session + Position同步"""

    def __init__(self, account_mgr: SimAccountManager, broker: SimBroker,
                 risk_guard: RiskGuard, signal_engine: SignalEngine):
        self.account = account_mgr
        self.broker = broker
        self.risk = risk_guard
        self.signal = signal_engine
        self._db: Optional[Session] = None

    def _get_db(self) -> Session:
        if self._db is None:
            self._db = SessionLocal()
        return self._db

    def _get_account(self, db: Session) -> SimAccount:
        acc = db.query(SimAccount).first()
        if not acc:
            acc = SimAccount()
            db.add(acc)
            db.flush()
        return acc

    def create_from_signal(self, signal: TradingSignal) -> Optional[TradingOrder]:
        """从信号创建订单, 执行风控管道 + 撮合 + Position同步 (单一Session)"""
        db = self._get_db()

        if signal.status != "approved":
            logger.warning(f"信号 {signal.id} 状态={signal.status}, 不可执行")
            return None

        direction = signal.signal_type
        stock_code = signal.stock_code
        quantity = round_lot(signal.suggested_qty or 100, stock_code)
        # 信号价格已按分存储
        price_fen = signal.price if signal.price else 0
        if isinstance(signal.price, float) and signal.price < 10000:
            price_fen = int(signal.price * 100)

        if price_fen <= 0:
            logger.error(f"信号 {signal.id} 价格无效")
            return None

        acc = self._get_account(db)
        board_type = get_board_type(stock_code)

        # 计算总资产 (现金+持仓市值)
        market_value = PositionManager.get_total_market_value(db)
        total_equity = acc.cash + acc.frozen + market_value

        # === 风控管道 ===
        pos = PositionManager.get_one(db, stock_code)
        limit_up_fen = 0
        limit_down_fen = 0
        # 基于当前价格和板块涨跌幅限制计算涨跌停价格
        limit_pct = get_price_limit_pct(stock_code)
        if price_fen > 0:
            limit_up_fen = int(price_fen * (1 + limit_pct))
            limit_down_fen = int(price_fen * (1 - limit_pct))
        today_bought = pos.today_bought_qty if pos else 0
        amount_fen = price_fen * quantity

        ok, reason = self.risk.pipeline_check(
            stock_code, direction, price_fen, quantity,
            acc.cash, total_equity,
            limit_up_fen=limit_up_fen, limit_down_fen=limit_down_fen
        )
        if not ok:
            order = TradingOrder(
                signal_id=signal.id, stock_code=stock_code,
                stock_name=signal.stock_name, direction=direction,
                board_type=board_type,
                order_type="market", quantity=quantity,
                status="rejected", rejection_reason=reason,
                submitted_at=datetime.now(),
            )
            db.add(order)
            db.commit()
            return order

        # === 状态: submitted ===
        order = TradingOrder(
            signal_id=signal.id, stock_code=stock_code,
            stock_name=signal.stock_name, direction=direction,
            board_type=board_type,
            order_type="market", quantity=quantity,
            status="submitted", submitted_at=datetime.now(),
        )
        db.add(order)
        db.flush()

        # === 事务主流程 (带回滚保护) ===
        try:
            result = self.broker.execute_market_order(
                stock_code, direction, price_fen, quantity,
                today_bought_qty=today_bought,
            )
            if not result:
                order.status = "rejected"
                order.rejection_reason = "撮合失败(T+1/涨跌停/数量)"
                db.commit()
                return order

            total_cost = result["amount"] + result["fee"]
            fee_detail = result.get("fee_detail", {
                "commission": result["fee"], "stamp_tax": 0,
                "transfer": 0, "handling": 0, "regulatory": 0, "total": result["fee"]
            })

            # === 更新订单 → filled ===
            order.status = "filled"
            order.filled_price = result["filled_price"]
            order.filled_quantity = result["filled_quantity"]
            order.fee = result["fee"]
            order.fee_detail = fee_detail
            order.filled_at = datetime.now()

            # === 更新账户现金 (同Session) ===
            if direction == "buy":
                acc.cash -= total_cost
            else:
                acc.cash += result["amount"] - result["fee"]
            # 更新资产峰值 (基于当前持仓市值)
            market_value_now = PositionManager.get_total_market_value(db)
            if market_value_now > 0:
                current_total = acc.cash + acc.frozen + market_value_now
                acc.total_value = current_total

            # PnL 不在 trade path 中直接计算 (避免累积误差)
            # 使用 recalculate_pnl() 在交易完成后统一重新计算

            # === 更新 Position ===
            pnl_fen = 0
            if direction == "sell" and pos:
                pnl_fen = (result["filled_price"] - pos.avg_cost) * quantity - result["fee"]
            if direction == "buy":
                PositionManager.update_on_buy(
                    db, stock_code, signal.stock_name,
                    result["filled_price"], quantity, result["amount"]
                )
            else:
                PositionManager.update_on_sell(
                    db, stock_code, signal.stock_name,
                    result["filled_price"], quantity, pnl_fen
                )

            # === 交易日志 (卖出存负数) ===
            log_qty = quantity if direction == "buy" else -quantity
            log = TradeLog(
                order_id=order.id, stock_code=stock_code, stock_name=signal.stock_name,
                direction=direction, price=result["filled_price"],
                quantity=log_qty, amount=result["amount"], fee=result["fee"],
                pnl=pnl_fen if direction == "sell" else None,
                pnl_pct=round(pnl_fen / (pos.avg_cost * quantity), 4) if direction == "sell" and pos and pos.avg_cost > 0 else None,
                strategy_name=signal.strategy_name,
                signal_id=signal.id,
            )
            db.add(log)

            # === 更新信号 → executed ===
            signal_obj = db.query(TradingSignal).filter(TradingSignal.id == signal.id).first()
            if signal_obj:
                signal_obj.status = "executed"

            db.commit()
            db.refresh(order)
            db.refresh(log)
            self.account.recalculate_pnl()

            logger.info(f"订单成交: {direction} {stock_code} {quantity}股 @¥{result['filled_price']/100:.2f} "
                         f"费用¥{result['fee']/100:.2f}")
            return order

        except Exception as e:
            db.rollback()
            logger.error(f"订单执行事务回滚: {e}", exc_info=True)
            # 创建一条回滚记录
            rollback_order = TradingOrder(
                signal_id=signal.id, stock_code=stock_code,
                stock_name=signal.stock_name, direction=direction,
                board_type=board_type,
                order_type="market", quantity=quantity,
                status="error", rejection_reason=f"事务回滚: {str(e)[:200]}",
                submitted_at=datetime.now(),
            )
            db.add(rollback_order)
            db.commit()
            return rollback_order

    def get_orders(self, status: str = "all", limit: int = 50) -> List[TradingOrder]:
        q = self._get_db().query(TradingOrder)
        if status != "all":
            q = q.filter(TradingOrder.status == status)
        return q.order_by(TradingOrder.created_at.desc()).limit(limit).all()

    def cancel_order(self, order_id: int) -> Optional[TradingOrder]:
        db = self._get_db()
        o = db.query(TradingOrder).filter(TradingOrder.id == order_id).first()
        if not o or o.status in TradingOrder.TERMINAL_STATUSES:
            return None
        o.status = "cancelled"
        o.rejection_reason = "用户撤销"
        db.commit()
        return o
