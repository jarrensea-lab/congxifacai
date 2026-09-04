from app.services.position_sizing import calculate_position_size


def test_calculate_position_size_caps_by_trade_risk_budget():
    result = calculate_position_size(
        code="002123",
        entry_price=3.2,
        stop_loss=3.04,
        available_cash=6085.61,
        total_assets=6085.61,
        profile={
            "cash_reserve_pct": 10,
            "single_position_limit_pct": 50,
            "risk_per_trade_pct": 1,
        },
    )

    assert result["position_amount"] == 960.0
    assert result["shares"] == 300
    assert result["risk_amount"] == 48.0
    assert result["risk_budget"] == 60.86
    assert result["executable_budget"] == 3042.8
    assert result["block_reason"] == ""


def test_calculate_position_size_blocks_when_one_lot_exceeds_risk_budget():
    result = calculate_position_size(
        code="002123",
        entry_price=7.0,
        stop_loss=6.2,
        available_cash=6085.61,
        total_assets=6085.61,
        profile={
            "cash_reserve_pct": 10,
            "single_position_limit_pct": 50,
            "risk_per_trade_pct": 1,
        },
    )

    assert result["position_amount"] == 0
    assert result["shares"] == 0
    assert result["lot_size"] == 100
    assert result["block_reason"] == "risk_budget_too_small"


def test_calculate_position_size_falls_back_to_cash_when_assets_missing():
    result = calculate_position_size(
        code="002123",
        entry_price=3.2,
        stop_loss=3.04,
        available_cash=6085.61,
        total_assets=0,
        profile={
            "cash_reserve_pct": 10,
            "single_position_limit_pct": 50,
            "risk_per_trade_pct": 1,
        },
    )

    assert result["risk_budget"] == 60.86
    assert result["position_amount"] == 960.0


def test_position_size_reports_one_lot_risk_and_concentration():
    result = calculate_position_size(
        code="002185",
        entry_price=15.53,
        stop_loss=13.98,
        available_cash=6281.8,
        total_assets=10058.8,
        profile={
            "cash_reserve_pct": 10,
            "single_position_limit_pct": 50,
            "risk_per_trade_pct": 2,
        },
    )

    assert result["risk_per_lot"] == 155.0
    assert result["lot_concentration_pct"] == 15.44
    assert result["risk_budget_utilization_pct"] == 77.05


def test_blocked_position_still_reports_risk_audit():
    result = calculate_position_size(
        code="002837",
        entry_price=49.43,
        stop_loss=44.49,
        available_cash=6281.8,
        total_assets=10058.8,
        profile={
            "cash_reserve_pct": 10,
            "single_position_limit_pct": 50,
            "risk_per_trade_pct": 2,
        },
    )

    assert result["block_reason"] == "risk_budget_too_small"
    assert result["risk_per_lot"] == 494.0
    assert result["lot_concentration_pct"] == 49.14
    assert result["risk_budget_utilization_pct"] == 245.55
