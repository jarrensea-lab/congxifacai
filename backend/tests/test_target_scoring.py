from app.services.target_scoring import score_target


def _base_snapshot(code="002123", price=3.2):
    return {
        "code": code,
        "name": "测试标的",
        "quote": {
            "status": "ok",
            "price": price,
            "change_pct": 4.2,
            "amount_wan": 18000,
            "turnover_pct": 8.0,
            "vol_ratio": 2.6,
        },
        "kline": {
            "status": "ok",
            "bars": [
                {"close": 2.8, "high": 2.9, "low": 2.7},
                {"close": 3.0, "high": 3.05, "low": 2.85},
                {"close": price, "high": price, "low": 3.0},
            ],
        },
        "fund_flow": {"status": "ok", "net": "净流入"},
        "financial": {"status": "ok", "revenue_yoy_pct": 12.0, "gross_margin_pct": 35.0},
        "news": {"status": "ok", "items": [{"title": "订单增长"}]},
        "sentinel": {"status": "ok", "evidence_ids": ["ev_test"]},
        "serenity": {"status": "ok", "score": 65, "theme": "测试主题"},
    }


def test_score_target_marks_unaffordable_stock_as_research_only():
    result = score_target(
        _base_snapshot(code="688008", price=68.5),
        available_cash=6085.61,
        total_assets=6085.61,
    )

    assert result["action"] == "research_only"
    assert result["block_reason"] == "lot_size_exceeded"
    assert result["lot_size"] == 200
    assert result["lot_value"] == 13700.0
    assert "买不起最小交易单位" in result["decision_reason"]


def test_score_target_returns_buy_for_low_price_volume_breakout():
    result = score_target(
        _base_snapshot(code="002123", price=3.2),
        available_cash=6085.61,
        total_assets=6085.61,
    )

    assert result["action"] == "buy"
    assert result["entry_price"] == 3.2
    assert result["stop_loss"] == 3.04
    assert result["target_price"] > result["entry_price"]
    assert result["position_amount"] <= 2129.97
    assert result["missing_data"] == []


def test_score_target_names_missing_data_instead_of_generic_insufficient():
    snapshot = _base_snapshot()
    snapshot["fund_flow"] = {"status": "missing"}
    snapshot["financial"] = {"status": "missing"}

    result = score_target(snapshot, available_cash=6085.61, total_assets=6085.61)

    assert result["action"] == "watch"
    assert result["block_reason"] == "missing_required_data"
    assert result["missing_data"] == ["fund_flow", "financial"]
    assert "数据不足" not in result["decision_reason"]
    assert result["entry_price"] == 3.2
    assert result["stop_loss"] == 3.04
    assert result["target_price"] == 3.58
    assert "补齐" in result["next_signal"]
