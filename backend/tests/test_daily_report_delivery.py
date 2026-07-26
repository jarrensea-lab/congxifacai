"""Daily report delivery and Obsidian archive regression tests."""
import json
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest


class _EmptyLongThesisStore:
    def get(self, symbol):
        return None


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
        final_view="对长江电力执行小仓位分批加仓",
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
        "## 一、系统和项目工作状态",
        "## 二、明日持仓策略",
        "## 三、短线关注标的池",
        "## 四、中长线关注标的池",
    ):
        assert heading in sections
    assert "系统结论" in sections
    assert "Sentinel" in sections
    assert "Serenity" in sections
    assert "2026-06-29" in sections
    assert "AI半导体" in sections
    assert "2026-06-28_Serenity深挖-AI半导体.md" in sections
    assert "明日【唯一】实盘狙击标的" not in sections
    assert "核心主攻" not in sections
    assert "复盘与自迭代" in sections
    assert "数据覆盖与评分审计" in sections


def test_first_screen_explicitly_marks_yitaojin_quotes_not_enabled():
    from scripts.daily_report import (
        _project_status_section,
        get_strategy_profile,
    )

    first_screen = "\n".join(
        _project_status_section(
            report_date="2026-07-26",
            target_date="2026-07-27",
            risk_level=2,
            final_view="等待确认",
            confidence=5,
            positions=[],
            available_cash=3000,
            total_assets=3000,
            market_data={"indices": {}},
            analysis_report={"overall_bias": "neutral"},
            sentinel_package=None,
            profile=get_strategy_profile(),
            budget_blocked_count=0,
            visible_decision_gate={
                "entry_allowed": True,
                "state": "allowed",
                "reasons": [],
                "quote_validation": {
                    "enabled": False,
                    "status": "not_enabled",
                    "as_of": None,
                    "validations": {},
                    "reasons": ["quote_validation_not_enabled"],
                },
            },
        )
    )

    assert "易淘金行情校验：未启用" in first_screen
    assert "quote_status=not_enabled" in first_screen
    assert "未声称已完成易淘金核价" in first_screen


def test_build_next_day_strategy_sections_is_concise_enough_for_feishu():
    from scripts.daily_report import build_feishu_summary, build_next_day_strategy_sections

    sections = "\n".join(build_next_day_strategy_sections(
        report_date="2026-07-12",
        target_date="2026-07-13",
        risk_level=4,
        final_view="空仓观望",
        confidence=7,
        positions=[
            {
                "code": "000725",
                "name": "京东方A",
                "shares": 100,
                "avg_cost": 8.011,
                "current_price": 7.59,
                "current_value": 759,
                "quote_source": "tencent",
                "quote_timestamp": "2026-07-12T15:00:00+08:00",
                "quote_trading_date": "2026-07-12",
                "quote_freshness": "fresh",
            },
            {
                "code": "600839",
                "name": "四川长虹",
                "shares": 100,
                "avg_cost": 6.701,
                "current_price": 6.83,
                "current_value": 683,
            },
        ],
        available_cash=1739.31,
        total_assets=5984.31,
        market_data={"indices": {"shanghai": 3500}},
        analysis_report={"overall_bias": "neutral", "limit_up_count": 30, "limit_down_count": 8},
        decision={
            "position_watch": {
                "items": {
                    "000725": {"stop_loss_price": 7.61, "target_price": 8.65},
                    "600839": {"stop_loss_price": 6.37, "target_price": 7.24},
                }
            },
            "target_scores": [
                {
                    "code": "301583",
                    "name": "托伦斯",
                    "action": "watch",
                    "score": 60,
                    "current_price": 22.3,
                    "trigger_price": 22.0,
                    "stop_loss": 20.9,
                    "target_price": 24.6,
                    "decision_reason": "等待量能和资金流确认。",
                },
                {
                    "code": "002123",
                    "name": "长期测试",
                    "action": "watch",
                    "score": 68,
                    "current_price": 3.2,
                    "trigger_price": 3.1,
                    "stop_loss": 2.95,
                    "target_price": 3.7,
                    "long_quality_score": 88,
                    "thesis_status": "healthy",
                    "valuation_zone": "accumulation_zone",
                    "decision_reason": "产业趋势保持，等待交易剧本确认。",
                },
            ],
            "outside_pool_scan": [
                {
                    "code": "000629",
                    "name": "钒钛股份",
                    "current_price": 3.55,
                    "trigger_price": 3.55,
                    "stop_loss": 3.37,
                    "target_price": 3.98,
                    "suggested_amount": 355.0,
                    "affordable": True,
                    "watch_reason": "已具备量能线索，明日若资金流转正且不高开追涨，可一手试错复核。",
                }
            ],
        },
        roles={},
        sentinel_package={
            "event_count": 1200,
            "key_event_count": 80,
            "top_themes": [{"name": "AI端侧", "count": 18}],
            "serenity_deep_dives": [{"theme": "AI端侧", "learning_report_path": "/tmp/serenity.md"}],
            "source_status": {"status": "ok"},
        },
    ))

    summary = build_feishu_summary(sections)

    assert len(summary) <= 3000
    assert "完整报告已保存至 Obsidian" not in summary
    assert "数据覆盖与评分审计" in sections
    assert "数据覆盖与评分审计" not in summary
    for heading in (
        "## 一、系统和项目工作状态",
        "## 二、明日持仓策略",
        "## 三、短线关注标的池",
        "## 四、中长线关注标的池",
    ):
        assert heading in summary
    assert "京东方A(000725)" in summary
    assert "新鲜行情已跌破 ¥7.61，立即卖出100股/退出" in summary
    assert "| 标的 | 状态 | 现价 | 触发价格 | 止损 | 止盈 | 入选原因 | 重点 |" in summary
    assert "等待触发（未触发不买）" in summary
    assert "| 标的 | 现价 | 触发价格 | 止损 | 止盈 | 发展趋势 | 计划持有周期 | 入选原因 |" in summary


def test_build_next_day_strategy_sections_holding_stop_loss_is_explicit():
    from scripts.daily_report import build_next_day_strategy_sections

    sections = "\n".join(build_next_day_strategy_sections(
        report_date="2026-07-12",
        target_date="2026-07-13",
        risk_level=4,
        final_view="空仓观望",
        confidence=7,
        positions=[{
            "code": "000725",
            "name": "京东方A",
            "shares": 100,
            "avg_cost": 8.011,
            "current_price": 7.59,
            "current_value": 759,
            "quote_source": "tencent",
            "quote_timestamp": "2026-07-12T15:00:00+08:00",
            "quote_trading_date": "2026-07-12",
            "quote_freshness": "fresh",
        }],
        available_cash=1739.31,
        total_assets=5984.31,
        market_data={"indices": {}},
        analysis_report={"overall_bias": "neutral"},
        decision={
            "position_watch": {
                "items": {"000725": {"stop_loss_price": 7.61, "target_price": 8.65}}
            },
            "target_scores": [],
        },
        roles={},
        sentinel_package=None,
    ))

    assert "京东方A(000725)" in sections
    assert "立即退出止损" in sections
    assert "新鲜行情已跌破 ¥7.61，立即卖出100股/退出" in sections
    assert "小仓位分批加仓" not in sections
    assert "执行动作以下方" in sections
    assert "数据不足，建议观望" not in sections


def test_holding_without_watch_plan_uses_active_profile_stop_and_target():
    from scripts.daily_report import build_next_day_strategy_sections

    sections = "\n".join(build_next_day_strategy_sections(
        report_date="2026-07-12",
        target_date="2026-07-13",
        risk_level=3,
        final_view="持有观察",
        confidence=7,
        positions=[{
            "code": "000725",
            "name": "京东方A",
            "shares": 100,
            "avg_cost": 8.0,
            "current_price": 7.5,
            "current_value": 750.0,
        }],
        available_cash=1200.0,
        total_assets=2000.0,
        market_data={"indices": {}},
        analysis_report={"overall_bias": "neutral"},
        decision={},
        roles={},
        sentinel_package=None,
    ))

    assert "¥7.20" in sections
    assert "¥9.60" in sections


def test_build_next_day_strategy_sections_promotes_breached_stop_to_first_screen():
    from scripts.daily_report import build_next_day_strategy_sections

    sections = "\n".join(build_next_day_strategy_sections(
        report_date="2026-07-12",
        target_date="2026-07-13",
        risk_level=4,
        final_view="观察科技线反弹",
        confidence=7,
        positions=[{
            "code": "000725",
            "name": "京东方A",
            "shares": 100,
            "avg_cost": 8.011,
            "current_price": 7.59,
            "current_value": 759,
            "quote_source": "tencent",
            "quote_timestamp": "2026-07-12T15:00:00+08:00",
            "quote_trading_date": "2026-07-12",
            "quote_freshness": "fresh",
        }],
        available_cash=1739.31,
        total_assets=5984.31,
        market_data={"indices": {}},
        analysis_report={"overall_bias": "neutral"},
        decision={
            "position_watch": {
                "items": {"000725": {"stop_loss_price": 7.61, "target_price": 8.65}}
            },
            "target_scores": [{
                "code": "000629",
                "name": "钒钛股份",
                "action": "buy",
                "current_price": 3.55,
                "trigger_price": 3.55,
                "stop_loss": 3.37,
                "target_price": 3.98,
                "decision_reason": "低价股放量。",
            }],
        },
        roles={},
        sentinel_package=None,
    ))

    first_screen = sections[:sections.index("## 二、明日持仓策略")]
    assert "开盘前硬风控" in first_screen
    assert "京东方A(000725) 已跌破止损 ¥7.61" in first_screen
    assert "新开仓暂停" in first_screen
    assert "优先处理风险仓" in first_screen
    assert "立即执行减仓/退出" in first_screen
    assert "次日首个15分钟" not in first_screen
    assert "统一入场闸门：阻断" in first_screen
    short_pool = sections[
        sections.index("## 三、短线关注标的池"):sections.index("## 四、中长线关注标的池")
    ]
    assert "钒钛股份(000629)" in short_pool
    assert "等待触发（未触发不买）" in short_pool
    assert "| 可执行 |" not in short_pool


def test_main_report_not_triggered_gate_zeroes_all_entry_rows():
    from scripts.daily_report import build_next_day_strategy_sections

    sections = "\n".join(build_next_day_strategy_sections(
        report_date="2026-07-21",
        target_date="2026-07-22",
        risk_level=3,
        final_view="未触发不买",
        confidence=8,
        positions=[],
        available_cash=3000,
        total_assets=3000,
        market_data={"indices": {}},
        analysis_report={"overall_bias": "neutral"},
        decision={
            "main_report_state": "未触发不买",
            "entry_state": "未触发不买",
            "target_scores": [{
                "code": "000629",
                "name": "铒钛股份",
                "action": "buy",
                "score": 80,
                "entry_price": 3.55,
                "position_amount": 1000,
            }],
            "outside_pool_scan": [{
                "code": "002123",
                "name": "池外候选",
                "action": "actionable",
                "affordable": True,
                "suggested_amount": 900,
                "current_price": 3.0,
            }],
        },
        roles={},
        sentinel_package=None,
    ))

    first_screen = sections[:sections.index("## 二、明日持仓策略")]
    short_pool = sections[
        sections.index("## 三、短线关注标的池"):sections.index("## 四、中长线关注标的池")
    ]
    score_audit = sections[sections.index("### 标的评分"):]
    assert "统一入场闸门：阻断" in first_screen
    assert "主报告入场状态明确为“未触发不买”" in first_screen
    assert "| 可执行 |" not in short_pool
    assert "可人工复核买入" not in score_audit
    assert "可人工复核" not in score_audit


def test_pending_user_confirmed_portfolio_writeback_zeroes_entry_rows():
    from scripts.daily_report import build_next_day_strategy_sections

    sections = "\n".join(build_next_day_strategy_sections(
        report_date="2026-07-21",
        target_date="2026-07-22",
        risk_level=3,
        final_view="小仓试错",
        confidence=8,
        positions=[],
        available_cash=3000,
        total_assets=3000,
        market_data={"indices": {}},
        analysis_report={"overall_bias": "neutral"},
        decision={
            "target_scores": [{
                "code": "000629",
                "name": "铒钛股份",
                "action": "add",
                "score": 80,
                "entry_price": 3.55,
                "suggested_amount": 1000,
            }],
        },
        roles={},
        sentinel_package=None,
        portfolio_truth={
            "pending_user_confirmed_fills": [
                {"fill_id": "fill-user-1", "writeback_status": "unsynced"}
            ]
        },
    ))

    first_screen = sections[:sections.index("## 二、明日持仓策略")]
    short_pool = sections[
        sections.index("## 三、短线关注标的池"):sections.index("## 四、中长线关注标的池")
    ]
    assert "用户确认成交尚未同步持仓真值" in first_screen
    assert "| 可执行 |" not in short_pool


def test_fresh_quote_below_stop_requires_immediate_exit_without_wait():
    from scripts.daily_report import build_next_day_strategy_sections

    sections = "\n".join(build_next_day_strategy_sections(
        report_date="2026-07-15",
        target_date="2026-07-16",
        risk_level=4,
        final_view="先处理风险仓",
        confidence=8,
        positions=[{
            "code": "000725",
            "name": "京东方A",
            "shares": 100,
            "avg_cost": 8.011,
            "current_price": 7.59,
            "current_value": 759,
            "quote_source": "tencent",
            "quote_timestamp": "2026-07-15T14:59:30+08:00",
            "quote_trading_date": "2026-07-15",
            "quote_freshness": "fresh",
        }],
        available_cash=1739.31,
        total_assets=5984.31,
        market_data={"indices": {}},
        analysis_report={"overall_bias": "neutral"},
        decision={
            "position_watch": {
                "items": {"000725": {"stop_loss_price": 7.61, "target_price": 8.65}}
            },
            "target_scores": [],
        },
        roles={},
        sentinel_package=None,
    ))

    first_screen = sections[:sections.index("## 二、明日持仓策略")]
    holding_section = sections[
        sections.index("## 二、明日持仓策略"):sections.index("## 三、短线关注标的池")
    ]
    assert "立即" in first_screen
    assert "退出" in first_screen
    assert "立即" in holding_section
    assert "卖出100股" in holding_section
    assert "15分钟" not in sections


def test_stale_quote_below_stop_renders_named_alarm_not_deterministic_exit():
    from scripts.daily_report import build_next_day_strategy_sections

    sections = "\n".join(build_next_day_strategy_sections(
        report_date="2026-07-15",
        target_date="2026-07-16",
        risk_level=4,
        final_view="先核验行情",
        confidence=6,
        positions=[{
            "code": "000725",
            "name": "京东方A",
            "shares": 100,
            "avg_cost": 8.011,
            "current_price": 7.59,
            "current_value": 759,
            "quote_source": "tencent",
            "quote_timestamp": "2026-07-14T15:00:00+08:00",
            "quote_trading_date": "2026-07-14",
            "quote_freshness": "stale",
        }],
        available_cash=1739.31,
        total_assets=5984.31,
        market_data={"indices": {}},
        analysis_report={"overall_bias": "neutral"},
        decision={
            "position_watch": {
                "items": {"000725": {"stop_loss_price": 7.61, "target_price": 8.65}}
            },
            "target_scores": [],
        },
        roles={},
        sentinel_package=None,
    ))

    assert "行情陈旧" in sections
    assert "必须人工核验止损" in sections
    assert "卖出100股" not in sections
    assert "已跌破止损" not in sections


def test_latest_official_close_for_service_date_triggers_immediate_exit():
    from scripts.daily_report import _apply_position_quote, build_next_day_strategy_sections

    position = {
        "code": "000725",
        "name": "京东方A",
        "shares": 100,
        "avg_cost": 8.011,
        "total_cost": 801.1,
        "current_price": 7.8,
        "current_value": 780.0,
        "pnl": -21.1,
        "pnl_pct": -2.63,
    }
    _apply_position_quote(position, {
        "price": 7.59,
        "source": "tencent",
        "quote_timestamp": "2026-07-17T15:00:00+08:00",
        "trading_date": "2026-07-17",
        "captured_at": "2026-07-19T20:30:00+08:00",
        "freshness": "valid_close",
    }, service_date="2026-07-20")

    sections = "\n".join(build_next_day_strategy_sections(
        report_date="2026-07-19",
        target_date="2026-07-20",
        risk_level=4,
        final_view="先处理风险仓",
        confidence=8,
        positions=[position],
        available_cash=1739.31,
        total_assets=5984.31,
        market_data={"indices": {}},
        analysis_report={"overall_bias": "neutral"},
        decision={
            "position_watch": {
                "items": {"000725": {"stop_loss_price": 7.61, "target_price": 8.65}}
            },
            "target_scores": [],
        },
        roles={},
        sentinel_package=None,
    ))

    assert position["quote_freshness"] == "fresh_close"
    assert position["current_price"] == 7.59
    assert "有效收盘价" in sections
    assert "立即卖出100股/退出" in sections
    assert "15分钟" not in sections


@pytest.mark.parametrize("status", ["suspended", "adjustment_anomaly", "source_conflict"])
def test_apply_position_quote_preserves_upstream_risk_status(status):
    from scripts.daily_report import _apply_position_quote

    position = {
        "shares": 100,
        "total_cost": 800.0,
        "current_price": 8.0,
        "current_value": 800.0,
        "pnl": 0.0,
        "pnl_pct": 0.0,
    }
    _apply_position_quote(position, {
        "price": 7.5,
        "source": "tencent",
        "quote_timestamp": "2026-07-20T14:59:30+08:00",
        "trading_date": "2026-07-20",
        "freshness": "fresh",
        "quote_status": status,
    }, service_date="2026-07-21")

    assert position["quote_status"] == status
    assert position["current_price"] == 8.0
    assert position["current_value"] == 800.0
    assert position["last_quote_price"] == 7.5


def test_stale_quote_keeps_account_values_and_renders_price_as_unverified():
    from scripts.daily_report import _apply_position_quote, build_next_day_strategy_sections

    position = {
        "code": "000725",
        "name": "京东方A",
        "shares": 100,
        "avg_cost": 8.2,
        "total_cost": 820.0,
        "current_price": 8.0,
        "current_value": 800.0,
        "pnl": -20.0,
        "pnl_pct": -2.44,
    }
    _apply_position_quote(position, {
        "price": 7.5,
        "source": "tencent",
        "quote_timestamp": "2026-07-16T15:00:00+08:00",
        "trading_date": "2026-07-16",
        "freshness": "valid_close",
    }, service_date="2026-07-20")

    sections = "\n".join(build_next_day_strategy_sections(
        report_date="2026-07-19",
        target_date="2026-07-20",
        risk_level=4,
        final_view="先核验行情",
        confidence=6,
        positions=[position],
        available_cash=1000.0,
        total_assets=1800.0,
        market_data={"indices": {}},
        analysis_report={"overall_bias": "neutral"},
        decision={
            "position_watch": {
                "items": {"000725": {"stop_loss_price": 7.61, "target_price": 9.0}}
            },
            "target_scores": [],
        },
        roles={},
        sentinel_package=None,
    ))

    assert position["current_price"] == 8.0
    assert position["current_value"] == 800.0
    assert position["pnl"] == -20.0
    assert position["last_quote_price"] == 7.5
    assert "待核验 / ¥8.20" in sections
    assert "¥8.00 / ¥8.20" not in sections


def test_build_next_day_strategy_sections_excludes_current_holdings_from_new_entry_pool():
    from scripts.daily_report import build_next_day_strategy_sections

    sections = "\n".join(build_next_day_strategy_sections(
        report_date="2026-07-12",
        target_date="2026-07-13",
        risk_level=3,
        final_view="等待触发",
        confidence=7,
        positions=[{
            "code": "000725",
            "name": "京东方A",
            "shares": 100,
            "current_price": 7.59,
            "current_value": 759,
        }],
        available_cash=1739.31,
        total_assets=5984.31,
        market_data={"indices": {}},
        analysis_report={"overall_bias": "neutral"},
        decision={
            "position_watch": {
                "items": {"000725": {"stop_loss_price": 7.61, "target_price": 8.65}}
            },
            "target_scores": [
                {
                    "code": "000725",
                    "name": "京东方A",
                    "action": "buy",
                    "current_price": 7.59,
                    "trigger_price": 7.61,
                    "stop_loss": 6.85,
                    "target_price": 8.65,
                    "decision_reason": "候选池残留记录。",
                },
                {
                    "code": "000629",
                    "name": "钒钛股份",
                    "action": "buy",
                    "current_price": 3.55,
                    "trigger_price": 3.55,
                    "stop_loss": 3.2,
                    "target_price": 3.98,
                    "decision_reason": "已通过结构化评分。",
                },
            ],
        },
        roles={},
        sentinel_package=None,
    ))

    short_pool = sections[
        sections.index("## 三、短线关注标的池"):sections.index("## 四、中长线关注标的池")
    ]
    assert "京东方A(000725)" not in short_pool
    assert "钒钛股份(000629)" in short_pool


def test_build_next_day_strategy_sections_uses_profit_first_dashboard_order():
    from scripts.daily_report import build_next_day_strategy_sections, get_strategy_profile

    sections = "\n".join(build_next_day_strategy_sections(
        report_date="2026-07-01",
        target_date="2026-07-02",
        risk_level=4,
        final_view="空仓观望，明日仅做条件触发",
        confidence=7,
        positions=[],
        available_cash=6085.61,
        total_assets=6085.61,
        market_data={"indices": {"shanghai": 4118.89}},
        analysis_report={"overall_bias": "neutral", "limit_up_count": 28, "limit_down_count": 9},
        decision={
            "target_scores": [
                {
                    "code": "002371",
                    "name": "北方华创",
                    "action": "research_only",
                    "score": 55,
                    "current_price": 935.36,
                    "lot_value": 93536,
                    "block_reason": "lot_size_exceeded",
                    "missing_data": ["kline", "fund_flow"],
                    "decision_reason": "一手门槛超过当前账户预算。",
                },
                {
                    "code": "301583",
                    "name": "托伦斯",
                    "action": "watch",
                    "score": 40,
                    "entry_price": 22.6,
                    "lot_value": 2260,
                    "block_reason": "missing_required_data",
                    "missing_data": ["kline", "fund_flow"],
                    "decision_reason": "缺少结构化数据项：kline、fund_flow；先补数据。",
                },
            ],
            "outside_pool_scan": [
                {
                    "code": "000629",
                    "name": "钒钛股份",
                    "source": "small_account_discovery",
                    "current_price": 3.55,
                    "lot_value": 355.0,
                    "max_entry_price": 30.42,
                    "trigger_price": 3.55,
                    "stop_loss": 3.37,
                    "target_price": 3.98,
                    "suggested_amount": 355.0,
                    "watch_reason": "池外小账户补扫；已具备量能线索，明日若资金流转正且不高开追涨，可一手试错复核。",
                }
            ],
            "role_votes": {
                "002371": {
                    "hunter": {"score": 3, "reason": "AI原文价格错配"},
                    "guardian": {"veto": True, "reason": "现价325元高于区间30%"},
                }
            },
        },
        roles={},
        sentinel_package=None,
        strategy_profile=get_strategy_profile("growth_sprint"),
    ))

    system = sections.index("## 一、系统和项目工作状态")
    holdings = sections.index("## 二、明日持仓策略")
    short_pool = sections.index("## 三、短线关注标的池")
    long_pool = sections.index("## 四、中长线关注标的池")
    assert system < holdings < short_pool < long_pool

    first_screen = sections[:short_pool]
    short_screen = sections[short_pool:long_pool]
    assert "钒钛股份(000629)" in short_screen
    assert "已具备量能线索" in short_screen
    assert "¥3.55" in short_screen
    assert "¥3.37" in short_screen
    assert "¥3.98" in short_screen
    assert "池外小账户补扫" not in short_screen

    assert "预算阻断 1 只" in sections
    assert "AI原文价格错配" not in sections
    assert "现价325元高于区间30%" not in sections
    assert "赚钱效应" in first_screen
    assert "赚钱效应：偏低" in first_screen
    assert "¥30.43" not in sections


def test_load_sentinel_research_package_falls_back_to_latest(monkeypatch, tmp_path):
    import scripts.daily_report as daily_report

    root = tmp_path / "sentinel"
    package_dir = root / "research_packages"
    package_dir.mkdir(parents=True)
    old_package = {
        "date": "2026-06-30",
        "event_count": 1705,
        "source_status": {"status": "ok"},
    }
    (package_dir / "2026-06-30.json").write_text(json.dumps(old_package), encoding="utf-8")
    monkeypatch.setattr(daily_report, "SENTINEL_OUTPUT_ROOT", root)

    package = daily_report.load_sentinel_research_package("2026-07-01")

    assert package["date"] == "2026-06-30"
    assert package["fallback_used"] is True
    assert package["requested_date"] == "2026-07-01"


def test_build_next_day_strategy_sections_render_role_votes():
    from scripts.daily_report import build_feishu_summary, build_next_day_strategy_sections

    sections = "\n".join(build_next_day_strategy_sections(
        report_date="2026-07-01",
        target_date="2026-07-02",
        risk_level=4,
        final_view="观察",
        confidence=7,
        positions=[],
        available_cash=6085.61,
        total_assets=6085.61,
        market_data={"indices": {"shanghai": 4118.89}},
        analysis_report={"overall_bias": "neutral"},
        decision={
            "reasoning": "等待触发价。",
            "role_votes": {
                "688008": {
                    "hunter": {"score": 7, "reason": "放量突破"},
                    "accountant": {"score": 5, "reason": "估值中性"},
                    "guardian": {"veto": False, "reason": "未触发风控"},
                    "serenity": {"score": 8, "reason": "产业链瓶颈"},
                    "evidence_ids": ["ev_test"],
                }
            },
        },
        roles={},
        sentinel_package=None,
    ))

    assert "## 一、系统和项目工作状态" in sections
    assert "角色投票和裁判原文只留在 Obsidian" in sections
    summary = build_feishu_summary(sections)
    assert "688008" not in summary
    assert "猎手 7分" not in summary
    assert "Serenity 8分" not in summary
    assert "ev_test" not in summary
    assert "688008" in sections
    assert "猎手 7分" in sections
    assert "Serenity 8分" in sections
    assert "ev_test" in sections
    assert "H7" not in sections
    assert "S8" not in sections
    assert "未触发风控" not in sections
    assert "主报告以结构化评分为准" in sections


def test_build_next_day_strategy_sections_hides_budget_blocked_research_reference_from_feishu():
    from scripts.daily_report import build_next_day_strategy_sections

    sections = "\n".join(build_next_day_strategy_sections(
        report_date="2026-07-01",
        target_date="2026-07-02",
        risk_level=4,
        final_view="现金等待触发",
        confidence=8,
        positions=[],
        available_cash=6085.61,
        total_assets=6085.61,
        market_data={"indices": {"shanghai": 4118.89}},
        analysis_report={"overall_bias": "neutral"},
        decision={
            "target_scores": [
                {
                    "code": "002123",
                    "name": "低价突破",
                    "action": "buy",
                    "score": 78,
                    "entry_price": 3.2,
                    "stop_loss": 3.04,
                    "target_price": 3.58,
                    "position_amount": 1800,
                    "decision_reason": "放量突破且买得起",
                },
                {
                    "code": "688008",
                    "name": "澜起科技",
                    "action": "research_only",
                    "score": 70,
                    "lot_value": 13700,
                    "block_reason": "lot_size_exceeded",
                    "decision_reason": "买不起最小交易单位，仅作半导体锚点",
                },
            ]
        },
        roles={},
        sentinel_package=None,
    ))

    assert "## 三、短线关注标的池" in sections
    assert "低价突破(002123)" in sections
    assert "## 四、中长线关注标的池" in sections
    assert "澜起科技(688008)" not in sections
    assert "预算阻断 1 只" in sections
    assert "数据不足，建议观望" not in sections


def test_build_next_day_strategy_sections_explains_long_research_exists_when_budget_blocked():
    from scripts.daily_report import build_next_day_strategy_sections

    sections = "\n".join(build_next_day_strategy_sections(
        report_date="2026-07-01",
        target_date="2026-07-02",
        risk_level=4,
        final_view="等待触发",
        confidence=7,
        positions=[],
        available_cash=1739.31,
        total_assets=5984.31,
        market_data={"indices": {}},
        analysis_report={"overall_bias": "neutral"},
        decision={
            "target_scores": [
                {
                    "code": "688008",
                    "name": "澜起科技",
                    "action": "research_only",
                    "entry_price": 268.06,
                    "lot_value": 53612,
                    "block_reason": "lot_size_exceeded",
                    "decision_reason": "买不起最小交易单位，仅作半导体研究锚点。",
                }
            ],
        },
        roles={},
        sentinel_package=None,
    ))

    assert "澜起科技(688008)" not in sections
    assert "中长线研究不是没有" in sections
    assert "研究层仍有 1 只预算阻断标的" in sections
    assert "明细在 Obsidian" in sections


def test_build_next_day_strategy_sections_excludes_legacy_report_order():
    from scripts.daily_report import build_feishu_summary, build_next_day_strategy_sections

    sections = "\n".join(build_next_day_strategy_sections(
        report_date="2026-07-01",
        target_date="2026-07-02",
        risk_level=3,
        final_view="等待触发",
        confidence=7,
        positions=[],
        available_cash=6085.61,
        total_assets=6085.61,
        market_data={"indices": {}},
        analysis_report={"overall_bias": "neutral"},
        decision={"target_scores": []},
        roles={},
        sentinel_package=None,
    ))

    assert "## 📈 一、市场概况" not in sections
    assert "## 🧠 三、AI 多维度分析" not in sections
    assert sections.index("## 一、系统和项目工作状态") < sections.index("## 二、明日持仓策略")
    assert sections.index("## 三、短线关注标的池") < sections.index("## 四、中长线关注标的池")
    assert "明日【唯一】实盘狙击标的" not in sections
    summary = build_feishu_summary(sections)
    assert "后台风控与策略审计" in sections
    assert "后台风控与策略审计" not in summary


def test_build_next_day_strategy_sections_renders_outside_pool_scan_when_no_buy():
    from scripts.daily_report import build_next_day_strategy_sections

    sections = "\n".join(build_next_day_strategy_sections(
        report_date="2026-07-01",
        target_date="2026-07-02",
        risk_level=3,
        final_view="等待触发",
        confidence=7,
        positions=[],
        available_cash=6085.61,
        total_assets=6085.61,
        market_data={"indices": {}},
        analysis_report={"overall_bias": "neutral"},
        decision={
            "target_scores": [
                {
                    "code": "688008",
                    "name": "澜起科技",
                    "action": "research_only",
                    "score": 55,
                    "lot_value": 63178,
                    "block_reason": "lot_size_exceeded",
                    "decision_reason": "买不起最小交易单位",
                }
            ],
            "outside_pool_scan": [
                {
                    "code": "000629",
                    "name": "钒钛股份",
                    "source": "small_account_discovery",
                    "current_price": 3.55,
                    "lot_value": 355.0,
                    "max_entry_price": 30.42,
                    "trigger_price": 3.55,
                    "stop_loss": 3.37,
                    "target_price": 3.98,
                    "suggested_amount": 355.0,
                    "watch_reason": "池外小账户补扫；已具备量能线索，明日若资金流转正且不高开追涨，可一手试错复核。",
                }
            ],
        },
        roles={},
        sentinel_package=None,
    ))

    assert "### 池外小账户补扫" not in sections
    assert "钒钛股份(000629)" in sections
    assert "触发价格" in sections
    assert "¥3.37" in sections
    assert "¥3.98" in sections
    assert "small_account_discovery" not in sections


def test_build_next_day_strategy_sections_does_not_try_unaffordable_outside_scan():
    from scripts.daily_report import build_next_day_strategy_sections

    sections = "\n".join(build_next_day_strategy_sections(
        report_date="2026-07-01",
        target_date="2026-07-02",
        risk_level=4,
        final_view="等待回落",
        confidence=6,
        positions=[],
        available_cash=6085.61,
        total_assets=6085.61,
        market_data={"indices": {}},
        analysis_report={"overall_bias": "neutral"},
        decision={
            "target_scores": [],
            "outside_pool_scan": [
                {
                    "code": "300339",
                    "name": "润和软件",
                    "source": "small_account_discovery",
                    "current_price": 39.33,
                    "lot_value": 3933.0,
                    "max_entry_price": 30.42,
                    "trigger_price": 30.42,
                    "affordable": False,
                    "watch_reason": "池外小账户补扫；现价高于账户可买上限价，等回落到¥30.42以内。",
                }
            ],
        },
        roles={},
        sentinel_package=None,
    ))

    assert "不下单" in sections
    assert "润和软件(300339)" not in sections
    assert "预算阻断 1 只" in sections
    assert "一手试错约¥3,933.00" not in sections
    assert "等回落到¥30.42以内" not in sections


def test_build_next_day_strategy_sections_hides_internal_enums_and_translates_missing_data():
    from scripts.daily_report import build_next_day_strategy_sections

    sections = "\n".join(build_next_day_strategy_sections(
        report_date="2026-07-01",
        target_date="2026-07-02",
        risk_level=4,
        final_view="等待触发",
        confidence=7,
        positions=[],
        available_cash=6085.61,
        total_assets=6085.61,
        market_data={"indices": {"shanghai": 4118.89}},
        analysis_report={"overall_bias": "neutral"},
        decision={
            "target_scores": [
                {
                    "code": "002371",
                    "name": "北方华创",
                    "action": "research_only",
                    "score": 55,
                    "lot_value": 93536,
                    "block_reason": "lot_size_exceeded",
                    "missing_data": ["fund_flow"],
                    "decision_reason": "北方华创(002371) 买不起最小交易单位：100股约需¥93,536.00，当前可执行预算约¥3,042.81。",
                },
                {
                    "code": "301583",
                    "name": "托伦斯",
                    "action": "watch",
                    "score": 40,
                    "entry_price": 22.6,
                    "stop_loss": 21.47,
                    "target_price": 25.31,
                    "block_reason": "missing_required_data",
                    "missing_data": ["kline", "fund_flow"],
                    "decision_reason": "缺少结构化数据项：kline、fund_flow；未触发 breakout_entry 或 dip_entry，先补数据，不使用泛化观望兜底。",
                    "next_signal": "补齐kline、fund_flow，并恢复实时价格后再给触发价。",
                },
            ],
            "outside_pool_scan": [
                {
                    "code": "000629",
                    "name": "钒钛股份",
                    "source": "small_account_discovery",
                    "current_price": 3.55,
                    "lot_value": 355.0,
                    "max_entry_price": 30.42,
                    "trigger_price": 3.55,
                    "stop_loss": 3.37,
                    "target_price": 3.98,
                    "suggested_amount": 355.0,
                    "watch_reason": "池外小账户补扫；已具备量能线索，明日若资金流转正且不高开追涨，可一手试错复核。",
                }
            ],
        },
        roles={},
        sentinel_package=None,
    ))

    forbidden = [
        "research_only",
        "missing_required_data",
        "lot_size_exceeded",
        "small_account_discovery",
        "breakout_entry",
        "dip_entry",
        "fund_flow",
        "kline",
    ]
    for token in forbidden:
        assert token not in sections
    assert "个股资金流" in sections
    assert "K线" in sections
    assert "放量突破买点" in sections
    assert "回踩买点" in sections
    assert "预算阻断 1 只" in sections


def test_build_next_day_strategy_sections_renders_mid_frequency_strategy_line():
    from scripts.daily_report import build_next_day_strategy_sections, get_strategy_profile

    sections = "\n".join(build_next_day_strategy_sections(
        report_date="2026-07-01",
        target_date="2026-07-02",
        risk_level=4,
        final_view="等待触发",
        confidence=7,
        positions=[],
        available_cash=6085.61,
        total_assets=6085.61,
        market_data={"indices": {"shanghai": 4118.89}},
        analysis_report={"overall_bias": "neutral"},
        decision={
            "target_scores": [
                {
                    "code": "002371",
                    "name": "北方华创",
                    "action": "research_only",
                    "score": 55,
                    "entry_price": 935.36,
                    "lot_value": 93536,
                    "lot_size": 100,
                    "block_reason": "lot_size_exceeded",
                    "decision_reason": "北方华创(002371) 买不起最小交易单位：100股约需¥93,536.00，当前可执行预算约¥3,042.80。",
                },
                {
                    "code": "301583",
                    "name": "托伦斯",
                    "action": "watch",
                    "score": 40,
                    "entry_price": 22.6,
                    "lot_value": 2260,
                    "block_reason": "missing_required_data",
                    "missing_data": ["kline", "fund_flow"],
                    "decision_reason": "缺少结构化数据项：kline、fund_flow；先补数据。",
                },
            ],
        },
        roles={},
        sentinel_package=None,
        strategy_profile=get_strategy_profile("growth_sprint"),
    ))

    assert "## 四、中长线关注标的池" in sections
    assert "北方华创(002371)" not in sections
    assert "预算阻断 1 只" in sections
    assert "¥935.36" not in sections
    assert "可人工复核买入" not in sections


def test_build_next_day_strategy_sections_renders_long_horizon_summary():
    from scripts.daily_report import build_next_day_strategy_sections

    sections = "\n".join(build_next_day_strategy_sections(
        report_date="2026-07-01",
        target_date="2026-07-02",
        risk_level=3,
        final_view="观察",
        confidence=7,
        positions=[],
        available_cash=6085.61,
        total_assets=6085.61,
        market_data={"indices": {"shanghai": 4118.89}},
        analysis_report={"overall_bias": "neutral"},
        decision={
            "target_scores": [
                {
                    "code": "002123",
                    "name": "长期测试",
                    "action": "watch",
                    "score": 68,
                    "entry_price": 3.2,
                    "lot_value": 320,
                    "block_reason": "price_not_triggered",
                    "decision_reason": "等待交易剧本确认。",
                    "long_quality_score": 88,
                    "thesis_status": "healthy",
                    "valuation_zone": "accumulation_zone",
                    "red_line_status": "clear",
                    "combined_decision_reason": "等待交易剧本确认。长期跟踪：assumptions_intact。",
                },
                {
                    "code": "002456",
                    "name": "红线测试",
                    "action": "watch",
                    "score": 45,
                    "entry_price": 5.0,
                    "lot_value": 500,
                    "block_reason": "long_thesis_broken",
                    "decision_reason": "中长期 thesis 红线触发。",
                    "long_quality_score": 0,
                    "thesis_status": "broken",
                    "valuation_zone": "fair_zone",
                    "red_line_status": "triggered",
                    "combined_decision_reason": "中长期 thesis 红线触发。",
                },
            ],
        },
        roles={},
        sentinel_package=None,
    ))

    assert "## 四、中长线关注标的池" in sections
    assert "长期测试(002123)" in sections
    assert "论文成立" in sections
    assert "积累区" in sections
    assert "红线触发" in sections
    first_screen = sections[:sections.index("## 四、中长线关注标的池")]
    assert "accumulation_zone" not in first_screen


def test_build_next_day_strategy_sections_does_not_render_raw_judge_reasoning_when_scores_exist():
    from scripts.daily_report import build_next_day_strategy_sections

    sections = "\n".join(build_next_day_strategy_sections(
        report_date="2026-07-01",
        target_date="2026-07-02",
        risk_level=4,
        final_view="等待触发",
        confidence=7,
        positions=[],
        available_cash=6085.61,
        total_assets=6085.61,
        market_data={"indices": {"shanghai": 4118.89}},
        analysis_report={"overall_bias": "neutral"},
        decision={
            "reasoning": "AI原文误写：中微公司一手需¥17000，北方华创一手需¥34500。",
            "target_scores": [
                {
                    "code": "002371",
                    "name": "北方华创",
                    "action": "research_only",
                    "score": 55,
                    "lot_value": 93536,
                    "block_reason": "lot_size_exceeded",
                    "decision_reason": "北方华创(002371) 买不起最小交易单位：100股约需¥93,536.00，当前可执行预算约¥3,042.80。",
                }
            ],
        },
        roles={},
        sentinel_package=None,
    ))

    assert "AI原文误写" not in sections
    assert "¥17000" not in sections
    assert "主报告以结构化评分为准" in sections
    assert "预算阻断 1 只" in sections


def test_build_next_day_strategy_sections_does_not_render_raw_role_vote_reasons():
    from scripts.daily_report import build_next_day_strategy_sections

    sections = "\n".join(build_next_day_strategy_sections(
        report_date="2026-07-01",
        target_date="2026-07-02",
        risk_level=4,
        final_view="等待触发",
        confidence=7,
        positions=[],
        available_cash=6085.61,
        total_assets=6085.61,
        market_data={"indices": {"shanghai": 4118.89}},
        analysis_report={"overall_bias": "neutral"},
        decision={
            "role_votes": {
                "002371": {
                    "hunter": {"score": 8},
                    "accountant": {"score": 7},
                    "guardian": {"veto": True, "reason": "AI误写：现价325元，不符合真实行情。"},
                    "serenity": {"score": 8},
                    "evidence_ids": [],
                }
            },
        },
        roles={},
        sentinel_package=None,
    ))

    assert "AI误写" not in sections
    assert "现价325元" not in sections
    assert "主报告以结构化评分为准" in sections


@pytest.mark.asyncio
async def test_build_outside_pool_scan_for_report_adds_live_quote_context(monkeypatch):
    import app.data_sources.tencent_client as tencent_module
    from scripts.daily_report import build_outside_pool_scan_for_report

    class FakeTencent:
        async def fetch_batch(self, codes):
            return {
                "000629": {
                    "code": "000629",
                    "name": "钒钛股份",
                    "price": 22.5,
                    "change_pct": 1.2,
                    "vol_ratio": 1.3,
                    "amount_wan": 8000,
                },
                "000100": {
                    "code": "000100",
                    "name": "TCL科技",
                    "price": 4.8,
                    "change_pct": 3.5,
                    "vol_ratio": 2.4,
                    "amount_wan": 25000,
                },
            }

    monkeypatch.setattr(tencent_module, "TencentDataSource", FakeTencent)

    rows = await build_outside_pool_scan_for_report(
        available_cash=6085.61,
        total_assets=6085.61,
        existing_codes={"688008"},
    )

    tcl = next(item for item in rows if item["code"] == "000100")
    assert rows[0]["code"] == "000100"
    assert tcl["current_price"] == 4.8
    assert tcl["lot_value"] == 480.0
    assert tcl["affordable"] is True
    assert tcl["trigger_price"] == 4.8
    assert tcl["stop_loss"] == 4.32
    assert tcl["target_price"] == 5.76
    assert tcl["suggested_amount"] == 480.0
    assert "量能线索" in tcl["watch_reason"]


@pytest.mark.asyncio
async def test_discover_small_account_candidates_uses_live_rotating_market_rows():
    from scripts import daily_report

    class FakeMarketSource:
        async def fetch_fund_flow_individual(self):
            return [
                {
                    "code": "000563",
                    "name": "陕国投A",
                    "latest_price": "3.02",
                    "change_pct": "5.96%",
                    "turnover": "4.37%",
                    "net": "1.25亿",
                    "amount": "12.3亿",
                }
            ]

    helper = getattr(daily_report, "discover_small_account_candidates_for_report", None)
    assert callable(helper)
    rows = await helper(
        available_cash=2103.25,
        total_assets=5975.25,
        existing_codes={"000100"},
        market_source=FakeMarketSource(),
    )

    assert [row["code"] for row in rows] == ["000563"]
    assert rows[0]["source"] == "dynamic_fund_flow_discovery"
    assert rows[0]["research_only"] is True


@pytest.mark.asyncio
async def test_refreshed_outside_pool_scan_flows_dynamic_candidate_into_quote_gate(monkeypatch):
    import app.data_sources.tencent_client as tencent_module
    from scripts import daily_report

    class FakeMarketSource:
        async def fetch_fund_flow_individual(self):
            return [
                {
                    "code": "000563",
                    "name": "陕国投A",
                    "latest_price": "3.02",
                    "change_pct": "5.96%",
                    "turnover": "4.37%",
                    "net": "1.25亿",
                    "amount": "12.3亿",
                }
            ]

    class FakeTencent:
        async def fetch_batch(self, codes):
            assert codes == ["000563"]
            return {
                "000563": {
                    "code": "000563",
                    "name": "陕国投A",
                    "price": 3.02,
                    "change_pct": 5.96,
                    "vol_ratio": 2.3,
                    "amount_wan": 123000,
                }
            }

    monkeypatch.setattr(tencent_module, "TencentDataSource", FakeTencent)
    helper = getattr(daily_report, "build_refreshed_outside_pool_scan_for_report", None)
    assert callable(helper)

    rows = await helper(
        available_cash=2103.25,
        total_assets=5975.25,
        existing_codes={"000100"},
        market_source=FakeMarketSource(),
    )

    assert [row["code"] for row in rows] == ["000563"]
    assert rows[0]["affordable"] is True
    assert rows[0]["source"] == "dynamic_fund_flow_discovery"
    assert "仅进入研究观察" in rows[0]["watch_reason"]


def test_dynamic_research_candidate_is_visible_but_never_rendered_as_trade_trigger():
    from scripts import daily_report

    decision = {
        "outside_pool_scan": [
            {
                "code": "000563",
                "name": "陕国投A",
                "source": "dynamic_fund_flow_discovery",
                "research_only": True,
                "current_price": 3.02,
                "lot_value": 302.0,
                "suggested_amount": 302.0,
                "affordable": True,
                "trigger_price": 3.02,
                "stop_loss": 2.72,
                "target_price": 3.62,
                "watch_reason": "动态资金流候选；需要完整评分。",
            }
        ]
    }

    section = "\n".join(daily_report._short_pool_section(decision))
    entry_lines = "\n".join(daily_report._new_entry_action_lines(decision))

    assert "研究候选（未晋级，不买）" in section
    assert "待完整评分" in section
    assert "今天不主动买入" in entry_lines
    assert "不下单" in entry_lines
    assert "优先复核 陕国投A" not in entry_lines


def test_research_provenance_score_never_appears_as_short_term_watch():
    from scripts import daily_report

    decision = {
        "target_scores": [
            {
                "code": "002729",
                "name": "好利科技",
                "action": "watch",
                "score": 35,
                "current_price": 13.38,
                "production_eligibility": {
                    "eligible": False,
                    "reason": "research_only_provenance",
                },
            }
        ]
    }

    buckets = daily_report._split_target_scores(decision)
    short_section = "\n".join(daily_report._short_pool_section(decision))

    assert buckets["watching"] == []
    assert [item["code"] for item in buckets["research_reference"]] == ["002729"]
    assert "好利科技" not in short_section
    assert daily_report._effective_target_action(decision["target_scores"][0]) == "research_only"


@pytest.mark.asyncio
async def test_build_outside_pool_scan_reports_cash_reserve_adjusted_budget(monkeypatch):
    import app.data_sources.tencent_client as tencent_module
    from scripts.daily_report import build_outside_pool_scan_for_report

    class FakeTencent:
        async def fetch_batch(self, codes):
            return {
                "000100": {
                    "code": "000100",
                    "name": "TCL科技",
                    "price": 4.8,
                    "change_pct": 0,
                    "vol_ratio": 1,
                    "amount_wan": 20000,
                },
            }

    monkeypatch.setattr(tencent_module, "TencentDataSource", FakeTencent)

    rows = await build_outside_pool_scan_for_report(
        available_cash=1469.57,
        total_assets=6052.57,
    )

    tcl = next(item for item in rows if item["code"] == "000100")
    assert tcl["affordable"] is True
    assert tcl["executable_budget"] == 864.31


@pytest.mark.asyncio
async def test_build_target_scores_prioritizes_actionable_pool_status(monkeypatch):
    from scripts.daily_report import build_target_scores_for_report

    selected_codes = []
    selected_market_sources = []
    selected_trigger_prices = []

    class FakeStore:
        def load(self):
            return {
                "items": {
                    "688008": {"code": "688008", "name": "澜起科技", "status": "research_reference"},
                    "300002": {
                        "code": "300002",
                        "name": "神州泰岳",
                        "status": "watching",
                        "trigger_price": "0",
                        "evidence": {"trigger_price": "4.52"},
                    },
                }
            }

        def upsert_target(self, **kwargs):
            return True

    class FakeSource:
        async def fetch_fund_flow_individual(self):
            return []

        async def fetch_hsgt_flow(self):
            return []

    async def fake_snapshot(code, **kwargs):
        selected_codes.append(code)
        selected_market_sources.append(kwargs.get("market_source"))
        return {"code": code, "name": kwargs["name"], "quote": {"price": 8.0}}

    def fake_score(snapshot, **kwargs):
        selected_trigger_prices.append(snapshot.get("trigger_price"))
        return {"code": snapshot["code"], "name": snapshot["name"], "score": 50, "action": "watch"}

    monkeypatch.setattr("app.services.quant_lifecycle.TargetPoolStore", FakeStore)
    monkeypatch.setattr("app.data_sources.akshare_market.AKShareMarketClient", FakeSource)
    monkeypatch.setattr("app.data_sources.akshare_news.AKShareNewsClient", FakeSource)
    monkeypatch.setattr("app.data_sources.realtime_market_data.FastRealtimeMarketDataSource", FakeSource)
    monkeypatch.setattr("app.services.target_snapshot.build_target_snapshot", fake_snapshot)
    monkeypatch.setattr("app.services.target_scoring.score_target", fake_score)

    shared_market_source = FakeSource()
    await build_target_scores_for_report(
        available_cash=1469.57,
        total_assets=6052.57,
        limit=1,
        market_source=shared_market_source,
        long_thesis_store=_EmptyLongThesisStore(),
    )

    assert selected_codes == ["300002"]
    assert selected_market_sources == [shared_market_source]
    assert selected_trigger_prices == [4.52]


@pytest.mark.asyncio
async def test_build_target_scores_does_not_reactivate_cooldown_after_loss(monkeypatch):
    from scripts.daily_report import build_target_scores_for_report

    selected_codes = []
    selected_trigger_prices = []

    class FakeStore:
        def load(self):
            return {
                "items": {
                    "000725": {"code": "000725", "name": "京东方A", "status": "cooldown_after_loss"},
                    "300002": {
                        "code": "300002",
                        "name": "神州泰岳",
                        "status": "watching",
                        "evidence": "invalid-evidence",
                    },
                }
            }

        def upsert_target(self, **kwargs):
            return True

    class FakeSource:
        async def fetch_fund_flow_individual(self):
            return []

        async def fetch_hsgt_flow(self):
            return []

    async def fake_snapshot(code, **kwargs):
        selected_codes.append(code)
        return {"code": code, "name": kwargs["name"], "quote": {"price": 8.0}}

    def fake_score(snapshot, **kwargs):
        selected_trigger_prices.append(snapshot.get("trigger_price"))
        return {"code": snapshot["code"], "name": snapshot["name"], "score": 50, "action": "watch"}

    monkeypatch.setattr("app.services.quant_lifecycle.TargetPoolStore", FakeStore)
    monkeypatch.setattr("app.data_sources.akshare_market.AKShareMarketClient", FakeSource)
    monkeypatch.setattr("app.data_sources.akshare_news.AKShareNewsClient", FakeSource)
    monkeypatch.setattr("app.data_sources.realtime_market_data.FastRealtimeMarketDataSource", FakeSource)
    monkeypatch.setattr("app.services.target_snapshot.build_target_snapshot", fake_snapshot)
    monkeypatch.setattr("app.services.target_scoring.score_target", fake_score)

    await build_target_scores_for_report(
        available_cash=2103.25,
        total_assets=5975.25,
        market_source=FakeSource(),
        long_thesis_store=_EmptyLongThesisStore(),
    )

    assert selected_codes == ["300002"]
    assert selected_trigger_prices == [None]


@pytest.mark.asyncio
async def test_build_target_scores_refreshes_newest_research_hypothesis_first(monkeypatch):
    from scripts.daily_report import build_target_scores_for_report

    selected_codes = []

    class FakeStore:
        def load(self):
            return {
                "items": {
                    "688008": {
                        "code": "688008",
                        "name": "澜起科技",
                        "status": "research_reference",
                        "updated_at": "2026-07-05 10:00:00",
                    },
                    "000563": {
                        "code": "000563",
                        "name": "陕国投A",
                        "status": "research_reference",
                        "updated_at": "2026-07-15 15:00:00",
                    },
                }
            }

        def upsert_target(self, **kwargs):
            return True

    class FakeSource:
        async def fetch_fund_flow_individual(self):
            return []

        async def fetch_hsgt_flow(self):
            return []

    async def fake_snapshot(code, **kwargs):
        selected_codes.append(code)
        return {"code": code, "name": kwargs["name"], "quote": {"price": 8.0}}

    def fake_score(snapshot, **kwargs):
        return {"code": snapshot["code"], "name": snapshot["name"], "score": 50, "action": "watch"}

    monkeypatch.setattr("app.services.quant_lifecycle.TargetPoolStore", FakeStore)
    monkeypatch.setattr("app.data_sources.akshare_market.AKShareMarketClient", FakeSource)
    monkeypatch.setattr("app.data_sources.akshare_news.AKShareNewsClient", FakeSource)
    monkeypatch.setattr("app.data_sources.realtime_market_data.FastRealtimeMarketDataSource", FakeSource)
    monkeypatch.setattr("app.services.target_snapshot.build_target_snapshot", fake_snapshot)
    monkeypatch.setattr("app.services.target_scoring.score_target", fake_score)

    await build_target_scores_for_report(
        available_cash=2103.25,
        total_assets=5975.25,
        limit=1,
        market_source=FakeSource(),
        long_thesis_store=_EmptyLongThesisStore(),
    )

    assert selected_codes == ["000563"]


@pytest.mark.asyncio
async def test_build_target_scores_keeps_triggered_research_reference_non_executable(monkeypatch):
    from scripts.daily_report import build_target_scores_for_report

    writes = []

    class FakeStore:
        def load(self):
            return {
                "items": {
                    "002123": {
                        "code": "002123",
                        "name": "梦网科技",
                        "status": "research_reference",
                        "source": "sentinel_serenity",
                        "trigger_price": 3.1,
                    }
                }
            }

        def upsert_target(self, **kwargs):
            writes.append(kwargs)
            return True

    class FakeSource:
        async def fetch_fund_flow_individual(self):
            return []

        async def fetch_hsgt_flow(self):
            return []

    async def fake_snapshot(code, **kwargs):
        return {
            "code": code,
            "name": kwargs["name"],
            "quote": {
                "status": "ok",
                "price": 3.2,
                "change_pct": 4.2,
                "amount_wan": 18000,
                "turnover_pct": 8.0,
                "vol_ratio": 2.6,
            },
            "kline": {
                "status": "ok",
                "bars": [
                    {"open": 3.0, "close": 3.1, "high": 3.2, "low": 2.9}
                    for _ in range(20)
                ],
            },
            "fund_flow": {"status": "ok", "net": "净流入"},
            "financial": {"status": "ok", "revenue_yoy_pct": 12.0},
            "news": {"status": "ok", "items": [{"title": "订单增长"}]},
            "sentinel": {"status": "ok", "evidence_ids": ["ev_test"]},
            "serenity": {"status": "ok", "score": 65},
        }

    monkeypatch.setattr("app.services.quant_lifecycle.TargetPoolStore", FakeStore)
    monkeypatch.setattr("app.data_sources.akshare_market.AKShareMarketClient", FakeSource)
    monkeypatch.setattr("app.data_sources.akshare_news.AKShareNewsClient", FakeSource)
    monkeypatch.setattr("app.data_sources.realtime_market_data.FastRealtimeMarketDataSource", FakeSource)
    monkeypatch.setattr("app.services.target_snapshot.build_target_snapshot", fake_snapshot)

    scores = await build_target_scores_for_report(
        available_cash=6085.61,
        total_assets=6085.61,
        limit=1,
        long_thesis_store=_EmptyLongThesisStore(),
    )

    assert scores[0]["score"] >= 70
    assert scores[0]["action"] == "research_only"
    assert writes[0]["status"] == "research_reference"
    assert writes[0]["scoring_decision"]["score"] == scores[0]["score"]
    assert writes[0]["scoring_decision"]["action"] == "research_only"
    assert writes[0]["scoring_decision"]["source_status"]["quote"] == "ok"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("current_status", "expected_status"),
    [
        ("long_research", "long_watch"),
        ("long_watch", "long_watch"),
        ("accumulation_zone", "accumulation_zone"),
    ],
)
async def test_build_target_scores_consumes_injected_long_thesis_and_preserves_long_state(
    monkeypatch,
    tmp_path,
    current_status,
    expected_status,
):
    from app.services.long_thesis import LongThesisStore
    from scripts.daily_report import build_target_scores_for_report

    writes = []
    thesis_store = LongThesisStore(tmp_path / "long_thesis.json")
    thesis_store.upsert(
        {
            "symbol": "002123",
            "name": "梦网科技",
            "quality_score": 88,
            "thesis_status": "healthy",
            "verification_status": "verified",
            "current_long_evidence_ids": [
                "long-thesis:002123:v2",
                "long-red-line:002123:v2",
            ],
            "valuation_anchor": {
                "accumulation_price": 3.3,
                "fair_price": 4.0,
                "overpriced_price": 5.2,
            },
            "assumptions": [{"id": "growth", "status": "intact"}],
            "red_lines": [{"id": "margin", "status": "clear"}],
            "financial_evidence": {"raw_blob": "not-for-scorecard" * 100},
        }
    )

    class FakeStore:
        def load(self):
            return {
                "items": {
                    "002123": {
                        "code": "002123",
                        "name": "梦网科技",
                        "status": current_status,
                        "source": "long_horizon",
                        "current_long_evidence_ids": ["long-thesis:002123:v1"],
                    }
                }
            }

        def upsert_target(self, **kwargs):
            writes.append(kwargs)
            return True

    class FakeSource:
        async def fetch_fund_flow_individual(self):
            return []

        async def fetch_hsgt_flow(self):
            return []

    async def fake_snapshot(code, **kwargs):
        return {
            "code": code,
            "name": kwargs["name"],
            "quote": {
                "status": "ok",
                "price": 3.2,
                "change_pct": 1.2,
                "amount_wan": 8200,
                "turnover_pct": 3.0,
                "vol_ratio": 1.1,
            },
            "kline": {
                "status": "ok",
                "bars": [
                    {"open": 3.1, "close": 3.15, "high": 3.2, "low": 3.0}
                    for _ in range(20)
                ],
            },
            "fund_flow": {"status": "ok", "net": "净流入"},
            "financial": {"status": "ok", "revenue_yoy_pct": 12.0},
            "news": {"status": "ok", "items": [{"title": "订单增长"}]},
            "sentinel": {"status": "ok", "evidence_ids": ["ev_test"]},
            "serenity": {"status": "ok", "score": 65},
        }

    monkeypatch.setattr("app.services.quant_lifecycle.TargetPoolStore", FakeStore)
    monkeypatch.setattr(
        "app.data_sources.realtime_market_data.FastRealtimeMarketDataSource",
        FakeSource,
    )
    monkeypatch.setattr("app.data_sources.akshare_news.AKShareNewsClient", FakeSource)
    monkeypatch.setattr(
        "app.services.target_snapshot.build_target_snapshot",
        fake_snapshot,
    )

    scores = await build_target_scores_for_report(
        available_cash=6085.61,
        total_assets=6085.61,
        limit=1,
        market_source=FakeSource(),
        long_thesis_store=thesis_store,
    )

    assert scores[0]["action"] == "watch"
    assert scores[0]["block_reason"] == "price_not_triggered"
    assert scores[0]["long_quality_score"] == 88
    assert scores[0]["thesis_status"] == "healthy"
    assert scores[0]["valuation_zone"] == "accumulation_zone"
    assert scores[0]["red_line_status"] == "clear"
    assert scores[0]["long_horizon_reason"] == "assumptions_intact"
    assert "长期跟踪" in scores[0]["combined_decision_reason"]
    assert scores[0]["current_long_evidence_ids"] == [
        "long-thesis:002123:v2",
        "long-red-line:002123:v2",
    ]
    assert writes[0]["status"] == expected_status
    assert writes[0]["current_long_evidence_ids"] == scores[0][
        "current_long_evidence_ids"
    ]
    decision = writes[0]["scoring_decision"]
    for field in (
        "long_quality_score",
        "thesis_status",
        "valuation_zone",
        "red_line_status",
        "long_horizon_reason",
        "combined_decision_reason",
        "current_long_evidence_ids",
    ):
        assert decision[field] == scores[0][field]
    assert "financial_evidence" not in decision


@pytest.mark.asyncio
async def test_build_target_scores_default_long_thesis_store_uses_env_path(
    monkeypatch,
    tmp_path,
):
    from app.services.long_thesis import LongThesisStore
    from scripts.daily_report import build_target_scores_for_report

    thesis_path = tmp_path / "env-long-thesis.json"
    monkeypatch.setenv("CONGXI_LONG_THESIS_PATH", str(thesis_path))
    LongThesisStore().upsert(
        {
            "symbol": "000001",
            "quality_score": 77,
            "thesis_status": "healthy",
            "assumptions": [{"id": "deposit", "status": "intact"}],
            "red_lines": [],
        }
    )
    received_theses = []

    class FakeStore:
        def load(self):
            return {
                "items": {
                    "000001": {
                        "code": "000001",
                        "name": "平安银行",
                        "status": "long_research",
                        "source": "long_horizon",
                    }
                }
            }

        def upsert_target(self, **kwargs):
            return True

    class FakeSource:
        async def fetch_fund_flow_individual(self):
            return []

        async def fetch_hsgt_flow(self):
            return []

    async def fake_snapshot(code, **kwargs):
        return {"code": code, "name": kwargs["name"], "quote": {"price": 10.0}}

    def fake_score(snapshot, **kwargs):
        received_theses.append(kwargs.get("long_thesis"))
        return {
            "code": snapshot["code"],
            "name": snapshot["name"],
            "score": 50,
            "action": "watch",
            "long_quality_score": 77,
            "thesis_status": "healthy",
        }

    monkeypatch.setattr("app.services.quant_lifecycle.TargetPoolStore", FakeStore)
    monkeypatch.setattr(
        "app.data_sources.realtime_market_data.FastRealtimeMarketDataSource",
        FakeSource,
    )
    monkeypatch.setattr("app.data_sources.akshare_news.AKShareNewsClient", FakeSource)
    monkeypatch.setattr(
        "app.services.target_snapshot.build_target_snapshot",
        fake_snapshot,
    )
    monkeypatch.setattr("app.services.target_scoring.score_target", fake_score)

    await build_target_scores_for_report(
        available_cash=6085.61,
        total_assets=6085.61,
        limit=1,
        market_source=FakeSource(),
    )

    assert received_theses[0]["symbol"] == "000001"
    assert received_theses[0]["quality_score"] == 77


def test_build_feishu_summary_keeps_full_report_local_hint():
    from scripts.daily_report import build_feishu_summary

    summary = build_feishu_summary("A" * 4000, limit=100)

    assert len(summary) > 100
    assert "完整报告已保存至 Obsidian" in summary
    assert summary.startswith("A" * 50)


@pytest.mark.asyncio
async def test_push_daily_report_to_feishu_uses_unified_channel(monkeypatch):
    from scripts import daily_report

    async def fake_send(**kwargs):
        assert kwargs["title"] == "次日策略"
        assert "正文" in kwargs["content"]
        return {"feishu_api": True, "feishu_webhook": False, "channel": "api", "error": ""}

    monkeypatch.setattr("app.services.feishu_pusher.send_feishu_card", fake_send)

    result = await daily_report.push_daily_report_to_feishu("次日策略", "正文")

    assert result["feishu_api"] is True
    assert result["channel"] == "api"


@pytest.mark.asyncio
async def test_push_daily_report_to_feishu_can_be_disabled_for_local_reconciliation(monkeypatch):
    from scripts import daily_report

    monkeypatch.setenv("CONGXI_DISABLE_FEISHU_PUSH", "1")

    async def forbidden_send(**kwargs):
        raise AssertionError("disabled reconciliation run must not send Feishu messages")

    monkeypatch.setattr("app.services.feishu_pusher.send_feishu_card", forbidden_send)
    result = await daily_report.push_daily_report_to_feishu("次日策略", "正文")

    assert result["feishu_api"] is False
    assert result["feishu_webhook"] is False
    assert result["disabled"] is True


def test_persist_outside_pool_scan_promotes_affordable_watch_names(tmp_path):
    from app.services.quant_lifecycle import TargetPoolStore
    from scripts.daily_report import persist_outside_pool_scan_to_target_pool

    store = TargetPoolStore(tmp_path / "candidate_pool.json")
    rows = [
        {
            "code": "000725",
            "name": "京东方A",
            "source": "small_account_discovery",
            "theme": "面板/低价大成交",
            "current_price": 8.58,
            "trigger_price": 8.58,
            "stop_loss": 8.15,
            "target_price": 9.61,
            "affordable": True,
            "chasing_risk": False,
            "watch_reason": "池外小账户补扫；等待资金流转正。",
        },
        {
            "code": "688008",
            "name": "澜起科技",
            "current_price": 68.5,
            "affordable": False,
            "chasing_risk": False,
        },
    ]

    promoted = persist_outside_pool_scan_to_target_pool(
        rows,
        available_cash=6085.61,
        total_assets=6085.61,
        store=store,
    )

    assert promoted == 1
    item = store.get("000725")
    assert item["status"] == "watching"
    assert item["source"] == "small_account_discovery"
    assert item["evidence"]["trigger_price"] == 8.58
    assert store.get("688008") is None


def test_persist_dynamic_discovery_keeps_new_name_research_only(tmp_path):
    from app.services.quant_lifecycle import TargetPoolStore
    from scripts.daily_report import persist_outside_pool_scan_to_target_pool

    store = TargetPoolStore(tmp_path / "candidate_pool.json")
    promoted = persist_outside_pool_scan_to_target_pool(
        [
            {
                "code": "000563",
                "name": "陕国投A",
                "source": "dynamic_fund_flow_discovery",
                "research_only": True,
                "theme": "动态资金流/量价候选",
                "current_price": 3.02,
                "trigger_price": 3.02,
                "stop_loss": 2.72,
                "target_price": 3.62,
                "affordable": True,
                "chasing_risk": False,
                "watch_reason": "动态资金流候选；需要完整评分。",
                "market_evidence": {"net_flow_yuan": 125_000_000},
            }
        ],
        available_cash=2103.25,
        total_assets=5975.25,
        store=store,
    )

    assert promoted == 1
    item = store.get("000563")
    assert item["status"] == "research_reference"
    assert item["production_eligibility"]["eligible"] is False
    assert item["provenance"]["research_only"] is True
    assert item["evidence"]["market_evidence"]["net_flow_yuan"] == 125_000_000


def test_rotate_dynamic_discovery_expires_names_missing_from_latest_scan(tmp_path):
    from app.services.quant_lifecycle import TargetPoolStore
    from scripts import daily_report

    store = TargetPoolStore(tmp_path / "candidate_pool.json")
    store.upsert_target(
        code="000563",
        name="陕国投A",
        status="research_reference",
        source="dynamic_fund_flow_discovery",
        evidence={"stage": "hypothesis"},
        current_price=3.02,
        available_cash=2103.25,
        total_assets=5975.25,
    )
    store.upsert_target(
        code="000597",
        name="东北制药",
        status="research_reference",
        source="dynamic_fund_flow_discovery",
        evidence={"stage": "hypothesis"},
        current_price=4.80,
        available_cash=2103.25,
        total_assets=5975.25,
    )
    store.upsert_target(code="000100", name="TCL科技", status="watching", source="manual")

    helper = getattr(daily_report, "rotate_dynamic_discovery_targets", None)
    assert callable(helper)
    expired = helper({"000597"}, store=store)

    assert expired == 1
    assert store.get("000563")["status"] == "expired"
    assert store.get("000597")["status"] == "research_reference"
    assert store.get("000100")["status"] == "watching"


def test_outside_pool_exclusions_include_removed_target_pool_items(tmp_path):
    from app.services.quant_lifecycle import TargetPoolStore
    from scripts import daily_report

    store = TargetPoolStore(tmp_path / "candidate_pool.json")
    store.upsert_target(code="600839", name="四川长虹", status="removed")
    store.upsert_target(
        code="000563",
        name="陕国投A",
        status="research_reference",
        source="dynamic_fund_flow_discovery",
        evidence={"stage": "hypothesis"},
    )

    helper = getattr(daily_report, "collect_outside_pool_exclusions", None)
    assert callable(helper)
    assert helper([{"code": "300002"}], store=store) == {"300002", "600839"}


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


def test_portfolio_database_sync_exception_returns_structured_failure_without_error_dump():
    from scripts.daily_report import sync_portfolio_database_truth

    class FakeDb:
        closed = False

        def close(self):
            self.closed = True

    db = FakeDb()

    def fail_sync(session, portfolio_path):
        raise RuntimeError("authorization=SECRET-DO-NOT-REPORT")

    portfolio = {"positions": [], "available_cash": 1000}
    result = sync_portfolio_database_truth(
        portfolio,
        "/tmp/test-portfolio.json",
        session_factory=lambda: db,
        sync_fn=fail_sync,
    )

    assert result["ok"] is False
    assert result["status"] == "failed"
    assert portfolio["portfolio_sync_failed"] is True
    assert portfolio["portfolio_sync_status"] == "failed"
    assert "SECRET" not in json.dumps(result, ensure_ascii=False)
    assert "SECRET" not in json.dumps(portfolio, ensure_ascii=False)
    assert db.closed is True


def test_data_source_audit_marks_sqlite_degraded_from_structured_sync_truth():
    from scripts.daily_report import build_data_source_audit

    audit = "\n".join(
        build_data_source_audit(
            market_data={
                "indices": {"shanghai": 4000},
                "portfolio_sync_failed": True,
                "portfolio_sync_status": "failed",
            },
            sentinel_package=None,
        )
    )

    assert "| SQLite | degraded |" in audit
    assert "SECRET" not in audit


def test_data_source_audit_requires_structured_market_success_with_indices():
    from scripts.daily_report import build_data_source_audit

    fresh_cutoff = datetime.now().astimezone().isoformat()
    failed_audit = "\n".join(
        build_data_source_audit(
            market_data={
                "indices": {"shanghai": 4000},
                "market_source_status": {
                    "status": "failed",
                    "provider": "data_router+tencent",
                    "data_cutoff": None,
                    "error": "all_realtime_index_sources_failed",
                },
            },
            sentinel_package=None,
        )
    )
    ok_audit = "\n".join(
        build_data_source_audit(
            market_data={
                "indices": {
                    "shanghai": 4000,
                    "shenzhen": 12000,
                    "cyb": 2600,
                },
                "market_source_status": {
                    "status": "ok",
                    "provider": "tencent",
                    "data_cutoff": fresh_cutoff,
                    "freshness": "fresh",
                    "error": "",
                    "coverage": {"expected": 3, "verified": 3},
                    "missing_sources": [],
                    "rejected_sources": [],
                },
            },
            sentinel_package=None,
        )
    )
    empty_audit = "\n".join(
        build_data_source_audit(
            market_data={
                "indices": {},
                "market_source_status": {
                    "status": "ok",
                    "provider": "tencent",
                    "data_cutoff": fresh_cutoff,
                    "freshness_status": "fresh",
                    "error": "",
                },
            },
            sentinel_package=None,
        )
    )

    assert "| 行情数据 | degraded |" in failed_audit
    assert "all_realtime_index_sources_failed" in failed_audit
    assert "| 行情数据 | ok |" in ok_audit
    assert "tencent" in ok_audit
    assert fresh_cutoff in ok_audit
    assert "| 行情数据 | degraded |" in empty_audit


@pytest.mark.parametrize(
    "status_patch",
    [
        {},
        {"coverage": {"expected": 3, "verified": 2}},
        {
            "coverage": {"expected": 3, "verified": 3},
            "missing_sources": ["sz399006"],
        },
        {
            "coverage": {"expected": 3, "verified": 3},
            "rejected_sources": ["sz399006"],
        },
    ],
)
def test_data_source_audit_rejects_incomplete_market_coverage(status_patch):
    from scripts.daily_report import build_data_source_audit

    status = {
        "status": "ok",
        "provider": "tencent",
        "data_cutoff": datetime.now().astimezone().isoformat(),
        "freshness_status": "fresh",
        "error": "",
        **status_patch,
    }
    audit = "\n".join(
        build_data_source_audit(
            market_data={
                "indices": {
                    "shanghai": 4000,
                    "shenzhen": 12000,
                    "cyb": 2600,
                },
                "market_source_status": status,
            },
            sentinel_package=None,
        )
    )

    assert "| 行情数据 | degraded |" in audit


def test_data_source_audit_rejects_naive_market_cutoff():
    from scripts.daily_report import build_data_source_audit

    audit = "\n".join(
        build_data_source_audit(
            market_data={
                "indices": {
                    "shanghai": 4000,
                    "shenzhen": 12000,
                    "cyb": 2600,
                },
                "market_source_status": {
                    "status": "ok",
                    "provider": "tencent",
                    "data_cutoff": datetime.now().replace(tzinfo=None).isoformat(),
                    "freshness_status": "fresh",
                    "error": "",
                    "coverage": {"expected": 3, "verified": 3},
                    "missing_sources": [],
                    "rejected_sources": [],
                },
            },
            sentinel_package=None,
        )
    )

    assert "| 行情数据 | degraded |" in audit


@pytest.mark.parametrize(
    "market_source_status",
    [
        {
            "status": "ok",
            "provider": "tencent",
            "data_cutoff": None,
            "freshness_status": "fresh",
            "error": "",
        },
        {
            "status": "ok",
            "provider": "tencent",
            "data_cutoff": "2000-01-01T00:00:00+08:00",
            "freshness_status": "fresh",
            "error": "",
        },
        {
            "status": "ok",
            "provider": "tencent",
            "data_cutoff": "FRESH_CUTOFF",
            "error": "",
        },
    ],
)
def test_data_source_audit_rejects_missing_stale_or_unproven_market_truth(
    market_source_status,
):
    from scripts.daily_report import build_data_source_audit

    status = dict(market_source_status)
    if status.get("data_cutoff") == "FRESH_CUTOFF":
        status["data_cutoff"] = datetime.now().astimezone().isoformat()
    audit = "\n".join(
        build_data_source_audit(
            market_data={
                "indices": {"shanghai": 4000},
                "market_source_status": status,
            },
            sentinel_package=None,
        )
    )

    assert "| 行情数据 | degraded |" in audit


@pytest.mark.parametrize("freshness_status", ["stale", "conflict", "unknown", "failed"])
def test_data_source_audit_rejects_nonfresh_market_status(freshness_status):
    from scripts.daily_report import build_data_source_audit

    audit = "\n".join(
        build_data_source_audit(
            market_data={
                "indices": {"shanghai": 4000},
                "market_source_status": {
                    "status": "ok",
                    "provider": "tencent",
                    "data_cutoff": datetime.now().astimezone().isoformat(),
                    "freshness_status": freshness_status,
                    "error": "",
                },
            },
            sentinel_package=None,
        )
    )

    assert "| 行情数据 | degraded |" in audit


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "partial_kind",
    [
        "none",
        "exception",
        "empty",
        "price_zero",
        "price_nan",
        "price_inf",
        "price_negative_inf",
    ],
)
async def test_daily_report_main_sync_exception_fails_closed_end_to_end(
    tmp_path,
    monkeypatch,
    capsys,
    partial_kind,
):
    import app.data_sources.akshare_market as akshare_module
    import app.data_sources.realtime_market_data as realtime_module
    import app.engine.analysis as analysis_module
    import app.engine.workshop as workshop_module
    import app.services.evidence_ledger as evidence_module
    import app.services.portfolio_store as portfolio_store_module
    import app.services.quant_lifecycle as lifecycle_module
    import scripts.daily_report as daily_report
    from app.services.visible_decision_gate import apply_visible_decision_gate

    portfolio_path = tmp_path / "portfolio.json"
    gate_path = tmp_path / "visible-gate.json"
    archive_dir = tmp_path / "reports"
    portfolio_path.write_text(
        json.dumps(
            {
                "positions": [],
                "closed_positions": [],
                "available_cash": 3000,
                "cash": 3000,
                "realized_pnl": 0,
            }
        ),
        encoding="utf-8",
    )
    secret = "authorization=SECRET-MAIN-PROBE"
    captured = {}

    class FakeDb:
        closed = False

        def close(self):
            self.closed = True

    db = FakeDb()

    class FakeRealtimeSource:
        async def fetch_batch(self, codes):
            fresh_cutoff = datetime.now().astimezone().isoformat()
            quotes = {
                code: {
                    "price": 4000 if code == "sh000001" else 12000,
                    "change_pct": 0.1,
                    "source": "tencent",
                    "quote_timestamp": fresh_cutoff,
                    "freshness": "fresh",
                }
                for code in codes
            }
            if partial_kind == "none":
                quotes["sz399001"] = None
            elif partial_kind == "exception":
                quotes["sz399001"] = RuntimeError("index source failed")
            elif partial_kind == "empty":
                quotes["sz399001"] = {}
            else:
                invalid_prices = {
                    "price_zero": 0,
                    "price_nan": float("nan"),
                    "price_inf": float("inf"),
                    "price_negative_inf": float("-inf"),
                }
                quotes["sz399001"]["price"] = invalid_prices[partial_kind]
            return quotes

    class FakePositionWatchStore:
        def load(self):
            return {}

    async def fake_analysis(market_data):
        captured["market_data"] = dict(market_data)
        return {"overall_bias": "neutral"}

    async def fake_debate(report):
        return {
            "decision": {"final_view": "小仓试错", "confidence": 7},
            "roles": {},
            "recommended_risk_level": 3,
        }

    async def fake_target_scores(**kwargs):
        return [
            {"code": "000001", "name": "买入样本", "score": 80, "action": "buy", "position_amount": 1000},
            {"code": "000002", "name": "卖出样本", "score": 30, "action": "sell", "position_amount": 500},
            {"code": "000003", "name": "止损样本", "score": 20, "action": "stop_loss"},
            {"code": "000004", "name": "撤单样本", "score": 10, "action": "entry_cancelled"},
        ]

    async def fake_outside_scan(**kwargs):
        return [
            {
                "code": "000005",
                "name": "池外样本",
                "action": "actionable",
                "suggested_amount": 900,
                "lot_value": 900,
                "affordable": True,
                "source": "fixture",
            }
        ]

    async def fake_push(*args, **kwargs):
        return {"feishu_webhook": False, "error": "disabled_in_test"}

    real_render = daily_report.build_next_day_strategy_sections

    def capture_render(**kwargs):
        captured["gate"] = dict(kwargs["visible_decision_gate"])
        captured["visible_decision"] = apply_visible_decision_gate(
            kwargs["decision"],
            kwargs["visible_decision_gate"],
        )
        lines = real_render(**kwargs)
        captured["rendered_sections"] = "\n".join(lines)
        return lines

    monkeypatch.setenv("CONGXI_PORTFOLIO_PATH", str(portfolio_path))
    monkeypatch.setenv("CONGXI_VISIBLE_DECISION_GATE_PATH", str(gate_path))
    report_day = date.today()
    target_day = report_day + timedelta(days=1)
    monkeypatch.setenv("CONGXI_REPORT_DATE", report_day.isoformat())
    monkeypatch.setenv("CONGXI_TARGET_DATE", target_day.isoformat())
    monkeypatch.delenv("CONGXI_REPORT_LEGACY_SECTIONS", raising=False)
    monkeypatch.setattr(daily_report, "ARCHIVE_DIR", str(archive_dir))
    monkeypatch.setattr(daily_report, "load_sentinel_research_package", lambda day: None)
    monkeypatch.setattr(daily_report, "build_target_scores_for_report", fake_target_scores)
    monkeypatch.setattr(daily_report, "build_refreshed_outside_pool_scan_for_report", fake_outside_scan)
    monkeypatch.setattr(daily_report, "persist_outside_pool_scan_to_target_pool", lambda *args, **kwargs: 0)
    monkeypatch.setattr(daily_report, "build_next_day_strategy_sections", capture_render)
    monkeypatch.setattr(daily_report, "push_daily_report_to_feishu", fake_push)
    monkeypatch.setattr(realtime_module, "FastRealtimeMarketDataSource", FakeRealtimeSource)
    monkeypatch.setattr(akshare_module, "AKShareMarketClient", lambda: object())
    monkeypatch.setattr(analysis_module, "run_analysis", fake_analysis)
    monkeypatch.setattr(workshop_module, "run_debate", fake_debate)
    monkeypatch.setattr(evidence_module, "build_sentinel_evidence_context", lambda package: {})
    monkeypatch.setattr(evidence_module, "upsert_sentinel_evidence_to_target_pool", lambda package: {})
    monkeypatch.setattr(portfolio_store_module, "recalculate_portfolio", lambda portfolio: portfolio)
    monkeypatch.setattr(
        portfolio_store_module,
        "sync_db_from_user_portfolio",
        lambda session, path: (_ for _ in ()).throw(RuntimeError(secret)),
    )
    monkeypatch.setattr("app.database.SessionLocal", lambda: db)
    monkeypatch.setattr(lifecycle_module, "PositionWatchStore", FakePositionWatchStore)

    report_path = await daily_report.main()

    persisted_portfolio = json.loads(portfolio_path.read_text(encoding="utf-8"))
    persisted_gate = json.loads(gate_path.read_text(encoding="utf-8"))
    report_content = Path(report_path).read_text(encoding="utf-8")
    output = capsys.readouterr().out
    visible = captured["visible_decision"]
    target_rows = visible["target_scores"]
    outside_rows = visible["outside_pool_scan"]
    market_source_status = captured["market_data"]["market_source_status"]

    assert db.closed is True
    assert persisted_portfolio["portfolio_sync_failed"] is True
    assert captured["market_data"]["portfolio_sync_failed"] is True
    assert "shanghai" in captured["market_data"]["indices"]
    assert "cyb" in captured["market_data"]["indices"]
    assert "shenzhen" not in captured["market_data"]["indices"]
    assert market_source_status["status"] == "degraded"
    assert market_source_status["freshness_status"] == "degraded"
    assert "sz399001" in (
        market_source_status["missing_sources"]
        + market_source_status["rejected_sources"]
    )
    assert persisted_gate["reasons"] == ["portfolio_sync_failed"]
    assert captured["gate"] == persisted_gate
    assert target_rows[0]["action"] == "watching"
    assert target_rows[0]["position_amount"] == 0
    assert outside_rows[0]["action"] == "watching"
    assert outside_rows[0]["suggested_amount"] == 0
    assert [row["action"] for row in target_rows[1:]] == [
        "sell",
        "stop_loss",
        "entry_cancelled",
    ]
    assert "| SQLite | degraded |" in captured["rendered_sections"]
    assert "| SQLite | degraded |" in report_content
    assert "| 行情数据 | degraded |" in captured["rendered_sections"]
    assert "coverage=2/3" in captured["rendered_sections"]
    assert "sz399001" in captured["rendered_sections"]
    for artifact in (output, report_content, json.dumps(persisted_gate), json.dumps(persisted_portfolio)):
        assert "SECRET-MAIN-PROBE" not in artifact


def test_growth_sprint_profile_uses_confirmed_high_return_limits(monkeypatch):
    from scripts.daily_report import (
        get_strategy_profile,
        build_execution_guard,
        build_next_day_strategy_sections,
    )

    monkeypatch.delenv("CONGXI_STRATEGY_MODE", raising=False)
    default_profile = get_strategy_profile()
    assert default_profile["mode"] == "growth_sprint"

    profile = get_strategy_profile("growth_sprint")
    assert profile["mode"] == "growth_sprint"
    assert profile["max_drawdown_pct"] == 10
    assert profile["single_position_limit_pct"] == 50
    assert profile["stop_loss_pct"] == 10
    assert profile["risk_per_trade_pct"] == 2
    assert profile["target_profit_pct"] == 20
    assert profile["min_reward_risk_ratio"] == 2
    assert profile["allow_high_volatility"] is True

    guard = build_execution_guard(
        positions=[],
        available_cash=3085.6,
        total_assets=3085.6,
        strategy_profile=profile,
    )

    assert "高收益试验模式" in guard
    assert "账户最大回撤 -10%" in guard
    assert "单票上限 50%" in guard
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
    assert "Sentinel/Serenity" in sections


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
