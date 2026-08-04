from app.services.financial_quality import assess_financial_quality


def test_compounder_quality_rewards_cash_conversion_and_clean_working_capital():
    result = assess_financial_quality({
        "status": "ok",
        "earnings_profile": "compounder",
        "revenue_yoy_pct": 18,
        "gross_margin_pct": 35,
        "gross_margin_yoy_pct": 2,
        "inventory_yoy_pct": 8,
        "receivable_yoy_pct": 9,
        "operating_cashflow_yoy_pct": 25,
        "free_cash_flow": 120_000_000,
        "debt_to_asset_pct": 32,
        "pe_ttm": 24,
    })

    assert result["score"] >= 80
    assert result["coverage"] >= 0.8
    assert result["valuation_eligible"] is True
    assert result["flags"] == []


def test_working_capital_and_cash_flow_deterioration_are_auditable():
    result = assess_financial_quality({
        "status": "ok",
        "revenue_yoy_pct": 5,
        "gross_margin_yoy_pct": -3,
        "inventory_yoy_pct": 28,
        "receivable_yoy_pct": 31,
        "operating_cashflow_yoy_pct": -45,
        "free_cash_flow": -1,
    })

    assert result["score"] < 50
    assert set(result["flags"]) >= {
        "gross_margin_declining",
        "inventory_outpaces_revenue",
        "receivable_outpaces_revenue",
        "operating_cashflow_deteriorating",
        "free_cash_flow_negative",
    }


def test_cyclical_low_pe_needs_normalized_earnings_or_positive_free_cash_flow():
    weak = assess_financial_quality({
        "status": "ok",
        "earnings_profile": "cyclical",
        "pe_ttm": 8,
        "free_cash_flow": -100,
    })
    cash_confirmed = assess_financial_quality({
        "status": "ok",
        "earnings_profile": "cyclical",
        "pe_ttm": 8,
        "free_cash_flow": 100,
    })

    assert weak["valuation_eligible"] is False
    assert "cyclical_valuation_unconfirmed" in weak["flags"]
    assert cash_confirmed["valuation_eligible"] is True


def test_missing_financial_payload_is_explicit_and_neutral():
    result = assess_financial_quality({"status": "missing"})

    assert result == {
        "status": "missing",
        "score": 0.0,
        "coverage": 0.0,
        "earnings_profile": "unknown",
        "flags": ["financial_source_missing"],
        "valuation_eligible": False,
    }
