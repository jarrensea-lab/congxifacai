"""Sentinel research package tests."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.ai.sentinel_research import (
    build_serenity_deep_dives,
    build_news_research_package,
    persist_research_package,
    render_research_package_markdown,
    persist_serenity_deep_dive_reports,
)


def _sample_events():
    return [
        {
            "id": "1",
            "source": "tushare",
            "channel": "滚动新闻",
            "published_at": "2026-06-28T09:00:00+08:00",
            "content": "半导体设备订单改善，国产替代主题升温。",
            "is_key": True,
            "symbols": ["688012.SH"],
            "themes": ["AI半导体"],
        },
        {
            "id": "2",
            "source": "tushare",
            "channel": "风险新闻",
            "published_at": "2026-06-28T10:00:00+08:00",
            "content": "机器人板块出现监管问询和减持风险。",
            "is_key": False,
            "symbols": ["300001.SZ"],
            "themes": ["机器人"],
        },
        {
            "id": "3",
            "source": "horizon",
            "channel": "滚动新闻",
            "published_at": "2026-06-28T11:00:00+08:00",
            "content": "半导体产业链继续获得政策支持。",
            "is_key": True,
            "symbols": ["688012.SH"],
            "themes": ["AI半导体"],
        },
    ]


def test_build_news_research_package_counts_themes_symbols_and_risks():
    package = build_news_research_package(_sample_events(), report_date="2026-06-28")

    assert package["date"] == "2026-06-28"
    assert package["event_count"] == 3
    assert package["key_event_count"] == 2
    assert package["top_themes"][0]["name"] == "AI半导体"
    assert package["top_themes"][0]["count"] == 2
    assert package["top_symbols"][0]["name"] == "688012.SH"
    assert package["risk_events"][0]["id"] == "2"
    assert package["source_status"]["status"] == "ok"


def test_render_research_package_markdown_is_research_only():
    package = build_news_research_package(_sample_events(), report_date="2026-06-28")

    markdown = render_research_package_markdown(package)

    assert "# Sentinel 研究包 - 2026-06-28" in markdown
    assert "AI半导体" in markdown
    assert "监管问询" in markdown
    assert "研究输入" in markdown
    for forbidden in ("buy", "sell", "clear", "all_in"):
        assert forbidden not in markdown.lower()


def test_serenity_deep_dives_are_research_only_inputs():
    def fake_pipeline(theme, **kwargs):
        return {
            "theme": theme,
            "normalized_theme": theme,
            "chokepoints": [
                {"sector": "上游材料", "bottleneck": "高纯材料良率", "verify_next": "核验订单"}
            ],
            "top_candidates": [
                {
                    "name": "测试材料",
                    "code": "300001",
                    "score": 78,
                    "chokepoint": "高纯材料良率",
                    "verify_next": "核验财报",
                }
            ],
            "verification_tasks": [{"task": "核验收入传导", "priority": "high"}],
            "quote_status": {"status": "skipped"},
            "financial_status": {"status": "skipped"},
            "account_constraint": "研究输入，不执行交易",
        }

    dives = build_serenity_deep_dives(
        [{"name": "AI半导体", "count": 2}, {"name": "机器人", "count": 1}],
        report_date="2026-06-28",
        limit=1,
        pipeline_runner=fake_pipeline,
    )

    assert dives[0]["module"] == "serenity_bottleneck_deep_dive"
    assert dives[0]["theme"] == "AI半导体"
    assert dives[0]["boundary"] == "research_only"
    assert dives[0]["top_candidates"][0]["name"] == "测试材料"

    package = build_news_research_package(_sample_events(), report_date="2026-06-28")
    package["serenity_deep_dives"] = dives
    markdown = render_research_package_markdown(package)

    assert "## Serenity 产业链瓶颈深挖" in markdown
    assert "AI半导体" in markdown
    assert "高纯材料良率" in markdown
    assert "不生成交易指令" in markdown


def test_serenity_deep_dives_keep_long_fields_and_forward_verification_fetchers():
    quote_fetcher = object()
    financial_fetcher = object()
    captured = {}
    full_candidate = {
        "name": "测试材料",
        "code": "300001",
        "score": 78,
        "chokepoint": "高纯材料良率",
        "chain_position": "上游材料",
        "verify_next": "核验财报",
        "research_priority": "high",
        "long_assumptions": [{"id": "a1", "claim": "需求持续", "status": "intact"}],
        "red_lines": [{"id": "r1", "condition": "替代技术突破", "status": "clear"}],
        "valuation_questions": ["估值是否透支？"],
        "quarterly_verification_tasks": ["复核订单兑现"],
        "bottleneck_duration": "1-3年",
        "bottleneck_map": {"chokepoint": "高纯材料良率", "substitution_risk": "待跟踪"},
        "financial_evidence": {"fact": "财务有效", "metrics": {"revenue_yoy_pct": 20}},
        "quote_evidence": {"fact": "行情有效", "metrics": {"price": 12.3}},
    }

    def fake_pipeline(theme, **kwargs):
        captured.update(kwargs)
        return {
            "theme": theme,
            "normalized_theme": theme,
            "chokepoints": [],
            "top_candidates": [full_candidate],
            "verification_tasks": [],
            "quote_status": {"status": "success"},
            "financial_status": {"status": "success"},
            "account_constraint": "研究输入，不执行交易",
        }

    dives = build_serenity_deep_dives(
        [{"name": "AI半导体", "count": 2}],
        report_date="2026-07-26",
        available_cash=1234.56,
        total_assets=4567.89,
        quote_fetcher=quote_fetcher,
        financial_fetcher=financial_fetcher,
        pipeline_runner=fake_pipeline,
    )

    compact = dives[0]["top_candidates"][0]
    for field in (
        "long_assumptions",
        "red_lines",
        "valuation_questions",
        "quarterly_verification_tasks",
        "bottleneck_duration",
        "bottleneck_map",
        "financial_evidence",
        "quote_evidence",
    ):
        assert compact[field] == full_candidate[field]
    assert compact["boundary"] == "research_only"
    assert dives[0]["boundary"] == "research_only"
    assert captured["available_cash"] == 1234.56
    assert captured["total_assets"] == 4567.89
    assert captured["quote_fetcher"] is quote_fetcher
    assert captured["financial_fetcher"] is financial_fetcher


def test_serenity_deep_dives_skip_empty_themes_and_continue_to_supported_theme():
    def fake_pipeline(theme, **kwargs):
        candidates = []
        if theme == "AI半导体":
            candidates = [{
                "name": "测试芯片",
                "code": "688001",
                "score": 70,
                "chokepoint": "先进制程",
                "verify_next": "核验订单",
            }]
        return {
            "theme": theme,
            "normalized_theme": theme,
            "chokepoints": [],
            "top_candidates": candidates,
            "verification_tasks": [],
            "quote_status": {"status": "skipped"},
            "financial_status": {"status": "skipped"},
            "account_constraint": "研究输入，不执行交易",
        }

    dives = build_serenity_deep_dives(
        [
            {"name": "金融", "count": 100},
            {"name": "AI", "count": 90},
            {"name": "AI半导体", "count": 80},
        ],
        report_date="2026-07-15",
        limit=1,
        pipeline_runner=fake_pipeline,
    )

    assert [item["theme"] for item in dives] == ["AI半导体"]
    assert dives[0]["top_candidates"][0]["code"] == "688001"


@pytest.mark.asyncio
async def test_sentinel_research_job_runs_news_mode(monkeypatch):
    from app import main

    captured = {}

    async def fake_to_thread(func, *args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout="{}", stderr="")

    monkeypatch.setattr(main.asyncio, "to_thread", fake_to_thread)

    status = await main._run_sentinel_research_with_status()

    command = captured["args"][0]
    assert command[command.index("--mode") + 1] == "news"
    assert captured["kwargs"]["timeout"] >= 120
    assert status["state"] == "completed"
    assert status["returncode"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("returncode", "expected_state", "expected_log"),
    [
        (0, "completed", "完成"),
        (2, "degraded", "降级"),
        (1, "failed", "失败"),
    ],
)
async def test_sentinel_scheduler_returns_structured_materialization_status(
    monkeypatch,
    caplog,
    returncode,
    expected_state,
    expected_log,
):
    from app import main

    async def fake_to_thread(func, *args, **kwargs):
        return SimpleNamespace(
            returncode=returncode,
            stdout='{"mode":"news"}',
            stderr="materialization diagnostic",
        )

    monkeypatch.setattr(main.asyncio, "to_thread", fake_to_thread)

    with caplog.at_level("INFO"):
        status = await main._run_sentinel_research_with_status()

    assert status["state"] == expected_state
    assert status["returncode"] == returncode
    assert expected_log in caplog.text
    if returncode != 0:
        assert "Sentinel研究包与Serenity深挖完成" not in caplog.text


def test_sentinel_research_job_is_registered_before_main_report():
    from app.services.scheduler_service import CRON_JOBS

    job_ids = [job.job_id for job in CRON_JOBS]
    research_job = job_ids.index("sentinel_research")
    main_report_job = job_ids.index("main_report")
    assert research_job < main_report_job


def test_project_status_rejects_stale_serenity_deep_dives():
    from scripts.daily_report import _project_status_section, get_strategy_profile

    lines = _project_status_section(
        report_date="2026-07-15",
        target_date="2026-07-16",
        risk_level=3,
        final_view="观望",
        confidence=7,
        positions=[],
        available_cash=2000,
        total_assets=6000,
        market_data={},
        analysis_report={},
        sentinel_package={
            "date": "2026-07-05",
            "event_count": 100,
            "key_event_count": 20,
            "top_themes": [{"name": "AI半导体", "count": 10}],
            "serenity_deep_dives": [{
                "theme": "AI半导体",
                "top_candidates": [{"name": "旧候选", "code": "688001"}],
                "learning_report_path": "/tmp/2026-07-05_Serenity深挖-AI半导体.md",
            }],
            "source_status": {"status": "ok"},
        },
        profile=get_strategy_profile(),
        budget_blocked_count=0,
    )
    content = "\n".join(lines)

    assert "已过期 10 天" in content
    assert "旧深挖不参与当前候选判断" in content
    assert "2026-07-05_Serenity深挖-AI半导体.md" not in content


def test_persist_serenity_deep_dive_reports_keeps_learning_markdown(tmp_path):
    dives = [{
        "module": "serenity_bottleneck_deep_dive",
        "theme": "AI半导体",
        "boundary": "research_only",
        "learning_report_markdown": "# Serenity瓶颈选股报告：AI半导体\n\n学习笔记\n",
    }]

    persisted = persist_serenity_deep_dive_reports(
        dives,
        report_date="2026-06-28",
        archive_dir=tmp_path,
    )

    report_path = tmp_path / "历史数据" / "Serenity深挖" / "2026-06-28" / "2026-06-28_Serenity深挖-AI半导体.md"
    assert persisted[0]["learning_report_path"] == str(report_path)
    assert report_path.exists()
    assert "学习笔记" in report_path.read_text(encoding="utf-8")
    assert persisted[0]["learning_report_markdown"] == ""


def test_persist_research_package_writes_json_and_markdown(tmp_path):
    package = build_news_research_package(_sample_events(), report_date="2026-06-28")

    result = persist_research_package(package, output_root=tmp_path)

    json_path = tmp_path / "research_packages" / "2026-06-28.json"
    md_path = tmp_path / "reports" / "2026-06-28_sentinel_research_package.md"
    assert result["research_package"] == str(json_path)
    assert result["research_report"] == str(md_path)
    assert json.loads(json_path.read_text(encoding="utf-8"))["event_count"] == 3
    assert "Sentinel 研究包" in md_path.read_text(encoding="utf-8")


def test_run_sentinel_news_job_writes_news_events_and_package(monkeypatch, tmp_path):
    import scripts.run_sentinel as runner

    monkeypatch.setattr(runner, "import_default_tushare_news_events", lambda report_date: _sample_events())
    monkeypatch.setattr(
        runner,
        "build_serenity_deep_dives",
        lambda top_themes, **kwargs: [{
            "module": "serenity_bottleneck_deep_dive",
            "theme": "AI半导体",
            "boundary": "research_only",
            "learning_report_markdown": "# report\n",
        }],
    )
    monkeypatch.setattr(runner, "SERENITY_LEARNING_ARCHIVE_DIR", str(tmp_path / "learning"))
    monkeypatch.setattr(
        runner,
        "materialize_serenity_long_horizon",
        lambda package, report_date: {
            "status": "success",
            "thesis_count": 0,
            "evidence_count": 0,
            "target_count": 0,
            "forming_count": 0,
            "verified_count": 0,
            "diagnostics": [],
        },
        raising=False,
    )
    monkeypatch.setenv("CONGXI_PORTFOLIO_PATH", str(tmp_path / "missing-portfolio.json"))

    result = runner.run_news_job("2026-06-28", output_root=tmp_path)

    news_path = tmp_path / "news_events" / "2026-06-28.jsonl"
    assert result["event_count"] == 3
    assert result["news_events"] == str(news_path)
    assert news_path.exists()
    package = json.loads((tmp_path / "research_packages" / "2026-06-28.json").read_text(encoding="utf-8"))
    assert package["serenity_deep_dives"][0]["theme"] == "AI半导体"
    assert "learning_report_path" in package["serenity_deep_dives"][0]
    assert not package["serenity_deep_dives"][0].get("learning_report_markdown")


def test_run_sentinel_news_job_persists_audit_package_after_materialization_failure(
    monkeypatch,
    tmp_path,
):
    import scripts.run_sentinel as runner

    monkeypatch.setattr(
        runner,
        "import_default_tushare_news_events",
        lambda report_date: _sample_events(),
    )
    monkeypatch.setattr(runner, "build_serenity_deep_dives", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        runner,
        "materialize_serenity_long_horizon",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("broken store")),
    )
    monkeypatch.setenv("CONGXI_PORTFOLIO_PATH", str(tmp_path / "missing.json"))

    result = runner.run_news_job(
        "2026-07-27",
        output_root=tmp_path,
        quote_fetcher=lambda codes: {},
        financial_fetcher=lambda codes: {},
    )

    package_path = tmp_path / "research_packages" / "2026-07-27.json"
    assert package_path.exists()
    persisted = json.loads(package_path.read_text(encoding="utf-8"))
    assert persisted["long_horizon_summary"]["status"] == "failed"
    assert result["long_horizon_summary"]["status"] == "failed"
    assert runner._sentinel_result_exit_code(result) == 1


@pytest.mark.parametrize(
    ("summary_status", "expected_code"),
    [
        ("success", 0),
        ("failed", 1),
        ("partial", 2),
        ("degraded", 2),
    ],
)
def test_run_sentinel_main_returns_materialization_exit_code(
    monkeypatch,
    tmp_path,
    summary_status,
    expected_code,
):
    import scripts.run_sentinel as runner

    monkeypatch.setattr(
        runner,
        "run_news_job",
        lambda *args, **kwargs: {
            "mode": "news",
            "long_horizon_summary": {"status": summary_status},
        },
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_sentinel.py",
            "--date",
            "2026-07-27",
            "--mode",
            "news",
            "--output-root",
            str(tmp_path),
        ],
    )

    assert runner.main() == expected_code


def test_run_sentinel_all_recursively_prioritizes_failure():
    import scripts.run_sentinel as runner

    assert (
        runner._sentinel_result_exit_code(
            {
                "news": {
                    "long_horizon_summary": {"status": "partial"},
                },
                "review": {
                    "nested": {
                        "long_horizon_summary": {"status": "failed"},
                    }
                },
            }
        )
        == 1
    )


def test_run_sentinel_news_job_wires_account_fetchers_and_materializes_before_package(
    monkeypatch,
    recwarn,
    tmp_path,
):
    import scripts.run_sentinel as runner

    order = []
    quote_calls = []
    financial_calls = []
    portfolio_path = tmp_path / "portfolio.json"
    portfolio_path.write_text(
        json.dumps({"available_cash": 2100.5, "total_assets": 5980.25}),
        encoding="utf-8",
    )
    monkeypatch.setenv("CONGXI_PORTFOLIO_PATH", str(portfolio_path))
    monkeypatch.setattr(
        runner,
        "import_default_tushare_news_events",
        lambda report_date: _sample_events(),
    )

    async def fake_quote_fetcher(codes):
        quote_calls.append(list(codes))
        return {
            code: {
                "code": code,
                "price": 10.0,
                "amount_wan": 1000.0,
                "mcap_yi": 100.0,
                "pe_ttm": 20.0,
                "pb": 2.0,
                "change_pct": 1.0,
                "source": "fake_async_quote",
            }
            for code in codes
        }

    async def fake_financial_fetcher(codes):
        financial_calls.append(list(codes))
        return {
            code: {
                "code": code,
                "status": "success",
                "report_period": "2026Q1",
                "revenue_yoy_pct": 12.0,
                "gross_margin_pct": 28.0,
                "source": "fake_async_financial",
            }
            for code in codes
        }

    persist_reports = runner.persist_serenity_deep_dive_reports
    persist_package = runner.persist_research_package

    def tracking_persist_reports(dives, **kwargs):
        order.append("persist_reports")
        return persist_reports(dives, **kwargs)

    def fake_materialize(package, report_date):
        order.append("materialize")
        dive = package["serenity_deep_dives"][0]
        candidate = dive["top_candidates"][0]
        assert dive["learning_report_path"]
        assert candidate["quote_evidence"]["metrics"]["price"] == 10.0
        assert candidate["financial_evidence"]["metrics"]["revenue_yoy_pct"] == 12.0
        return {
            "status": "success",
            "thesis_count": 1,
            "evidence_count": 3,
            "target_count": 1,
            "forming_count": 0,
            "verified_count": 1,
            "diagnostics": [],
        }

    def tracking_persist_package(package, **kwargs):
        order.append("persist_package")
        return persist_package(package, **kwargs)

    monkeypatch.setattr(
        runner,
        "persist_serenity_deep_dive_reports",
        tracking_persist_reports,
    )
    monkeypatch.setattr(
        runner,
        "materialize_serenity_long_horizon",
        fake_materialize,
        raising=False,
    )
    monkeypatch.setattr(
        runner,
        "persist_research_package",
        tracking_persist_package,
    )
    monkeypatch.setattr(
        runner,
        "SERENITY_LEARNING_ARCHIVE_DIR",
        str(tmp_path / "learning"),
    )

    result = runner.run_news_job(
        "2026-07-26",
        output_root=tmp_path,
        quote_fetcher=fake_quote_fetcher,
        financial_fetcher=fake_financial_fetcher,
    )

    assert quote_calls and all(codes for codes in quote_calls)
    assert financial_calls and all(codes for codes in financial_calls)
    assert order == ["persist_reports", "materialize", "persist_package"]
    assert result["account_status"]["status"] == "loaded"
    assert result["long_horizon_summary"]["verified_count"] == 1
    assert result["serenity_deep_dive_count"] >= 1
    assert not [
        warning
        for warning in recwarn
        if issubclass(warning.category, RuntimeWarning)
        or "event loop" in str(warning.message).lower()
        or "never awaited" in str(warning.message).lower()
    ]


def test_run_sentinel_review_job_handles_empty_outcomes(tmp_path):
    import scripts.run_sentinel as runner

    result = runner.run_review_job("2026-06-28", output_root=tmp_path)

    assert result["outcome_count"] == 0
    assert (tmp_path / "role_scores" / "2026-06-28.json").exists()
    assert (tmp_path / "reports" / "2026-06-28_sentinel_role_scorecard.md").exists()
