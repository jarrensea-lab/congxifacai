"""费率引擎单元测试"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from app.trading_engine.fee_schedule import (
    get_board_type, get_price_limit_pct, calc_fees, apply_slippage, get_lot_size
)

class TestBoardType:
    def test_main_board(self):
        assert get_board_type('000100') == 'main'
        assert get_board_type('600519') == 'main'

    def test_star_board(self):
        assert get_board_type('688981') == 'star'
        assert get_board_type('689009') == 'star'

    def test_chi_next(self):
        assert get_board_type('300750') == 'chi_next'
        assert get_board_type('301123') == 'chi_next'

    def test_bei_jiao(self):
        assert get_board_type('830799') == 'bei_jiao'
        assert get_board_type('430685') == 'bei_jiao'  # 4xxxx

class TestPriceLimit:
    def test_main_10pct(self):
        assert get_price_limit_pct('000100') == 0.10

    def test_star_20pct(self):
        assert get_price_limit_pct('688981') == 0.20

    def test_bei_jiao_30pct(self):
        assert get_price_limit_pct('430685') == 0.30

class TestFees:
    def test_buy_main(self):
        fees = calc_fees('000100', 'buy', 500000)  # 5000元
        assert fees['commission'] >= 500  # min commission
        assert fees['stamp_tax'] == 0     # no stamp tax on buy
        assert fees['total'] > 0

    def test_sell_main(self):
        fees = calc_fees('000100', 'sell', 500000)
        assert fees['stamp_tax'] > 0      # stamp tax on sell
        assert fees['total'] > fees['commission']

    def test_zero_amount(self):
        fees = calc_fees('000100', 'buy', 0)
        assert fees['total'] >= 0

class TestSlippage:
    def test_buy_slippage_positive(self):
        slipped = apply_slippage(1000, 'buy', '000100')
        assert slipped > 1000

    def test_sell_slippage_negative(self):
        slipped = apply_slippage(1000, 'sell', '000100')
        assert slipped < 1000

class TestLotSize:
    def test_main_lot(self):
        assert get_lot_size('000100') == 100

    def test_star_lot(self):
        assert get_lot_size('688981') == 200  # 科创板 200股/手


class TestRoundLot:
    """round_lot 边界测试 — 不足 1 手向上取整"""

    def test_exact_one_lot(self):
        from app.trading_engine.fee_schedule import round_lot
        assert round_lot(100, '000100') == 100

    def test_below_one_lot_rounds_up(self):
        from app.trading_engine.fee_schedule import round_lot
        assert round_lot(50, '000100') == 100

    def test_zero_quantity(self):
        from app.trading_engine.fee_schedule import round_lot
        assert round_lot(0, '000100') == 0

    def test_star_board_two_lots(self):
        from app.trading_engine.fee_schedule import round_lot
        assert round_lot(300, '688981') == 200  # 科创 200 起步

    def test_star_board_below_one_lot(self):
        from app.trading_engine.fee_schedule import round_lot
        assert round_lot(100, '688981') == 200  # 不足 1 手(200股) → 向上取整到 200
