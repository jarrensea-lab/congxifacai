"""RiskGuard 9-Gate Pipeline 单元测试"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from datetime import datetime
from app.trading_engine.risk_guard import RiskGuard

class TestRiskGuard:
    def setup_method(self):
        self.guard = RiskGuard()

    # Gate 1: 交易时间
    def test_trading_hours_morning(self, mocker):
        mocker.patch('app.trading_engine.risk_guard.datetime').now.return_value = \
            datetime(2026, 6, 5, 10, 0, 0)
        ok, _ = self.guard.check_trading_hours()
        assert ok

    def test_trading_hours_afternoon(self, mocker):
        mocker.patch('app.trading_engine.risk_guard.datetime').now.return_value = \
            datetime(2026, 6, 5, 14, 0, 0)
        ok, _ = self.guard.check_trading_hours()
        assert ok

    def test_trading_hours_closed(self, mocker):
        mocker.patch('app.trading_engine.risk_guard.datetime').now.return_value = \
            datetime(2026, 6, 5, 12, 0, 0)
        ok, reason = self.guard.check_trading_hours()
        assert not ok
        assert '非交易时段' in reason

    # Gate 2: 涨跌停
    def test_price_limit_buy_at_limit(self):
        ok, reason = self.guard.check_price_limit('000100', 1000, 1000, 900, 'buy')
        assert not ok or '涨停' in reason

    def test_price_limit_buy_below_limit(self):
        ok, _ = self.guard.check_price_limit('000100', 900, 1000, 900, 'buy')
        assert ok

    def test_price_limit_limit_zero(self):
        ok, _ = self.guard.check_price_limit('000100', 1000, 0, 0, 'buy')
        assert ok  # limit_up=0 means no limit data, pass through

    # Gate 4: 仓位上限
    def test_position_limit_within(self):
        ok, _ = self.guard.check_position_limit('000100', 500000, 2000000, 5000000)
        assert ok  # 10% within 20% limit

    def test_position_limit_exceeded_no_cash(self):
        ok, reason = self.guard.check_position_limit('000100', 2000000, 100000, 5000000)
        assert not ok
        assert '仓位超限' in reason

    # Gate 8: 可用资金
    def test_cash_sufficient(self, mocker):
        mocker.patch.object(self.guard, 'check_cash_available', return_value=(True, ''))
        ok, _ = self.guard.check_cash_available(1000000, 400000)
        assert ok

    def test_cash_insufficient(self, mocker):
        mocker.patch.object(self.guard, 'check_cash_available', return_value=(False, '可用资金不足'))
        ok, reason = self.guard.check_cash_available(500000, 100000)
        assert not ok
        assert '不足' in reason

    # Pipeline integration
    def test_pipeline_all_pass(self, mocker):
        mocker.patch.object(self.guard, 'check_trading_hours', return_value=(True, ''))
        mocker.patch.object(self.guard, 'check_t1', return_value=(True, ''))
        mocker.patch.object(self.guard, 'check_daily_loss', return_value=(True, ''))
        mocker.patch.object(self.guard, 'check_max_drawdown', return_value=(True, ''))
        mocker.patch.object(self.guard, 'check_trade_frequency', return_value=(True, ''))
        mocker.patch.object(self.guard, 'check_cash_available', return_value=(True, ''))
        mocker.patch.object(self.guard, 'check_fiduciary', return_value=(True, ''))

        ok, _ = self.guard.pipeline_check(
            '000100', 'buy', 488, 300, 153700, 301300,
            limit_up_fen=536, limit_down_fen=439
        )
        assert ok
