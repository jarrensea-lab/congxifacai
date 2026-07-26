import pytest

from app.services.playbook_engine import select_playbook


def twenty_structural_bars(*, high=4.2, low=3.6):
    return [
        {"open": 3.8, "close": 3.9, "high": high, "low": low}
        for _ in range(20)
    ]


def test_select_playbook_prefers_breakout_when_volume_confirms():
    result = select_playbook(
        {
            "quote": {"price": 4.2, "change_pct": 4.2, "vol_ratio": 2.6, "amount_wan": 18000},
            "kline": {"bars": twenty_structural_bars()},
            "fund_flow": {"net": "净流入"},
            "trigger_price": 4.0,
        }
    )

    assert result["triggered"] is True
    assert result["playbook"] == "breakout_entry"
    assert result["score_bonus"] == 0


def test_breakout_requires_price_to_reach_historical_resistance():
    result = select_playbook(
        {
            "quote": {"price": 3.97, "change_pct": 3.39, "vol_ratio": 3.33, "amount_wan": 32688},
            "kline": {"bars": twenty_structural_bars(high=4.2, low=3.6)},
            "trigger_price": 3.8,
        }
    )

    assert result["triggered"] is False
    assert result["block_reason"] == "breakout_resistance_not_crossed"
    assert "达到或越过" in result["next_signal"]


@pytest.mark.parametrize("bars", [[{} for _ in range(20)], [None for _ in range(20)]])
def test_breakout_fails_closed_without_twenty_valid_ohlc_bars(bars):
    result = select_playbook(
        {
            "quote": {"price": 4.2, "change_pct": 4.2, "vol_ratio": 2.6, "amount_wan": 18000},
            "kline": {"bars": bars},
            "trigger_price": 4.0,
        }
    )

    assert result["triggered"] is False
    assert result["block_reason"] == "breakout_kline_insufficient"


@pytest.mark.parametrize("trigger_price", [None, 0, "0", "invalid"])
def test_breakout_fails_closed_without_valid_positive_trigger(trigger_price):
    result = select_playbook(
        {
            "quote": {"price": 4.2, "change_pct": 4.2, "vol_ratio": 2.6, "amount_wan": 18000},
            "kline": {"bars": twenty_structural_bars()},
            "trigger_price": trigger_price,
        }
    )

    assert result["triggered"] is False
    assert result["block_reason"] == "breakout_trigger_invalid"


def test_breakout_fails_closed_without_twenty_bars():
    result = select_playbook(
        {
            "quote": {"price": 3.97, "change_pct": 3.39, "vol_ratio": 3.33, "amount_wan": 32688},
            "kline": {"bars": []},
            "trigger_price": 4.52,
        }
    )

    assert result["triggered"] is False
    assert result["block_reason"] == "breakout_kline_insufficient"


def test_breakout_fails_closed_below_configured_trigger():
    result = select_playbook(
        {
            "quote": {"price": 3.97, "change_pct": 3.39, "vol_ratio": 3.33, "amount_wan": 32688},
            "kline": {"bars": twenty_structural_bars()},
            "trigger_price": 4.52,
        }
    )

    assert result["triggered"] is False
    assert result["block_reason"] == "breakout_trigger_not_crossed"


def test_select_playbook_blocks_high_position_breakout_until_pullback():
    bars = [
        {
            "open": 6.0 + idx * 0.05,
            "close": 6.0 + idx * 0.05,
            "high": 6.05 + idx * 0.05,
            "low": 5.95 + idx * 0.05,
        }
        for idx in range(20)
    ]

    result = select_playbook(
        {
            "quote": {"price": 6.82, "change_pct": 4.2, "vol_ratio": 2.6, "amount_wan": 18000},
            "kline": {"bars": bars},
            "fund_flow": {"net": "净流入"},
            "trigger_price": 6.8,
        }
    )

    assert result["triggered"] is False
    assert result["playbook"] == "breakout_watch"
    assert result["block_reason"] == "blocked_high_position"
    assert result["range_position_pct"] >= 80


def test_select_playbook_triggers_dip_entry_on_pullback_holding_support():
    result = select_playbook(
        {
            "quote": {"price": 3.12, "change_pct": -1.2, "vol_ratio": 1.1, "amount_wan": 7800},
            "kline": {
                "bars": [
                    {"close": 3.0, "high": 3.08, "low": 2.95},
                    {"close": 3.18, "high": 3.24, "low": 3.02},
                    {"close": 3.28, "high": 3.32, "low": 3.11},
                    {"close": 3.12, "high": 3.18, "low": 3.1},
                ]
            },
            "fund_flow": {"net": "资金流出收敛"},
        }
    )

    assert result["triggered"] is True
    assert result["playbook"] == "dip_entry"
    assert result["score_bonus"] == 8


def test_select_playbook_blocks_dip_when_support_breaks():
    result = select_playbook(
        {
            "quote": {"price": 2.9, "change_pct": -4.8, "vol_ratio": 1.4, "amount_wan": 8600},
            "kline": {
                "bars": [
                    {"close": 3.0, "high": 3.08, "low": 2.95},
                    {"close": 3.18, "high": 3.24, "low": 3.02},
                    {"close": 3.28, "high": 3.32, "low": 3.11},
                    {"close": 2.9, "high": 3.02, "low": 2.86},
                ]
            },
            "fund_flow": {"net": "净流入"},
        }
    )

    assert result["triggered"] is False
    assert result["playbook"] == "watch"
