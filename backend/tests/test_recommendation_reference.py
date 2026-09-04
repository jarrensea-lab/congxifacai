from app.services.recommendation_reference import audit_recommendation_reference


def test_reference_quote_is_stale_after_large_price_divergence():
    result = audit_recommendation_reference(
        {"realtime_quote": {"price": 5.68, "trading_date": "2026-08-03"}},
        current_price=7.96,
    )

    assert result["status"] == "stale_divergence"
    assert result["usable_for_current_action"] is False
    assert result["reference_price"] == 5.68
    assert result["current_price"] == 7.96
    assert result["divergence_pct"] == 40.14
    assert result["reference_trading_date"] == "2026-08-03"


def test_reference_quote_within_five_percent_is_current():
    result = audit_recommendation_reference(
        {"realtime_quote": {"price": 10.0}},
        current_price=10.4,
    )

    assert result["status"] == "current"
    assert result["usable_for_current_action"] is True
    assert result["divergence_pct"] == 4.0


def test_reference_quote_without_prices_is_unverifiable():
    result = audit_recommendation_reference({}, current_price=10.0)

    assert result == {
        "status": "unverifiable",
        "reference_price": None,
        "current_price": 10.0,
        "divergence_pct": None,
        "reference_trading_date": None,
        "usable_for_current_action": False,
    }
