"""Daily report delivery and Obsidian archive regression tests."""
import json


def test_save_report_to_obsidian_writes_report_index_and_status(tmp_path):
    from scripts.daily_report import save_report_to_obsidian

    result = save_report_to_obsidian(
        "# Daily Report\n\nBody",
        report_date="2026-06-26",
        archive_dir=str(tmp_path),
        title="日报",
        push_status={"feishu_webhook": False, "error": "not configured"},
    )

    report_path = tmp_path / "2026" / "06" / "2026-06-26" / "2026-06-26_日报.md"
    index_path = tmp_path / "2026" / "06" / "2026-06-26" / "日报索引.md"
    status_path = tmp_path / "delivery_status.json"

    assert result["report_path"] == str(report_path)
    assert report_path.exists()
    assert "2026-06-26_日报.md" in index_path.read_text(encoding="utf-8")

    status = json.loads(status_path.read_text(encoding="utf-8"))
    assert status["latest"]["report_date"] == "2026-06-26"
    assert status["latest"]["obsidian_report"] is True
    assert status["latest"]["feishu_webhook"] is False
    assert "not configured" in status["latest"]["error"]


def test_save_main_report_uses_next_day_strategy_title(tmp_path):
    from scripts.daily_report import save_report_to_obsidian

    result = save_report_to_obsidian(
        "# 主报告\n",
        report_date="2026-06-28",
        archive_dir=str(tmp_path),
        title="次日投资策略主报告",
    )

    assert result["report_path"].endswith(
        "2026/06/2026-06-28/2026-06-28_次日投资策略主报告.md"
    )


def test_build_next_day_strategy_sections_include_required_blocks():
    from scripts.daily_report import build_next_day_strategy_sections

    sections = "\n".join(build_next_day_strategy_sections(
        report_date="2026-06-28",
        target_date="2026-06-29",
        risk_level=1,
        final_view="空仓观望",
        confidence=9,
        positions=[],
        available_cash=3085.6,
        total_assets=3085.6,
        market_data={"indices": {"shanghai": 4027.26, "sh_change": -2.26}},
        analysis_report={"overall_bias": "bearish"},
        decision={"reasoning": "市场未稳，等待确认。"},
        roles={
            "hunter": {"analysis": "短线弱势"},
            "accountant": {"analysis": "估值未到安全区"},
            "guardian": {"analysis": "小账户先保本金"},
            "researcher": {"analysis": "半导体主题热度高但不可直接交易"},
        },
        sentinel_package={
            "event_count": 5674,
            "key_event_count": 2558,
            "top_themes": [{"name": "AI半导体", "count": 88}],
            "risk_events": [{"excerpt": "监管问询风险"}],
            "serenity_deep_dives": [{
                "theme": "AI半导体",
                "top_candidates": [{"name": "测试材料", "code": "300001", "score": 78}],
                "learning_report_path": "/tmp/2026-06-28_Serenity深挖-AI半导体.md",
            }],
            "source_status": {"status": "ok"},
        },
    ))

    for heading in (
        "## 一、明日总策略",
        "## 二、账户约束",
        "## 三、数据源审计",
        "## 四、市场状态",
        "## 五、Sentinel 研究输入",
        "## 六、四人辩论矩阵",
        "## 七、裁判裁决",
        "## 八、明日执行剧本",
    ):
        assert heading in sections
    assert "2026-06-29" in sections
    assert "AI半导体" in sections
    assert "小账户先保本金" in sections
    assert "Serenity 深挖" in sections
    assert "2026-06-28_Serenity深挖-AI半导体.md" in sections
    assert "AI 原文若出现旧仓位或现金规则" in sections


def test_build_feishu_summary_keeps_full_report_local_hint():
    from scripts.daily_report import build_feishu_summary

    summary = build_feishu_summary("A" * 4000, limit=100)

    assert len(summary) > 100
    assert "完整报告已保存至 Obsidian" in summary
    assert summary.startswith("A" * 50)


def test_save_codex_consultation_uses_report_archive_flow(tmp_path):
    from scripts.save_codex_consultation import save_consultation

    result = save_consultation(
        "今天讨论了大盘风险和TCL科技持仓。",
        report_date="2026-06-26",
        archive_dir=str(tmp_path),
    )

    report_path = tmp_path / "2026" / "06" / "2026-06-26" / "2026-06-26_Codex盘中讨论纪要.md"
    assert result["report_path"] == str(report_path)
    content = report_path.read_text(encoding="utf-8")
    assert "Codex盘中讨论纪要" in content
    assert "TCL科技" in content


def test_build_execution_guard_flags_odd_lot_and_cash_limits():
    from scripts.daily_report import build_execution_guard, get_strategy_profile

    guard = build_execution_guard(
        positions=[{
            "code": "000100",
            "name": "TCL科技",
            "shares": 100,
            "current_price": 5.34,
            "current_value": 534.0,
        }],
        available_cash=1544.89,
        total_assets=2078.89,
        strategy_profile=get_strategy_profile("capital_preservation"),
    )

    assert "不新增买入" in guard
    assert "清仓100股" in guard
    assert "卖50股" not in guard


def test_growth_sprint_profile_uses_confirmed_high_return_limits():
    from scripts.daily_report import (
        get_strategy_profile,
        build_execution_guard,
        build_next_day_strategy_sections,
    )

    profile = get_strategy_profile("growth_sprint")
    assert profile["mode"] == "growth_sprint"
    assert profile["max_drawdown_pct"] == 10
    assert profile["single_position_limit_pct"] == 35
    assert profile["allow_high_volatility"] is True

    guard = build_execution_guard(
        positions=[],
        available_cash=3085.6,
        total_assets=3085.6,
        strategy_profile=profile,
    )

    assert "高收益试验模式" in guard
    assert "账户最大回撤 -10%" in guard
    assert "单票上限 35%" in guard
    assert "现金底线约 ¥308.56" in guard

    sections = "\n".join(build_next_day_strategy_sections(
        report_date="2026-06-29",
        target_date="2026-06-30",
        risk_level=4,
        final_view="小仓试错",
        confidence=7,
        positions=[],
        available_cash=3085.6,
        total_assets=3085.6,
        market_data={"indices": {}},
        analysis_report={"overall_bias": "neutral"},
        decision={"reasoning": "允许短期高波动，但必须硬止损。"},
        roles={"researcher": {"analysis": "题材热度提升"}},
        sentinel_package=None,
        strategy_profile=profile,
    ))

    assert "策略模式：高收益试验模式" in sections
    assert "目标：30天内争取 +10%" in sections
    assert "验收口径：不承诺收益" in sections
    assert "Serenity研究员" in sections


def test_empty_portfolio_action_summary_has_no_stale_holding_action():
    from scripts.daily_report import build_final_action_summary

    summary = build_final_action_summary(
        positions=[],
        available_cash=3085.61,
        total_assets=3085.61,
    )

    assert "当前无持仓" in summary
    assert "TCL科技" not in summary
    assert "清仓" not in summary
    assert "减仓" not in summary


def test_daily_report_archive_keeps_all_report_types_in_trade_day_folder(tmp_path):
    from app.services.report_archive import save_markdown_report

    report_types = ["日报", "盘前策略", "盘中分析", "收盘复盘", "系统状态"]

    for report_type in report_types:
        result = save_markdown_report(
            f"# {report_type}\n",
            report_date="2026-06-29",
            archive_dir=str(tmp_path),
            title=report_type,
        )
        assert result["report_path"].endswith(f"2026/06/2026-06-29/2026-06-29_{report_type}.md")

    day_dir = tmp_path / "2026" / "06" / "2026-06-29"
    index = (day_dir / "日报索引.md").read_text(encoding="utf-8")
    for report_type in report_types:
        assert f"2026-06-29_{report_type}.md" in index


def test_archive_legacy_serenity_reports_moves_root_files_to_history(tmp_path):
    from app.services.report_archive import archive_legacy_serenity_reports

    legacy = tmp_path / "2026-06-26_Serenity瓶颈选股报告-电网设备.md"
    legacy.write_text("# old serenity report\n", encoding="utf-8")
    sentinel = tmp_path / "Sentinel报告"
    sentinel.mkdir()
    (sentinel / "2026-06-28_Sentinel研究报告.md").write_text("# sentinel\n", encoding="utf-8")

    result = archive_legacy_serenity_reports(tmp_path)

    archived_path = tmp_path / "历史数据" / legacy.name
    assert result["moved"] == [str(archived_path)]
    assert archived_path.exists()
    assert not legacy.exists()
    assert (sentinel / "2026-06-28_Sentinel研究报告.md").exists()
