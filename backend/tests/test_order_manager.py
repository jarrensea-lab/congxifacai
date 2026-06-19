"""订单管理器单元测试 — OrderManager 事务回滚 + 全流程"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from unittest.mock import MagicMock

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.models import TradingSignal, TradingOrder, SimAccount, Position
from app.trading_engine.order_manager import OrderManager


class TestOrderManager:
    def setup_method(self):
        """每个测试前建立内存 SQLite 数据库 + 注入模拟依赖"""
        self.engine = create_engine('sqlite:///:memory:')
        Base.metadata.create_all(bind=self.engine)
        self.db = Session(self.engine)

        # 创建初始模拟账户 (10万元)
        self.db.add(SimAccount())
        self.db.commit()

        # 模拟依赖
        self.account_mgr = MagicMock()
        self.broker = MagicMock()
        self.risk = MagicMock()
        self.signal_engine = MagicMock()

        # 注入到 OrderManager
        self.om = OrderManager(self.account_mgr, self.broker, self.risk, self.signal_engine)
        self.om._db = self.db

    def teardown_method(self):
        self.db.close()
        self.engine.dispose()
        self.om._db = None

    # ===== helper =====

    def _make_signal(self, **overrides) -> TradingSignal:
        params = dict(
            id=1,
            stock_code='000100',
            stock_name='TCL科技',
            signal_type='buy',
            suggested_qty=100,
            price=50000,  # 500 元 in fen
            status='approved',
            strategy_name='test_strategy',
        )
        params.update(overrides)
        return TradingSignal(**params)

    def _broker_result(self, **overrides) -> dict:
        """模拟撮合引擎成功返回"""
        params = dict(
            filled_price=50000,
            filled_quantity=100,
            amount=5000000,
            fee=25,
            fee_detail={'commission': 25, 'stamp_tax': 0, 'transfer': 0, 'handling': 0, 'regulatory': 0, 'total': 25},
        )
        params.update(overrides)
        return params

    # ===== 信号前置检查 =====

    def test_signal_not_approved(self):
        signal = self._make_signal(status='pending')
        result = self.om.create_from_signal(signal)
        assert result is None, '未批准的信号应返回 None'

    def test_invalid_price_zero(self):
        signal = self._make_signal(price=0)
        result = self.om.create_from_signal(signal)
        assert result is None, '价格为零应返回 None'

    def test_invalid_price_negative(self):
        signal = self._make_signal(price=-1)
        result = self.om.create_from_signal(signal)
        assert result is None, '价格为负应返回 None'

    # ===== 风控拒绝 =====

    def test_risk_check_rejects(self):
        self.risk.pipeline_check.return_value = (False, '仓位超限')
        signal = self._make_signal()
        result = self.om.create_from_signal(signal)
        assert result is not None
        assert result.status == 'rejected'
        assert '仓位超限' in result.rejection_reason

    # ===== 撮合拒绝 =====

    def test_broker_rejects_t1(self):
        self.risk.pipeline_check.return_value = (True, 'ok')
        self.broker.execute_market_order.return_value = None
        signal = self._make_signal()
        result = self.om.create_from_signal(signal)
        assert result is not None
        assert result.status == 'rejected'
        assert result.rejection_reason is not None

    # ===== 成功买入 =====

    def test_successful_buy(self):
        self.risk.pipeline_check.return_value = (True, 'ok')
        self.broker.execute_market_order.return_value = self._broker_result()

        signal = self._make_signal()
        result = self.om.create_from_signal(signal)

        assert result is not None
        assert result.status == 'filled'
        assert result.direction == 'buy'
        assert result.filled_quantity == 100
        assert result.filled_price == 50000
        assert result.stock_code == '000100'

        # 验证账户现金被扣除
        acc = self.db.query(SimAccount).first()
        expected_cash = 10000000 - (5000000 + 25)  # default cash - (amount + fee)
        assert acc.cash == expected_cash, f'账户现金应为 {expected_cash}, 实际 {acc.cash}'

        # 验证持仓已创建
        pos = self.db.query(Position).filter(Position.stock_code == '000100').first()
        assert pos is not None
        assert pos.quantity == 100
        assert pos.avg_cost == 50000

        # 验证 TradeLog 已创建
        from app.models import TradeLog
        log = self.db.query(TradeLog).first()
        assert log is not None
        assert log.direction == 'buy'
        assert log.quantity == 100

    # ===== 成功卖出 =====

    def test_successful_sell(self):
        # 先手动创建持仓
        from app.trading_engine.position import PositionManager
        PositionManager.update_on_buy(self.db, '000100', 'TCL科技', 50000, 100, 5000000)

        self.risk.pipeline_check.return_value = (True, 'ok')
        self.broker.execute_market_order.return_value = self._broker_result(
            filled_price=52000, amount=5200000,
            fee_detail={'commission': 25, 'stamp_tax': 25, 'transfer': 0, 'handling': 0, 'regulatory': 0, 'total': 50},
        )

        signal = self._make_signal(id=2, stock_code='000100', stock_name='TCL科技',
                                    signal_type='sell', price=52000, suggested_qty=100)
        result = self.om.create_from_signal(signal)

        assert result is not None
        assert result.status == 'filled'
        assert result.direction == 'sell'

        # 验证持仓减少
        pos = PositionManager.get_one(self.db, '000100')
        assert pos is not None
        assert pos is None or pos.quantity == 0, f'卖出 100 股后应清仓, 实际 {pos.quantity if pos else 0}'

    # ===== 事务回滚 =====

    def test_transaction_rollback_on_broker_exception(self):
        """Broker 抛出异常 → 事务回滚 → error 记录"""
        self.risk.pipeline_check.return_value = (True, 'ok')
        self.broker.execute_market_order.side_effect = Exception('撮合引擎网络超时')

        signal = self._make_signal()
        result = self.om.create_from_signal(signal)

        assert result is not None
        assert result.status == 'error', f'回滚后应为 error, 实际 {result.status}'
        assert '撮合引擎网络超时' in result.rejection_reason or '事务回滚' in result.rejection_reason

        # 验证数据库未残留 submitted 订单
        submitted_orders = self.db.query(TradingOrder).filter(TradingOrder.status == 'submitted').all()
        assert len(submitted_orders) == 0, '回滚后不应有 submitted 订单'

    def test_get_orders_by_status(self):
        self.risk.pipeline_check.return_value = (True, 'ok')
        self.broker.execute_market_order.return_value = self._broker_result()
        self.om.create_from_signal(self._make_signal())

        filled_orders = self.om.get_orders(status='filled')
        assert len(filled_orders) == 1

        pending_orders = self.om.get_orders(status='submitted')
        assert len(pending_orders) == 0

    def test_get_orders_all(self):
        self.risk.pipeline_check.return_value = (True, 'ok')
        self.broker.execute_market_order.return_value = self._broker_result()
        self.om.create_from_signal(self._make_signal())

        all_orders = self.om.get_orders(status='all')
        assert len(all_orders) == 1

    # ===== 撤销 =====

    def test_cancel_open_order(self):
        order = TradingOrder(
            stock_code='000100', stock_name='TCL科技',
            direction='buy', order_type='market',
            status='submitted', quantity=100,
        )
        self.db.add(order)
        self.db.commit()

        result = self.om.cancel_order(order.id)
        assert result is not None
        assert result.status == 'cancelled'

    def test_cancel_filled_order_returns_none(self):
        order = TradingOrder(
            stock_code='000100', stock_name='TCL科技',
            status='filled', direction='buy',
            order_type='market', quantity=100,
        )
        self.db.add(order)
        self.db.commit()

        result = self.om.cancel_order(order.id)
        assert result is None, '已成交订单不可撤销'
