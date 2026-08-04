def complete_snapshot():
    return {
        "code": "000001",
        "name": "平安银行",
        "quote": {
            "status": "ok",
            "price": 6.0,
            "change_pct": 3.5,
            "vol_ratio": 2.4,
            "amount_wan": 20_000,
        },
        "kline": {
            "status": "ok",
            "bars": [
                {"close": 5.7, "high": 5.8, "low": 5.6},
                {"close": 5.85, "high": 5.9, "low": 5.7},
                {"close": 6.0, "high": 6.0, "low": 5.8},
            ],
        },
        "fund_flow": {
            "status": "ok",
            "main_net_amount_wan": 3500,
        },
        "financial": {
            "status": "ok",
            "revenue_yoy_pct": 12,
            "gross_margin_pct": 35,
            "pe_ttm": 16,
        },
        "news": {"status": "ok", "items": [{"title": "订单增长"}]},
        "sentinel": {"status": "ok", "evidence_ids": ["ev-test"]},
        "serenity": {"status": "ok", "score": 75, "theme": "金融科技"},
        "market_regime": {
            "label": "neutral",
            "sector_relative_rank": 20,
        },
    }


def test_composite_score_has_six_bounded_components_and_plain_reasons():
    from app.services.composite_score import build_composite_score

    score = build_composite_score(complete_snapshot())

    assert score["score_version"] == "composite_score_v1"
    assert set(score["components"]) == {
        "trend_volume",
        "fund_flow",
        "industry_catalyst",
        "fundamental_valuation",
        "relative_strength",
        "risk_reward",
    }
    assert round(sum(score["components"].values()), 1) == score["total"]
    assert len(score["top_reasons"]) == 3
    assert all(isinstance(reason, str) and reason for reason in score["top_reasons"])
    assert score["grade"] in {"S", "A", "B", "C"}
    assert score["missing_inputs"] == []


def test_composite_score_marks_missing_evidence_instead_of_awarding_health_points():
    from app.services.composite_score import build_composite_score

    snapshot = complete_snapshot()
    snapshot["fund_flow"] = {"status": "missing"}
    snapshot["financial"] = {}

    score = build_composite_score(snapshot)

    assert score["components"]["fund_flow"] == 0
    assert score["components"]["fundamental_valuation"] == 0
    assert score["source_status"]["fund_flow"] == "missing"
    assert score["source_status"]["fundamental_valuation"] == "missing"
    assert set(score["missing_inputs"]) >= {"fund_flow", "financial"}


def test_s_grade_requires_all_hard_gates_even_for_high_raw_score():
    from app.services.composite_score import build_composite_score

    score = build_composite_score(
        complete_snapshot(),
        all_hard_gates_passed=False,
    )

    assert score["total"] >= 85
    assert score["grade"] == "A"


def test_cyclical_low_pe_only_gets_valuation_credit_after_cash_confirmation():
    from app.services.composite_score import build_composite_score

    weak = complete_snapshot()
    weak["financial"] = {
        "status": "ok",
        "earnings_profile": "cyclical",
        "revenue_yoy_pct": 12,
        "gross_margin_pct": 35,
        "pe_ttm": 8,
        "free_cash_flow": -100,
    }
    cash_confirmed = complete_snapshot()
    cash_confirmed["financial"] = {
        **weak["financial"],
        "free_cash_flow": 100,
    }

    weak_score = build_composite_score(weak)
    confirmed_score = build_composite_score(cash_confirmed)

    assert (
        confirmed_score["components"]["fundamental_valuation"]
        > weak_score["components"]["fundamental_valuation"]
    )
    assert "周期" in weak_score["primary_risk"] or any(
        "周期" in reason for reason in weak_score["top_reasons"]
    )


def test_cyclical_cash_confirmation_does_not_award_missing_pe_credit():
    from app.services.composite_score import build_composite_score

    missing_pe = complete_snapshot()
    missing_pe["financial"] = {
        "status": "ok",
        "earnings_profile": "cyclical",
        "free_cash_flow": 100,
        "operating_cashflow_yoy_pct": -40,
    }
    low_pe = complete_snapshot()
    low_pe["financial"] = {
        **missing_pe["financial"],
        "pe_ttm": 15,
    }

    missing_pe_score = build_composite_score(missing_pe)
    low_pe_score = build_composite_score(low_pe)

    assert missing_pe_score["components"]["fundamental_valuation"] == 7.0
    assert low_pe_score["components"]["fundamental_valuation"] == 10.0
