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
        "## 一、明日【唯一】实盘狙击标的（可执行）",
        "## 二、明日盘中雷达触发池",
        "## 三、持仓与市场风控",
        "## 四、后台风控与策略审计",
        "## 五、数据覆盖与评分审计",
        "## 六、复盘与自迭代",
        "## 七、研究归档链接",
    ):
        assert heading in sections
    assert "当前持仓怎么处理" in sections
    assert "是否需要卖" in sections
    assert "核心主攻" in sections
    assert "明日盘中雷达触发池" in sections
    assert "2026-06-29" in sections
    assert "AI半导体" in sections
    assert "Serenity 深挖" in sections
    assert "2026-06-28_Serenity深挖-AI半导体.md" in sections
    assert "完整辩论记录" in sections


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

    dashboard = sections.index("## 一、明日【唯一】实盘狙击标的（可执行）")
    trigger_pool = sections.index("## 二、明日盘中雷达触发池")
    risk = sections.index("## 三、持仓与市场风控")
    audit = sections.index("## 四、后台风控与策略审计")
    assert dashboard < trigger_pool < risk < audit

    first_screen = sections[:trigger_pool]
    assert "核心主攻：钒钛股份(000629)" in first_screen
    assert "买入逻辑：已具备量能线索，博弈低价股资金回流" in first_screen
    assert "一手约¥355.00" in first_screen
    assert "止损位：¥3.37" in first_screen
    assert "第一目标位：¥3.98" in first_screen
    assert "池外小账户补扫" not in first_screen

    audit_screen = sections[audit:]
    assert "账户预算不足阻断" in audit_screen
    assert "预算阻断 1 只" in audit_screen
    assert "北方华创" not in sections
    assert "角色投票审计" in audit_screen
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
    from scripts.daily_report import build_next_day_strategy_sections

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

    assert "## 五、数据覆盖与评分" in sections
    assert "角色投票审计" in sections
    assert "688008" in sections
    assert "猎手 7分" in sections
    assert "Serenity 8分" in sections
    assert "ev_test" in sections
    assert "H7" not in sections
    assert "S8" not in sections
    assert "未触发风控" not in sections
    assert "主报告以结构化评分为准" in sections


def test_build_next_day_strategy_sections_separates_executable_and_research_reference():
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

    assert "### 今日可执行标的" in sections
    assert "低价突破(002123)" in sections
    assert "### 研究参照标的" in sections
    assert "澜起科技(688008)" not in sections
    assert "预算阻断 1 只" in sections
    assert "买不起最小交易单位" not in sections
    assert "数据不足，建议观望" not in sections


def test_build_next_day_strategy_sections_excludes_legacy_report_order():
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
        decision={"target_scores": []},
        roles={},
        sentinel_package=None,
    ))

    assert "## 📈 一、市场概况" not in sections
    assert "## 🧠 三、AI 多维度分析" not in sections
    assert sections.index("## 一、明日【唯一】实盘狙击标的（可执行）") < sections.index("## 二、明日盘中雷达触发池")
    assert sections.index("## 二、明日盘中雷达触发池") < sections.index("## 四、后台风控与策略审计")


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
    assert "核心主攻：钒钛股份(000629)" in sections
    assert "账户可买上限价" in sections
    assert "触发价参考" in sections
    assert "¥3.37" in sections
    assert "¥3.98" in sections
    assert "一手约¥355.00" in sections
    assert "买入逻辑" in sections
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
                    "decision_reason": "缺少结构化数据项：kline、fund_flow；先补数据，不使用泛化观望兜底。",
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
        "fund_flow",
        "kline",
    ]
    for token in forbidden:
        assert token not in sections
    assert "个股资金流" in sections
    assert "K线" in sections
    assert "研究参照" in sections
    assert "账户可买上限价" in sections


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

    assert "### 中低频观察/配置线" in sections
    assert "当前没有形成中低频观察/配置候选" in sections
    assert "北方华创(002371)" not in sections
    assert "账户总资产至少" not in sections
    assert "¥3,042.80" in sections
    assert "¥30.42" in sections
    assert "价格回落至" not in sections
    assert "可人工复核买入" not in sections


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
    assert "结构化评分摘要" in sections
    assert "预算阻断 1 只已隐藏" in sections


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
    assert tcl["stop_loss"] == 4.56
    assert tcl["target_price"] == 5.38
    assert tcl["suggested_amount"] == 480.0
    assert "量能线索" in tcl["watch_reason"]


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
