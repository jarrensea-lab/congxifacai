"""Regression tests for runtime issues found during project audit."""
import ast
import json
import os
from pathlib import Path
import subprocess
import sys
from datetime import date, datetime, timedelta

import pytest


def test_database_creates_missing_parent_directory(tmp_path):
    """Runtime SQLite startup should create the parent directory before connecting."""
    db_path = tmp_path / "missing" / "nested" / "stock_data.db"
    env = os.environ.copy()
    env["CONGXI_DATABASE_PATH"] = str(db_path)
    env["PYTHONPATH"] = "backend"

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from app.database import init_db; init_db()",
        ],
        cwd=Path.cwd(),
        env=env,
        text=True,
        capture_output=True,
        timeout=15,
    )

    assert result.returncode == 0, result.stderr
    assert db_path.exists()


def test_daily_report_queries_risk_alerts_by_timestamp():
    """Daily report risk-alert lookup should use RiskAlert.timestamp."""
    from app.database import SessionLocal
    from app.main import _get_today_risk_alerts
    from app.models import RiskAlert

    db = SessionLocal()
    try:
        db.add(
            RiskAlert(
                stock_code="000001",
                stock_name="平安银行",
                alert_type="price_drop",
                alert_level="high",
                alert_message="today",
                timestamp=datetime(2026, 6, 25, 10, 0, 0),
            )
        )
        db.add(
            RiskAlert(
                stock_code="000002",
                stock_name="万科A",
                alert_type="price_drop",
                alert_level="low",
                alert_message="yesterday",
                timestamp=datetime(2026, 6, 24, 15, 0, 0),
            )
        )
        db.commit()

        alerts = _get_today_risk_alerts(db, today=datetime(2026, 6, 25, 12, 0, 0))

        assert [a.stock_code for a in alerts] == ["000001"]
    finally:
        db.query(RiskAlert).delete()
        db.commit()
        db.close()


@pytest.mark.asyncio
async def test_get_review_logs_orders_by_review_date_without_created_at():
    """ReviewLog has review_date, not created_at."""
    from app.database import SessionLocal
    from app.models import ReviewLog
    from app.routers.strategy import get_review_logs

    db = SessionLocal()
    try:
        db.add(ReviewLog(review_date=date.today() - timedelta(days=1), result="yellow", violations=[]))
        db.add(ReviewLog(review_date=date.today(), result="pass", violations=[]))
        db.commit()

        logs = await get_review_logs(days=7)

        assert [item["result"] for item in logs[:2]] == ["pass", "yellow"]
    finally:
        db.query(ReviewLog).delete()
        db.commit()
        db.close()


def test_extract_stop_loss_uses_position_plan_values():
    """Stop-loss extraction should not silently return the default when plan data exists."""
    from app.engine.workshop import _extract_stop_loss

    final = {
        "position_plan": {
            "entries": [
                {"stop_loss": {"pct": "-8"}},
                {"stop_loss": {"pct": "-3.5"}},
            ]
        }
    }

    assert _extract_stop_loss(final) == -8


def test_debate_risk_level_rises_when_data_is_insufficient():
    """Data-scarce or unverified advice should be marked as high risk."""
    from app.engine.workshop import _derive_risk_level

    final = {
        "confidence": 6,
        "reasoning": "数据不足，建议观望；当前结论需要公告和财报交叉验证。",
        "position_plan": {"entries": []},
    }

    assert _derive_risk_level(final, available_cash=3000) >= 4


def test_debate_risk_level_rises_for_small_account_with_entries():
    """Small accounts can still receive research, but actionable entries are high risk."""
    from app.engine.workshop import _derive_risk_level

    final = {
        "confidence": 7,
        "reasoning": "产业链机会明确，但账户规模小。",
        "position_plan": {
            "entries": [
                {"code": "000100", "name": "TCL科技", "weight_pct": 20}
            ]
        },
    }

    assert _derive_risk_level(final, available_cash=3000) >= 4


@pytest.mark.asyncio
async def test_run_debate_passes_total_assets_separately_from_available_cash(monkeypatch):
    """AI roles need account scale, not just cash duplicated as total assets."""
    captured = {}

    class FakeEngine:
        async def debate(self, market_data, holdings_data, news, role_performance=""):
            captured["holdings_data"] = holdings_data
            return {
                "final": {
                    "final_decision": "观望",
                    "confidence": 6,
                    "short_term": {},
                    "mid_low_freq": {},
                    "position_plan": {"entries": []},
                },
                "debate": {},
            }

    import app.ai.debate as debate_module
    from app.engine.workshop import run_debate

    monkeypatch.setattr(debate_module, "AIDebateEngine", FakeEngine)

    await run_debate({
        "available_cash": 1544.89,
        "total_assets": 2078.89,
        "holdings_str": "TCL科技(000100)",
        "holdings": [{"code": "000100", "current_value": 534.0}],
        "news": [],
    })

    assert "总资产: ¥2,078.89" in captured["holdings_data"]
    assert "可用现金: ¥1,544.89" in captured["holdings_data"]


@pytest.mark.asyncio
async def test_run_analysis_and_debate_propagate_growth_sprint_profile(monkeypatch):
    """AI role prompts must receive the active high-return risk profile."""
    from app.engine.analysis import run_analysis
    from app.engine.workshop import run_debate
    import app.engine.analysis as analysis_module
    import app.ai.debate as debate_module

    async def fake_call_model(model_key, prompt):
        return {
            "score": 50,
            "overall_bias": "neutral",
            "plans": [],
            "key_risks": [],
            "market_context": "test",
        }

    captured = {}

    class FakeEngine:
        async def debate(self, market_data, holdings_data, news, role_performance=""):
            captured["holdings_data"] = holdings_data
            return {
                "final": {
                    "final_decision": "观望",
                    "confidence": 6,
                    "short_term": {},
                    "mid_low_freq": {},
                    "position_plan": {"entries": []},
                },
                "debate": {},
            }

    monkeypatch.setattr(analysis_module, "_call_model", fake_call_model)
    monkeypatch.setattr(debate_module, "AIDebateEngine", FakeEngine)

    analysis = await run_analysis({
        "holdings_str": "空仓",
        "available_cash": 3085.6,
        "total_assets": 3085.6,
        "strategy_profile": {
            "title": "高收益试验模式",
            "target": "30天内争取 +10%",
            "max_drawdown_pct": 10,
            "single_position_limit_pct": 35,
            "cash_reserve_pct": 10,
            "stop_loss_pct": 5,
        },
    })
    await run_debate(analysis)

    assert analysis["strategy_profile"]["title"] == "高收益试验模式"
    assert "高收益试验模式" in captured["holdings_data"]
    assert "单票上限: 35%" in captured["holdings_data"]
    assert "现金底线: 10%" in captured["holdings_data"]


def test_repair_final_decision_uses_roles_when_judge_json_invalid():
    """If judge output is unparsable, synthesize a usable conservative decision."""
    from app.engine.workshop import _repair_final_decision

    result = {
        "final": {"raw": "自然语言裁判输出，未按 JSON 返回"},
        "debate": {
            "guardian": {
                "position_advice": "当前仓位过高，建议减仓。",
                "systemic_risks": ["现金安全垫不足"],
            },
            "hunter": {
                "holdings_advice": [{"code": "000100", "name": "TCL科技", "action": "减仓", "reason": "短线超买"}],
                "recommendations": [],
            },
            "accountant": {"recommendations": []},
            "researcher": {"true_bottlenecks": [{"sector": "高端玻璃基板"}]},
        },
    }

    final = _repair_final_decision(result)

    assert final["final_decision"] == "减仓"
    assert final["confidence"] >= 4
    assert "裁判输出未形成可解析JSON" in final["reasoning"]
    assert final["short_term"]["holdings_advice"][0]["code"] == "000100"


def test_portfolio_risk_rises_for_small_account_concentrated_holding():
    from app.engine.workshop import _derive_portfolio_risk

    risk = _derive_portfolio_risk({
        "holdings": [{"code": "000100", "current_value": 534.0}],
        "total_assets": 2078.89,
    })

    assert risk >= 4


def test_run_premarket_defines_pos_plan_before_chart_use():
    """The premarket script should bind pos_plan before chart generation uses it."""
    source = open("scripts/run_premarket.py", encoding="utf-8").read()
    tree = ast.parse(source)

    first_assignment = None
    first_use = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "pos_plan":
                    first_assignment = min(first_assignment or node.lineno, node.lineno)
        if isinstance(node, ast.Name) and node.id == "pos_plan" and isinstance(node.ctx, ast.Load):
            first_use = min(first_use or node.lineno, node.lineno)

    assert first_assignment is not None
    assert first_use is not None
    assert first_assignment < first_use


def test_strategy_report_flags_recommendation_price_mismatch():
    """Premarket reports should not silently trust stale AI-written stock prices."""
    from app.services.report_templates import strategy_report_md

    report = strategy_report_md({
        "final_decision": "谨慎观察",
        "confidence": 6,
        "reasoning": "测试",
        "short_term": {
            "recommendations": [{
                "code": "002475",
                "name": "立讯精密",
                "reason": "现价33.5元，消费电子链条景气。",
                "buy_range": "33.0-34.0",
                "stop_loss": "31.0",
                "target": "38.0",
                "realtime_quote": {
                    "price": 67.62,
                    "last_close": 70.4,
                    "change_pct": -3.95,
                    "source": "tencent",
                },
            }]
        },
        "mid_low_freq": {"recommendations": []},
    })

    assert "实时行情: 67.62元" in report
    assert "腾讯" in report
    assert "AI文本价格疑似过期" in report
    assert "33.50元" in report


@pytest.mark.asyncio
async def test_quote_enrichment_attaches_realtime_quotes_to_recommendations():
    """Rendering inputs should carry live quote snapshots for recommended stocks."""
    from app.services.quote_enrichment import enrich_decision_with_realtime_quotes

    class FakeQuotes:
        async def fetch_batch(self, codes):
            assert codes == ["002475"]
            return {
                "002475": {
                    "code": "002475",
                    "name": "立讯精密",
                    "price": 67.62,
                    "last_close": 70.4,
                    "change_pct": -3.95,
                    "source": "tencent",
                }
            }

    decision = {
        "short_term": {
            "recommendations": [{
                "code": "002475",
                "name": "立讯精密",
                "reason": "现价33.5元。",
            }]
        },
        "mid_low_freq": {"recommendations": []},
    }

    enriched = await enrich_decision_with_realtime_quotes(decision, FakeQuotes())

    rec = enriched["short_term"]["recommendations"][0]
    assert rec["realtime_quote"]["price"] == 67.62
    assert enriched["quote_validation"]["status"] == "success"
    assert enriched["quote_validation"]["codes"] == ["002475"]


def test_premarket_report_engine_card_keeps_realtime_quote_warning():
    """The actual premarket push card should preserve quote mismatch warnings."""
    from app.report_engine.templates.premarket import build_premarket_report_data
    from app.report_engine.renderers.markdown_card import build_premarket_card

    data = build_premarket_report_data(
        date="2026-07-01",
        decision={
            "final_decision": "谨慎观察",
            "confidence": 6,
            "reasoning": "测试",
            "short_term": {
                "recommendations": [{
                    "code": "002475",
                    "name": "立讯精密",
                    "reason": "现价33.5元。",
                    "buy_range": "33-34",
                    "target": "38",
                    "realtime_quote": {
                        "price": 67.62,
                        "change_pct": -3.95,
                        "source": "tencent",
                    },
                }]
            },
            "mid_low_freq": {"recommendations": []},
        },
        positions=[],
        risk_level=3,
    )

    card = build_premarket_card(data)

    assert "实时行情: 67.62元" in card
    assert "AI文本价格疑似过期" in card


def test_daily_report_does_not_depend_on_serenity_candidate_pool():
    """The main daily report should not import the standalone Serenity candidate pool."""
    source = Path("scripts/daily_report.py").read_text(encoding="utf-8")

    assert "serenity_analyst" not in source
    assert "theme_candidates" not in source


def test_default_report_archives_point_to_local_siku_vault():
    """Recovered local workspace should not keep writing reports to the old flash drive."""
    from app.data_sources.horizon_news_importer import DEFAULT_TUSHARE_NEWS_ROOT
    from app.services import report_archive
    import scripts.daily_report as daily_report
    import scripts.run_sentinel as run_sentinel
    import scripts.serenity_research_report as serenity_report

    expected_root = Path.home() / "AI/projects/司库/01-资料采集/量化投资"

    assert daily_report.DEFAULT_ARCHIVE_DIR == str(expected_root / "恭喜发财报告")
    assert serenity_report.DEFAULT_SERENITY_ARCHIVE_DIR == str(expected_root / "Serenity研究")
    assert report_archive.DEFAULT_ARCHIVE_DIR == str(expected_root / "恭喜发财报告")
    assert run_sentinel.SERENITY_LEARNING_ARCHIVE_DIR == str(expected_root / "恭喜发财报告")
    assert DEFAULT_TUSHARE_NEWS_ROOT == expected_root / "Serenity研究/数据采集/tushare-news"

    checked_paths = [
        daily_report.DEFAULT_ARCHIVE_DIR,
        serenity_report.DEFAULT_SERENITY_ARCHIVE_DIR,
        report_archive.DEFAULT_ARCHIVE_DIR,
        run_sentinel.SERENITY_LEARNING_ARCHIVE_DIR,
        str(DEFAULT_TUSHARE_NEWS_ROOT),
    ]
    assert all("/Volumes/Aino Kishi" not in path for path in checked_paths)


def test_serenity_default_archive_is_research_only_without_feishu_or_quotes(tmp_path):
    """Standalone Serenity reports should archive locally by default without Feishu push."""
    from scripts.serenity_research_report import save_serenity_report

    result = save_serenity_report(
        "电网设备",
        report_date="2026-06-26",
        archive_dir=str(tmp_path),
        available_cash=3085.61,
        total_assets=3085.61,
    )
    status = json.loads((tmp_path / "delivery_status.json").read_text(encoding="utf-8"))
    report = Path(result["report_path"]).read_text(encoding="utf-8")

    assert status["latest"]["feishu_webhook"] is None
    assert status["latest"]["error"] == "serenity research archive only"
    assert "行情核验" not in report
    assert "买入" not in report
    assert "卖出" not in report


def test_serenity_manual_run_doc_lists_safe_commands():
    """Operators need explicit no-quotes and with-quotes manual run commands."""
    doc = Path("docs/serenity/manual-run.md")

    assert doc.exists()
    content = doc.read_text(encoding="utf-8")
    assert "scripts/serenity_research_report.py 电网设备" in content
    assert "--with-quotes" in content
    assert "默认不推送飞书" in content
