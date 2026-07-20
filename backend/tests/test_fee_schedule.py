"""费率引擎单元测试"""
import sys
import os
import pytest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from app.trading_engine import fee_schedule
from app.trading_engine.fee_schedule import (
    apply_slippage,
    calc_fees,
    get_board_type,
    get_fee_config,
    get_lot_size,
    get_price_limit_pct,
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


def test_cost_model_is_versioned_and_default_commission_is_estimated():
    config = get_fee_config("000001")

    assert fee_schedule.COST_MODEL_VERSION
    assert config["cost_model_version"] == fee_schedule.COST_MODEL_VERSION
    assert config["handling_fee_rate"] == 0.0000341
    assert config["commission_estimated"] is True
    assert config["commission_source"] == "estimated_default"


def test_commission_supports_environment_and_explicit_override(monkeypatch):
    monkeypatch.setenv("CONGXI_COMMISSION_RATE", "0.0002")

    assert get_fee_config("000001")["commission_rate"] == 0.0002
    assert get_fee_config("000001")["commission_source"] == "environment"
    assert get_fee_config("000001", commission_rate=0.0001)["commission_rate"] == 0.0001
    assert get_fee_config("000001", commission_rate=0.0001)["commission_source"] == "explicit"


def test_small_account_round_trip_includes_minimum_commission_sell_tax_and_slippage(monkeypatch):
    monkeypatch.delenv("CONGXI_COMMISSION_RATE", raising=False)

    result = fee_schedule.calculate_tradable_return(
        "000001",
        entry_price=10.0,
        exit_price=11.0,
        budget_fen=200_000,
        entry_policy="prediction_close",
        exit_policy="horizon_close",
    )

    assert result["tradable"] is True
    assert result["quantity"] == 100
    assert result["gross_return_pct"] == 10.0
    assert result["buy_fees"]["commission"] == 500
    assert result["sell_fees"]["commission"] == 500
    assert result["sell_fees"]["stamp_tax"] > 0
    assert result["slippage_cost_fen"] == 200
    assert result["round_trip_cost_fen"] == 1266
    assert result["net_tradable_return_pct"] == pytest.approx(8.734, abs=0.001)
    assert result["cost_estimated"] is True


@pytest.mark.parametrize(
    ("entry_policy", "exit_policy", "budget_fen", "reason"),
    [
        (None, "horizon_close", 200_000, "entry_policy_missing"),
        ("prediction_close", None, 200_000, "exit_policy_missing"),
        ("prediction_close", "horizon_close", 100_000, "insufficient_budget_for_min_lot"),
    ],
)
def test_tradable_return_fails_closed_without_policy_or_affordable_lot(
    entry_policy, exit_policy, budget_fen, reason
):
    result = fee_schedule.calculate_tradable_return(
        "000001",
        entry_price=10.0,
        exit_price=11.0,
        budget_fen=budget_fen,
        entry_policy=entry_policy,
        exit_policy=exit_policy,
    )

    assert result["tradable"] is False
    assert result["untradable_reason"] == reason
    assert result["net_tradable_return_pct"] is None


@pytest.mark.parametrize(
    ("entry_policy", "exit_policy", "reason"),
    [
        ("next_open", "horizon_close", "unsupported_entry_policy"),
        ("prediction_close", "stop_or_target", "unsupported_exit_policy"),
    ],
)
def test_tradable_return_rejects_unimplemented_policy_values(entry_policy, exit_policy, reason):
    result = fee_schedule.calculate_tradable_return(
        "000001",
        entry_price=10.0,
        exit_price=11.0,
        budget_fen=200_000,
        entry_policy=entry_policy,
        exit_policy=exit_policy,
        commission_rate=0.00015,
    )

    assert result["tradable"] is False
    assert result["untradable_reason"] == reason
    assert result["net_tradable_return_pct"] is None
