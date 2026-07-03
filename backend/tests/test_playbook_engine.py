from app.services.playbook_engine import select_playbook


def test_select_playbook_prefers_breakout_when_volume_confirms():
    result = select_playbook(
        {
            "quote": {"price": 3.2, "change_pct": 4.2, "vol_ratio": 2.6, "amount_wan": 18000},
            "kline": {"bars": []},
            "fund_flow": {"net": "净流入"},
        }
    )

    assert result["triggered"] is True
    assert result["playbook"] == "breakout_entry"
    assert result["score_bonus"] == 0


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
