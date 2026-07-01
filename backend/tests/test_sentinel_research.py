"""Sentinel research package tests."""
import json

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

    result = runner.run_news_job("2026-06-28", output_root=tmp_path)

    news_path = tmp_path / "news_events" / "2026-06-28.jsonl"
    assert result["event_count"] == 3
    assert result["news_events"] == str(news_path)
    assert news_path.exists()
    package = json.loads((tmp_path / "research_packages" / "2026-06-28.json").read_text(encoding="utf-8"))
    assert package["serenity_deep_dives"][0]["theme"] == "AI半导体"
    assert "learning_report_path" in package["serenity_deep_dives"][0]
    assert not package["serenity_deep_dives"][0].get("learning_report_markdown")


def test_run_sentinel_review_job_handles_empty_outcomes(tmp_path):
    import scripts.run_sentinel as runner

    result = runner.run_review_job("2026-06-28", output_root=tmp_path)

    assert result["outcome_count"] == 0
    assert (tmp_path / "role_scores" / "2026-06-28.json").exists()
    assert (tmp_path / "reports" / "2026-06-28_sentinel_role_scorecard.md").exists()
