"""Daily report delivery and Obsidian archive regression tests."""
import json

import pytest


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
    assert "次日首个15分钟仍未收回 ¥7.61，卖出100股" in summary
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
    assert "次日确认止损" in sections
    assert "次日首个15分钟仍未收回 ¥7.61，卖出100股" in sections
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
    assert "次日首个15分钟" in first_screen


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
