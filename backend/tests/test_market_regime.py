from app.services.market_regime import evaluate_market_regime


def test_evaluate_market_regime_allows_dip_when_context_is_missing():
    result = evaluate_market_regime({})

    assert result["can_buy"] is True
    assert result["can_dip"] is True


def test_evaluate_market_regime_blocks_dip_on_broad_selloff():
    result = evaluate_market_regime(
        {
            "market_regime": {
                "label": "neutral",
                "index_change_pct": -2.1,
                "breadth": 0.22,
                "sector_relative_rank": 45,
            }
        }
    )

    assert result["can_dip"] is False
    assert "禁止低吸" in result["reason"]


def test_evaluate_market_regime_accepts_breadth_percent_format():
    result = evaluate_market_regime(
        {
            "market_regime": {
                "label": "neutral",
                "index_change_pct": -2.1,
                "advance_ratio": 22,
            }
        }
    )

    assert result["can_dip"] is False


def test_evaluate_market_regime_blocks_dip_when_sector_is_weak():
    result = evaluate_market_regime(
        {
            "market_regime": {
                "label": "neutral",
                "index_change_pct": 0.2,
                "breadth": 0.55,
                "sector_relative_rank": 83,
            }
        }
    )

    assert result["can_dip"] is False
