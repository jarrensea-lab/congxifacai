import pytest

import app.services.target_scoring as target_scoring
from app.services.target_scoring import score_long_quality, score_target


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
                {
                    "open": price * 0.95,
                    "close": price * 0.98,
                    "high": price,
                    "low": price * 0.9,
                }
                for _ in range(20)
            ],
        },
        "trigger_price": price,
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
    assert result["stop_loss"] == 2.88
    assert result["target_price"] == 3.84
    assert result["executable_budget"] == 3042.8
    assert result["risk_budget"] == 121.71
    assert result["position_amount"] == 960.0
    assert result["position_shares"] == 300
    assert result["risk_amount"] == 96.0
    assert result["playbook"] == "breakout_entry"
    assert result["missing_data"] == []
    assert result["score_version"] == "composite_score_v1"
    assert len(result["score_components"]) == 6
    assert len(result["top_reasons"]) == 3
    assert result["grade"] in {"S", "A"}
    assert result["primary_risk"]


def test_score_target_caps_research_only_provenance_even_when_buy_trigger_fires():
    snapshot = _base_snapshot(code="002123", price=3.2)
    snapshot["production_eligibility"] = {
        "eligible": False,
        "research_only": True,
        "original_status": "research_reference",
        "reason": "research_only_provenance",
    }

    result = score_target(
        snapshot,
        available_cash=6085.61,
        total_assets=6085.61,
    )

    assert result["score"] >= 70
    assert result["action"] == "research_only"
    assert result["block_reason"] == "research_only_provenance"
    assert result["position_amount"] == 0
    assert result["position_shares"] == 0


def test_score_target_blocks_high_position_breakout():
    snapshot = _base_snapshot(code="002123", price=6.82)
    snapshot["quote"].update({"change_pct": 4.2, "vol_ratio": 2.6, "amount_wan": 18000})
    snapshot["kline"]["bars"] = [
        {
            "open": 6.0 + idx * 0.05,
            "close": 6.0 + idx * 0.05,
            "high": 6.05 + idx * 0.05,
            "low": 5.95 + idx * 0.05,
        }
        for idx in range(20)
    ]

    result = score_target(snapshot, available_cash=6085.61, total_assets=6085.61)

    assert result["action"] == "watch"
    assert result["block_reason"] == "blocked_high_position"
    assert result["playbook"] == "breakout_watch"
    assert result["score"] <= 68
    assert "不再按突破追买" in result["decision_reason"]


def test_score_target_returns_buy_for_dip_entry_when_pullback_holds_support():
    snapshot = _base_snapshot(code="002123", price=3.12)
    snapshot["quote"].update({"change_pct": -1.2, "amount_wan": 7800, "vol_ratio": 1.1})
    snapshot["kline"]["bars"] = [
        {"close": 3.0, "high": 3.08, "low": 2.95},
        {"close": 3.18, "high": 3.24, "low": 3.02},
        {"close": 3.28, "high": 3.32, "low": 3.11},
        {"close": 3.12, "high": 3.18, "low": 3.1},
    ]
    snapshot["fund_flow"] = {"status": "ok", "net": "资金流出收敛"}
    snapshot["serenity"] = {"status": "ok", "score": 90, "theme": "测试主题"}

    result = score_target(snapshot, available_cash=6085.61, total_assets=6085.61)

    assert result["action"] == "buy"
    assert result["playbook"] == "dip_entry"
    assert result["position_amount"] > 0
    assert "低吸回踩触发" in result["decision_reason"]


def test_score_target_blocks_dip_entry_when_market_regime_is_bad():
    snapshot = _base_snapshot(code="002123", price=3.12)
    snapshot["quote"].update({"change_pct": -1.2, "amount_wan": 7800, "vol_ratio": 1.1})
    snapshot["kline"]["bars"] = [
        {"close": 3.0, "high": 3.08, "low": 2.95},
        {"close": 3.18, "high": 3.24, "low": 3.02},
        {"close": 3.28, "high": 3.32, "low": 3.11},
        {"close": 3.12, "high": 3.18, "low": 3.1},
    ]
    snapshot["fund_flow"] = {"status": "ok", "net": "资金流出收敛"}
    snapshot["serenity"] = {"status": "ok", "score": 90, "theme": "测试主题"}
    snapshot["market_regime"] = {"label": "panic", "index_change_pct": -2.4, "breadth": 0.18}

    result = score_target(snapshot, available_cash=6085.61, total_assets=6085.61)

    assert result["action"] == "watch"
    assert result["block_reason"] == "regime_blocks_dip"
    assert result["playbook"] == "dip_entry"


def test_score_target_blocks_breakout_when_market_regime_is_panic():
    snapshot = _base_snapshot(code="002123", price=3.2)
    snapshot["market_regime"] = {
        "label": "panic",
        "index_change_pct": -2.4,
        "breadth": 0.18,
    }

    result = score_target(
        snapshot,
        available_cash=6085.61,
        total_assets=6085.61,
    )

    assert result["action"] == "watch"
    assert result["block_reason"] == "regime_blocks_buy"
    assert "市场环境恢复前不新开仓" in result["decision_reason"]


def test_score_target_checks_affordability_before_missing_data():
    snapshot = _base_snapshot(code="002371", price=935.36)
    snapshot["fund_flow"] = {"status": "missing", "reason": "fund_flow_not_found"}

    result = score_target(snapshot, available_cash=6085.61, total_assets=6085.61)

    assert result["action"] == "research_only"
    assert result["block_reason"] == "lot_size_exceeded"
    assert result["lot_value"] == 93536.0
    assert result["missing_data"] == ["fund_flow"]
    assert "买不起最小交易单位" in result["decision_reason"]


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
    assert result["stop_loss"] == 2.88
    assert result["target_price"] == 3.84
    assert "补齐" in result["next_signal"]


def test_score_target_carries_long_thesis_quality_without_overriding_trade_trigger():
    snapshot = _base_snapshot(code="002123", price=3.2)
    snapshot["quote"].update({"change_pct": 1.2, "vol_ratio": 1.1, "amount_wan": 8200})
    snapshot["serenity"] = {"status": "ok", "score": 82, "theme": "测试主题"}
    long_thesis = {
        "symbol": "002123",
        "quality_score": 88,
        "thesis_status": "healthy",
        "valuation_anchor": {"fair_price": 4.0, "accumulation_price": 3.3, "overpriced_price": 5.2},
        "assumptions": [{"id": "growth", "status": "intact"}],
        "red_lines": [{"id": "margin", "condition": "毛利率跌破25%", "status": "clear"}],
    }

    result = score_target(
        snapshot,
        available_cash=6085.61,
        total_assets=6085.61,
        long_thesis=long_thesis,
    )

    assert result["action"] == "watch"
    assert result["block_reason"] == "price_not_triggered"
    assert result["long_quality_score"] == 88
    assert result["thesis_status"] == "healthy"
    assert result["valuation_zone"] == "accumulation_zone"
    assert result["red_line_status"] == "clear"
    assert result["long_horizon_reason"] == "assumptions_intact"
    assert "长期跟踪：assumptions_intact" in result["combined_decision_reason"]

    long_quality = score_long_quality(snapshot, long_thesis)
    assert long_quality["long_quality_score"] == 88
    assert long_quality["valuation_zone"] == "accumulation_zone"


def test_unknown_long_thesis_cannot_lift_composite_score():
    snapshot = _base_snapshot(code="002123", price=3.2)
    snapshot["serenity"] = {"status": "ok", "score": 0}
    unknown_thesis = {
        "symbol": "002123",
        "quality_score": 88,
        "thesis_status": "unknown",
    }

    long_quality = score_long_quality(snapshot, unknown_thesis)
    result = score_target(
        snapshot,
        available_cash=6085.61,
        total_assets=6085.61,
        long_thesis=unknown_thesis,
    )
    baseline = score_target(
        snapshot,
        available_cash=6085.61,
        total_assets=6085.61,
    )

    assert long_quality["thesis_status"] == "unknown"
    assert long_quality["long_quality_score"] == 0
    assert long_quality["long_horizon_reason"] == "thesis_status_unknown"
    assert result["score"] == baseline["score"]
    assert result["action"] == baseline["action"]


def test_unknown_long_thesis_does_not_block_independently_qualified_tactical_buy():
    snapshot = _base_snapshot(code="002123", price=3.2)
    snapshot["serenity"] = {"status": "ok", "score": 65}
    unknown_thesis = {
        "symbol": "002123",
        "quality_score": 88,
        "thesis_status": "unknown",
    }

    result = score_target(
        snapshot,
        available_cash=6085.61,
        total_assets=6085.61,
        long_thesis=unknown_thesis,
    )

    assert result["thesis_status"] == "unknown"
    assert result["long_quality_score"] == 0
    assert result["long_horizon_reason"] == "thesis_status_unknown"
    assert result["score"] >= 70
    assert result["action"] == "buy"
    assert result["block_reason"] == ""


def test_score_target_blocks_trade_when_long_thesis_red_line_is_triggered():
    long_thesis = {
        "symbol": "002123",
        "quality_score": 91,
        "thesis_status": "healthy",
        "valuation_anchor": {"fair_price": 4.0, "accumulation_price": 3.3, "overpriced_price": 5.2},
        "assumptions": [{"id": "growth", "status": "intact"}],
        "red_lines": [{"id": "margin", "condition": "毛利率跌破25%", "status": "triggered"}],
    }

    result = score_target(
        _base_snapshot(code="002123", price=3.2),
        available_cash=6085.61,
        total_assets=6085.61,
        long_thesis=long_thesis,
    )

    assert result["action"] == "watch"
    assert result["block_reason"] == "long_thesis_broken"
    assert result["long_quality_score"] == 0
    assert result["thesis_status"] == "broken"
    assert result["red_line_status"] == "triggered"
    assert "中长期 thesis 红线触发" in result["decision_reason"]


def test_score_target_keeps_forming_thesis_incomplete_and_carries_current_evidence():
    long_thesis = {
        "symbol": "002123",
        "quality_score": 88,
        "thesis_status": "forming",
        "verification_status": "incomplete",
        "missing_verification": ["financial"],
        "current_long_evidence_ids": ["long-thesis:002123:v2"],
        "assumptions": [{"id": "growth", "status": "intact"}],
        "red_lines": [{"id": "margin", "status": "clear"}],
    }
    snapshot = _base_snapshot(code="002123", price=3.2)
    snapshot["quote"].update(
        {"change_pct": 1.2, "vol_ratio": 1.1, "amount_wan": 8200}
    )

    result = score_target(
        snapshot,
        available_cash=6085.61,
        total_assets=6085.61,
        long_thesis=long_thesis,
    )

    assert result["action"] == "watch"
    assert result["thesis_status"] == "forming"
    assert result["verification_status"] == "incomplete"
    assert result["current_long_evidence_ids"] == ["long-thesis:002123:v2"]


@pytest.mark.parametrize(
    ("current_status", "expected"),
    [
        ("long_watch", "long_watch"),
        ("accumulation_zone", "accumulation_zone"),
        ("tactical_watch", "tactical_watch"),
    ],
)
def test_next_target_status_preserves_explicit_long_state_during_watch_scan(
    current_status,
    expected,
):
    assert callable(getattr(target_scoring, "next_target_status", None))
    assert (
        target_scoring.next_target_status(
            current_status,
            "watch",
            {
                "thesis_status": "healthy",
                "long_quality_score": 90,
                "red_line_status": "clear",
            },
        )
        == expected
    )


def test_next_target_status_routes_forming_and_broken_theses_without_buying():
    assert (
        target_scoring.next_target_status(
            "long_watch",
            "watch",
            {
                "thesis_status": "forming",
                "verification_status": "incomplete",
                "long_quality_score": 90,
            },
        )
        == "long_research"
    )
    assert (
        target_scoring.next_target_status(
            "long_watch",
            "buy",
            {
                "thesis_status": "forming",
                "verification_status": "incomplete",
                "long_quality_score": 90,
            },
            authorization_valid=True,
        )
        == "long_research"
    )
    assert (
        target_scoring.next_target_status(
            "long_research",
            "buy",
            {
                "thesis_status": "broken",
                "red_line_status": "triggered",
                "long_quality_score": 0,
            },
            authorization_valid=True,
        )
        == "thesis_review"
    )


def test_next_target_status_allows_only_authorized_tactical_buy_and_explicit_remove():
    healthy = {
        "thesis_status": "healthy",
        "long_quality_score": 95,
        "red_line_status": "clear",
    }

    assert target_scoring.next_target_status("watching", "watch", healthy) == "watching"
    assert (
        target_scoring.next_target_status("long_research", "watch", healthy)
        == "long_watch"
    )
    assert (
        target_scoring.next_target_status(
            "long_research",
            "buy",
            healthy,
            authorization_valid=False,
        )
        == "long_research"
    )
    assert (
        target_scoring.next_target_status(
            "watching",
            "buy",
            healthy,
            authorization_valid=True,
        )
        == "executable"
    )
    assert target_scoring.next_target_status("long_watch", "remove", healthy) == "removed"


def test_held_buy_is_exposed_as_add_with_position_and_quality_audit():
    snapshot = _base_snapshot(code="000100", price=3.2)
    snapshot["financial"].update({
        "earnings_profile": "cyclical",
        "free_cash_flow": 100,
        "pe_ttm": 18,
    })
    snapshot["historical_recommendation"] = {
        "realtime_quote": {"price": 2.5, "trading_date": "2026-08-03"},
    }

    result = score_target(
        snapshot,
        available_cash=6281.8,
        total_assets=10058.8,
        is_held=True,
    )

    assert result["action"] == "add"
    assert result["entry_action"] == "add"
    assert result["position_context"] == "held"
    assert result["position_management_required"] is True
    assert result["risk_per_lot"] > 0
    assert result["risk_budget_utilization_pct"] > 0
    assert result["lot_concentration_pct"] > 0
    assert result["financial_quality_score"] >= 0
    assert result["financial_quality_coverage"] > 0
    assert result["earnings_profile"] == "cyclical"
    assert result["historical_reference_status"] == "stale_divergence"
    assert result["historical_reference_divergence_pct"] == 28.0


def test_research_only_holding_remains_managed_without_entry_authorization():
    snapshot = _base_snapshot(code="000100", price=3.2)
    snapshot["production_eligibility"] = {
        "eligible": False,
        "research_only": True,
        "original_status": "research_reference",
    }

    result = score_target(
        snapshot,
        available_cash=6281.8,
        total_assets=10058.8,
        is_held=True,
    )

    assert result["action"] == "research_only"
    assert result["entry_action"] == "research_only"
    assert result["position_context"] == "held"
    assert result["position_management_required"] is True
    assert result["position_amount"] == 0
    assert result["position_shares"] == 0
