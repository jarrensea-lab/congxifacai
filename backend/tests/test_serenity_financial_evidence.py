"""Tests for Serenity financial evidence integration."""

import json


def _base_scores():
    return {
        "需求确定性": 7,
        "瓶颈强度": 8,
        "传导清晰度": 7,
        "业务纯度": 6,
        "证据强度": 5,
        "市场忽视度": 5,
        "验证速度": 6,
        "下行安全": 5,
    }


def test_tushare_data_source_uses_saved_token_when_env_missing(monkeypatch):
    import app.data_sources.tushare_client as tushare_client

    captured = {}

    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    monkeypatch.setattr(tushare_client.ts, "get_token", lambda: "saved-token")
    monkeypatch.setattr(tushare_client.ts, "set_token", lambda token: (_ for _ in ()).throw(AssertionError("set_token should not write token files")))
    monkeypatch.setattr(tushare_client.ts, "pro_api", lambda token="": captured.setdefault("token", token) or object())

    source = tushare_client.TushareDataSource()

    assert source.is_available() is True
    assert captured["token"] == "saved-token"


def test_financial_evidence_adjusts_scores_and_flags_weak_transmission():
    from app.ai.serenity_financial_evidence import (
        adjust_scores_with_financial_evidence,
        build_financial_evidence,
    )

    snapshot = {
        "code": "300001",
        "name": "测试公司",
        "report_period": "2025Q4",
        "revenue_yoy_pct": 12.0,
        "gross_margin_pct": 21.0,
        "gross_margin_yoy_pct": -3.5,
        "inventory_yoy_pct": 35.0,
        "receivable_yoy_pct": 28.0,
        "operating_cashflow_yoy_pct": -45.0,
        "source": "fake-financials",
        "status": "success",
    }

    evidence = build_financial_evidence({"name": "测试公司", "code": "300001"}, snapshot)
    adjusted = adjust_scores_with_financial_evidence(_base_scores(), evidence)

    assert evidence["strength"] == "strong"
    assert adjusted["red_flag_signals"]["inventory_receivable_growth"] is True
    assert adjusted["red_flag_signals"]["margin_not_improving"] is True
    assert adjusted["scores"]["证据强度"] < _base_scores()["证据强度"]
    assert any("存货" in reason["reason"] for reason in adjusted["reasons"])


def test_pipeline_financial_fetcher_adds_evidence_and_report_section():
    from app.ai.serenity_analyst import build_serenity_research_report, run_serenity_pipeline

    async def fake_financial_fetcher(codes):
        return {
            code: {
                "code": code,
                "name": "测试公司",
                "report_period": "2025Q4",
                "revenue_yoy_pct": 12.0,
                "gross_margin_pct": 21.0,
                "gross_margin_yoy_pct": -3.5,
                "inventory_yoy_pct": 35.0,
                "receivable_yoy_pct": 28.0,
                "operating_cashflow_yoy_pct": -45.0,
                "source": "fake-financials",
                "status": "success",
            }
            for code in codes
        }

    pipeline = run_serenity_pipeline(
        "电网设备",
        available_cash=3085.61,
        total_assets=3085.61,
        financial_fetcher=fake_financial_fetcher,
    )
    report = build_serenity_research_report(pipeline)

    first = pipeline["candidates"][0]
    assert first["financial_evidence"]["source"] == "fake-financials财务证据"
    assert first["financial_adjustments"]
    assert pipeline["financial_status"]["status"] == "success"
    assert "## 财务证据摘要" in report
    assert "存货" in report
    assert "买入" not in report
    assert "卖出" not in report


def test_serenity_report_metadata_targets_siku_data_collection():
    from app.ai.serenity_analyst import build_serenity_research_report, run_serenity_pipeline

    pipeline = run_serenity_pipeline("电网设备", report_date="2026-06-26")
    report = build_serenity_research_report(pipeline)

    assert report.startswith("---\n")
    assert "source: congxifacai-serenity" in report
    assert "collection: 数据采集" in report
    assert "Serenity研究" in report


def test_add_serenity_candidate_script_validates_and_deduplicates(tmp_path):
    from scripts.add_serenity_candidate import main as add_candidate_main

    pool_path = tmp_path / "theme_candidates.json"
    pool_path.write_text(
        json.dumps({"aliases": {}, "candidates": {}}, ensure_ascii=False),
        encoding="utf-8",
    )
    candidate = {
        "name": "测试电气",
        "code": "300001",
        "chokepoint": "测试瓶颈",
        "chain_position": "测试位置",
        "scores": _base_scores(),
        "evidence_items": [
            {"fact": "测试证据待核验", "strength": "medium", "source": "手工维护"}
        ],
        "verify_next": "核验公告、财报和订单。",
    }

    first_exit = add_candidate_main([
        "--pool",
        str(pool_path),
        "--theme",
        "测试产业链",
        "--alias",
        "测试主题",
        "--candidate-json",
        json.dumps(candidate, ensure_ascii=False),
    ])
    second_exit = add_candidate_main([
        "--pool",
        str(pool_path),
        "--theme",
        "测试产业链",
        "--candidate-json",
        json.dumps(candidate, ensure_ascii=False),
    ])

    raw = json.loads(pool_path.read_text(encoding="utf-8"))
    assert first_exit == 0
    assert second_exit == 2
    assert raw["aliases"]["测试主题"] == "测试产业链"
    assert len(raw["candidates"]["测试产业链"]) == 1
