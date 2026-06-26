"""Daily report delivery and Obsidian archive regression tests."""
import json


def test_save_report_to_obsidian_writes_report_index_and_status(tmp_path):
    from scripts.daily_report import save_report_to_obsidian

    result = save_report_to_obsidian(
        "# Daily Report\n\nBody",
        report_date="2026-06-26",
        archive_dir=str(tmp_path),
        title="每日综合策略报告",
        push_status={"feishu_webhook": False, "error": "not configured"},
    )

    report_path = tmp_path / "2026-06-26_每日综合策略报告.md"
    index_path = tmp_path / "日报索引.md"
    status_path = tmp_path / "delivery_status.json"

    assert result["report_path"] == str(report_path)
    assert report_path.exists()
    assert "2026-06-26_每日综合策略报告.md" in index_path.read_text(encoding="utf-8")

    status = json.loads(status_path.read_text(encoding="utf-8"))
    assert status["latest"]["report_date"] == "2026-06-26"
    assert status["latest"]["obsidian_report"] is True
    assert status["latest"]["feishu_webhook"] is False
    assert "not configured" in status["latest"]["error"]


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

    report_path = tmp_path / "2026-06-26_Codex盘中讨论纪要.md"
    assert result["report_path"] == str(report_path)
    content = report_path.read_text(encoding="utf-8")
    assert "Codex盘中讨论纪要" in content
    assert "TCL科技" in content


def test_build_execution_guard_flags_odd_lot_and_cash_limits():
    from scripts.daily_report import build_execution_guard

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
    )

    assert "不新增买入" in guard
    assert "清仓100股" in guard
    assert "卖50股" not in guard


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
