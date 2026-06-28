"""tests for serenity_analyst module"""
from pathlib import Path

from app.ai.serenity_analyst import (
    score_company, score_summary_table, check_red_flags, summarize_red_flags,
    get_theme_chokepoints, get_chokepoint_prompt, evidence_summary,
    RESEARCHER_DEBATE_PROMPT, VALUE_CHAIN_LAYERS, RED_FLAGS,
    SERENITY_SCORING_V2, score_company_v2, get_theme_candidates,
    run_serenity_pipeline, build_serenity_research_report,
    load_theme_candidate_pool, adjust_scores_with_quote_evidence,
    validate_theme_candidate_pool,
)
from app.ai.serenity_evidence import build_quote_evidence, build_verification_tasks
from scripts.serenity_research_report import save_serenity_report
from scripts.validate_serenity_candidates import main as validate_serenity_candidates_main


class TestScoreCard:
    def test_score_company_basic(self):
        r = score_company("澜起科技", "688008",
                           demand_certainty=8, transmission_clarity=8,
                           business_purity=7, market_cap_elasticity=6,
                           market_neglect=5, verification_speed=7, downside_risk=6)
        assert r["code"] == "688008"
        assert r["name"] == "澜起科技"
        assert 0 <= r["score"] <= 100
        assert len(r["scores"]) == 7
        assert r["score"] > 50  # should be decent

    def test_score_company_low(self):
        r = score_company("低质量标的", "000001",
                           demand_certainty=1, transmission_clarity=1,
                           business_purity=2, market_cap_elasticity=3,
                           market_neglect=2, verification_speed=2, downside_risk=8)
        assert r["score"] < 40

    def test_score_company_high(self):
        r = score_company("优质标的", "300001",
                           demand_certainty=9, transmission_clarity=9,
                           business_purity=9, market_cap_elasticity=9,
                           market_neglect=9, verification_speed=9, downside_risk=9)
        assert r["score"] > 80

    def test_score_summary_table(self):
        r1 = score_company("A公司", "000001",
                            demand_certainty=8, transmission_clarity=8,
                            business_purity=7, market_cap_elasticity=6,
                            market_neglect=5, verification_speed=7, downside_risk=6)
        r2 = score_company("B公司", "000002",
                            demand_certainty=5, transmission_clarity=5,
                            business_purity=6, market_cap_elasticity=7,
                            market_neglect=4, verification_speed=5, downside_risk=5)
        table = score_summary_table([r1, r2])
        assert "A公司" in table
        assert "B公司" in table
        assert "总分" in table

    def test_score_validation_ranges(self):
        # No validation enforced - passes any value
        r = score_company("X", "000001",
                           demand_certainty=11, transmission_clarity=8,
                           business_purity=7, market_cap_elasticity=6,
                           market_neglect=5, verification_speed=7, downside_risk=6)
        assert r["score"] > 0  # No error raised (no validation)


class TestRedFlags:
    def test_all_clear(self):
        flags = check_red_flags({flag["id"]: False for flag in RED_FLAGS})
        assert len(flags) == 0

    def test_single_trigger(self):
        flags = check_red_flags({"social_media_driven": True})
        assert len(flags) == 1
        assert flags[0]["id"] == "social_media_driven"
        assert flags[0]["severity"] == "high"

    def test_multiple_triggers(self):
        flags = check_red_flags({
            "single_customer_rumor": True,
            "needs_financing": True,
            "insider_selling": True,
        })
        assert len(flags) == 3
        high_count = sum(1 for f in flags if f["severity"] == "high")
        assert high_count == 2

    def test_custom_flags(self):
        flags = check_red_flags({}, custom_flags=["自定义风险"])
        assert len(flags) == 1
        assert flags[0]["label"] == "自定义风险"

    def test_summarize_red_flags(self):
        flags = check_red_flags({
            "needs_financing": True,
            "insider_selling": True,
        })
        summary = summarize_red_flags(flags)
        assert "高风险信号" in summary
        assert "中等风险信号" in summary

    def test_summarize_no_flags(self):
        summary = summarize_red_flags([])
        assert "未检测到" in summary


class TestIndustryChain:
    def test_get_theme_chokepoints(self):
        cps = get_theme_chokepoints("AI半导体")
        assert len(cps) >= 5
        assert "HBM/DDR5" in cps[0] or "先进封装" in cps[1]

    def test_get_theme_chokepoints_cpo(self):
        cps = get_theme_chokepoints("CPO光通信")
        assert len(cps) >= 5
        assert "InP" in cps[0] or "硅光芯片" in cps[1] or "EML/VCSEL" in cps[2]

    def test_get_theme_chokepoints_unknown(self):
        cps = get_theme_chokepoints("未知主题")
        assert cps == []

    def test_value_chain_layers(self):
        assert len(VALUE_CHAIN_LAYERS) == 8
        names = [v[0] for v in VALUE_CHAIN_LAYERS]
        assert "芯片、器件与关键组件" in names
        assert "材料、耗材与特种输入" in names

    def test_researcher_prompt_format(self):
        """Verify RESEARCHER_DEBATE_PROMPT can be formatted"""
        prompt = RESEARCHER_DEBATE_PROMPT.format(
            news_context="测试新闻",
            market_data="测试市场数据",
            holdings_data="测试持仓",
        )
        assert "测试新闻" in prompt
        assert "产业链研究员" in prompt
        assert "chokepoint_candidates" in prompt

    def test_chokepoint_prompt(self):
        """Verify get_chokepoint_prompt generates valid prompt"""
        prompt = get_chokepoint_prompt(theme="AI半导体", news_summary="今日热点")
        assert "AI半导体" in prompt or "产业链" in prompt
        assert "json" in prompt.lower()


class TestEvidenceSummary:
    def test_evidence_basic(self):
        items = [
            {"fact": "季报显示收入增长30%", "strength": "strong", "source": "财报"},
            {"fact": "有媒体报道利好", "strength": "medium", "source": "财联社"},
        ]
        summary = evidence_summary("测试公司", items)
        assert "测试公司" in summary
        assert "强证据" in summary
        assert "中等证据" in summary

    def test_evidence_empty(self):
        summary = evidence_summary("无数据公司", [])
        assert "暂无明确证据" in summary

    def test_setup_teardown(self):
        pass  # no special setup needed


class TestSerenityPipelineV2:
    def test_scoring_v2_has_eight_dimensions(self):
        assert len(SERENITY_SCORING_V2) == 8
        assert "瓶颈强度" in SERENITY_SCORING_V2
        assert "证据强度" in SERENITY_SCORING_V2

        result = score_company_v2(
            name="测试公司",
            code="688001",
            chokepoint="先进封装",
            chain_position="设备与材料",
            scores={
                "需求确定性": 8,
                "瓶颈强度": 9,
                "传导清晰度": 8,
                "业务纯度": 7,
                "证据强度": 7,
                "市场忽视度": 5,
                "验证速度": 6,
                "下行安全": 5,
            },
            evidence_items=[{"fact": "公告或财报仍待核验", "strength": "medium", "source": "待核验"}],
        )

        assert result["score"] > 60
        assert len(result["scores"]) == 8
        assert result["research_tier"] in {"高优先级研究", "强观察", "初步线索", "弱线索"}

    def test_red_flags_downgrade_research_only(self):
        result = score_company_v2(
            name="噪音标的",
            code="300001",
            chokepoint="传闻客户",
            chain_position="未知",
            scores={
                "需求确定性": 7,
                "瓶颈强度": 8,
                "传导清晰度": 3,
                "业务纯度": 3,
                "证据强度": 2,
                "市场忽视度": 8,
                "验证速度": 4,
                "下行安全": 2,
            },
            red_flag_signals={
                "social_media_driven": True,
                "micro_cap_liquidity": True,
            },
        )

        assert result["actionability"] == "reject_for_now"
        assert result["research_tier"] == "弱线索"
        assert any(flag["id"] == "social_media_driven" for flag in result["red_flags"])

    def test_theme_candidate_map_supports_small_account_context(self):
        candidates = get_theme_candidates("电网设备")
        assert candidates
        assert any(c["code"] == "000400" for c in candidates)

        pipeline = run_serenity_pipeline("电网设备", available_cash=3085.61, total_assets=3085.61)
        assert pipeline["normalized_theme"] == "AI基建/电力"
        assert pipeline["available_cash"] == 3085.61
        assert pipeline["candidates"]
        assert "仅观察" in pipeline["account_constraint"]

    def test_report_is_research_only_and_has_tables(self):
        pipeline = run_serenity_pipeline("机器人", available_cash=3085.61, total_assets=3085.61)
        report = build_serenity_research_report(pipeline)

        assert "# Serenity瓶颈选股报告" in report
        assert "| 标的 | 代码 |" in report
        assert "研究优先级" in report
        assert "红旗" in report
        assert "不构成投资建议" in report
        assert "买入" not in report
        assert "卖出" not in report

    def test_unknown_theme_returns_safe_empty_pipeline(self):
        pipeline = run_serenity_pipeline("未知主题", available_cash=3085.61)
        report = build_serenity_research_report(pipeline)

        assert pipeline["candidates"] == []
        assert "暂无内置候选池" in report
        assert "不构成投资建议" in report

    def test_theme_candidate_pool_loads_external_json(self, tmp_path):
        pool_path = tmp_path / "theme_candidates.json"
        pool_path.write_text(
            """
{
  "aliases": {"测试主题": "测试产业链"},
  "candidates": {
    "测试产业链": [
      {
        "name": "测试电气",
        "code": "300001",
        "chokepoint": "测试瓶颈",
        "chain_position": "测试位置",
        "scores": {
          "需求确定性": 7,
          "瓶颈强度": 8,
          "传导清晰度": 7,
          "业务纯度": 6,
          "证据强度": 5,
          "市场忽视度": 4,
          "验证速度": 6,
          "下行安全": 5
        },
        "evidence_items": [
          {"fact": "测试证据仍待公告核验", "strength": "medium", "source": "测试源"}
        ],
        "verify_next": "核验测试订单、收入和毛利率。"
      }
    ]
  }
}
""".strip(),
            encoding="utf-8",
        )

        pool = load_theme_candidate_pool(str(pool_path))

        assert pool["aliases"]["测试主题"] == "测试产业链"
        assert pool["candidates"]["测试产业链"][0]["code"] == "300001"

    def test_theme_candidate_pool_invalid_json_falls_back_to_defaults(self, tmp_path):
        pool_path = tmp_path / "broken.json"
        pool_path.write_text("{not-json", encoding="utf-8")

        pool = load_theme_candidate_pool(str(pool_path))

        assert "电网设备" in pool["aliases"]
        assert "AI基建/电力" in pool["candidates"]

    def test_candidate_pool_validation_rejects_missing_required_field(self):
        raw = {
            "aliases": {},
            "candidates": {
                "测试产业链": [
                    {
                        "name": "缺代码公司",
                        "chokepoint": "测试瓶颈",
                        "chain_position": "测试位置",
                        "scores": {
                            "需求确定性": 7,
                            "瓶颈强度": 8,
                            "传导清晰度": 7,
                            "业务纯度": 6,
                            "证据强度": 5,
                            "市场忽视度": 4,
                            "验证速度": 6,
                            "下行安全": 5,
                        },
                    }
                ]
            },
        }

        errors = validate_theme_candidate_pool(raw)

        assert any("code" in error for error in errors)

    def test_candidate_pool_validation_rejects_invalid_score_dimension(self):
        raw = {
            "aliases": {},
            "candidates": {
                "测试产业链": [
                    {
                        "name": "分数非法公司",
                        "code": "300001",
                        "chokepoint": "测试瓶颈",
                        "chain_position": "测试位置",
                        "scores": {
                            "需求确定性": 11,
                            "瓶颈强度": 8,
                            "传导清晰度": 7,
                            "业务纯度": 6,
                            "证据强度": 5,
                            "市场忽视度": 4,
                            "验证速度": 6,
                            "下行安全": 5,
                        },
                    }
                ]
            },
        }

        errors = validate_theme_candidate_pool(raw)

        assert any("需求确定性" in error and "1-10" in error for error in errors)

    def test_validate_serenity_candidates_script_reports_errors(self, tmp_path, capsys):
        pool_path = tmp_path / "theme_candidates.json"
        pool_path.write_text(
            '{"aliases": {}, "candidates": {"测试产业链": [{"name": "缺字段"}]}}',
            encoding="utf-8",
        )

        exit_code = validate_serenity_candidates_main([str(pool_path)])
        output = capsys.readouterr().out

        assert exit_code == 1
        assert "候选池校验失败" in output
        assert "code" in output

    def test_pipeline_adds_verification_tasks_to_candidates(self):
        pipeline = run_serenity_pipeline("电网设备", available_cash=3085.61, total_assets=3085.61)
        first = pipeline["candidates"][0]

        assert first["verification_tasks"]
        assert first["verification_tasks"][0]["candidate_code"] == first["code"]
        assert first["verification_tasks"][0]["source_type"] in {"announcement", "financial_report", "market_data"}
        assert pipeline["verification_tasks"]

    def test_report_contains_verification_task_list_without_trading_language(self):
        pipeline = run_serenity_pipeline("机器人", available_cash=3085.61, total_assets=3085.61)
        report = build_serenity_research_report(pipeline)

        assert "## 待核验任务清单" in report
        assert "| 标的 | 优先级 | 核验任务 | 数据源 |" in report
        assert "公告/财报/订单" in report
        assert "买入" not in report
        assert "卖出" not in report


class TestSerenityEvidenceCollector:
    def test_quote_adjustment_downgrades_safety_when_lot_exceeds_cash(self):
        candidate = {"name": "高价电气", "code": "300001"}
        quote = {"price": 55.0, "amount_wan": 12000.0, "pe_ttm": 28.0, "pb": 2.5}
        evidence = build_quote_evidence(candidate, quote, available_cash=3085.61)
        scores = {
            "需求确定性": 7,
            "瓶颈强度": 8,
            "传导清晰度": 7,
            "业务纯度": 6,
            "证据强度": 6,
            "市场忽视度": 5,
            "验证速度": 6,
            "下行安全": 7,
        }

        adjusted = adjust_scores_with_quote_evidence(scores, evidence)

        assert adjusted["scores"]["下行安全"] == 5
        assert adjusted["red_flag_signals"] == {}
        assert any("一手金额" in reason["reason"] for reason in adjusted["reasons"])

    def test_quote_adjustment_flags_extreme_valuation(self):
        candidate = {"name": "高估值材料", "code": "688001"}
        quote = {"price": 20.0, "amount_wan": 15000.0, "pe_ttm": 96.0, "pb": 12.0}
        evidence = build_quote_evidence(candidate, quote, available_cash=5000.0)
        scores = {
            "需求确定性": 7,
            "瓶颈强度": 8,
            "传导清晰度": 7,
            "业务纯度": 6,
            "证据强度": 6,
            "市场忽视度": 5,
            "验证速度": 6,
            "下行安全": 7,
        }

        adjusted = adjust_scores_with_quote_evidence(scores, evidence)

        assert adjusted["scores"]["下行安全"] == 5
        assert adjusted["red_flag_signals"]["valuation_assumes_perfection"] is True
        assert any("PE" in reason["reason"] and "PB" in reason["reason"] for reason in adjusted["reasons"])

    def test_quote_adjustment_weakens_evidence_when_turnover_missing(self):
        candidate = {"name": "低流动性器件", "code": "300002"}
        quote = {"price": 8.0, "amount_wan": 0, "pe_ttm": 30.0, "pb": 2.0}
        evidence = build_quote_evidence(candidate, quote, available_cash=5000.0)
        scores = {
            "需求确定性": 7,
            "瓶颈强度": 8,
            "传导清晰度": 7,
            "业务纯度": 6,
            "证据强度": 6,
            "市场忽视度": 5,
            "验证速度": 6,
            "下行安全": 7,
        }

        adjusted = adjust_scores_with_quote_evidence(scores, evidence)

        assert adjusted["scores"]["证据强度"] == 5
        assert any("成交额" in reason["reason"] for reason in adjusted["reasons"])

    def test_pipeline_report_shows_quote_score_adjustment_reasons(self):
        async def fake_fetcher(codes):
            return {
                code: {
                    "price": 55.0,
                    "change_pct": 1.2,
                    "amount_wan": 0,
                    "mcap_yi": 80.0,
                    "pe_ttm": 96.0,
                    "pb": 12.0,
                    "source": "fake",
                }
                for code in codes
            }

        pipeline = run_serenity_pipeline(
            "电网设备",
            available_cash=3085.61,
            total_assets=3085.61,
            quote_fetcher=fake_fetcher,
        )
        report = build_serenity_research_report(pipeline)

        first = pipeline["candidates"][0]
        assert first["score_adjustments"]
        assert "## 评分调整原因" in report
        assert "一手金额" in report
        assert "成交额" in report
        assert "买入" not in report
        assert "卖出" not in report

    def test_build_verification_tasks_maps_candidate_to_evidence_work(self):
        candidate = {
            "name": "测试电气",
            "code": "300001",
            "chokepoint": "高压开关",
            "verify_next": "核验订单、毛利率和客户结构。",
            "evidence_items": [
                {"fact": "现有证据待核验", "strength": "weak", "source": "内部方法论待核验"}
            ],
        }

        tasks = build_verification_tasks("AI基建/电力", candidate)

        assert tasks[0]["candidate_name"] == "测试电气"
        assert tasks[0]["candidate_code"] == "300001"
        assert tasks[0]["theme"] == "AI基建/电力"
        assert tasks[0]["priority"] == "high"
        assert tasks[0]["source_type"] == "announcement"

    def test_build_quote_evidence_adds_small_account_metrics(self):
        candidate = {"name": "测试电气", "code": "300001"}
        quote = {
            "price": 12.34,
            "change_pct": 2.5,
            "amount_wan": 54321.0,
            "mcap_yi": 88.8,
            "pe_ttm": 21.6,
            "pb": 2.4,
            "source": "fake",
        }

        evidence = build_quote_evidence(candidate, quote, available_cash=3085.61)

        assert evidence["strength"] == "medium"
        assert evidence["source"] == "fake行情"
        assert "现价 12.34 元" in evidence["fact"]
        assert "一手金额 1234.00 元" in evidence["fact"]
        assert evidence["metrics"]["lot_value"] == 1234.0
        assert evidence["metrics"]["cash_coverage_ratio"] == 2.5

    def test_pipeline_can_collect_quote_evidence_without_changing_trade_boundary(self):
        async def fake_fetcher(codes):
            return {
                code: {
                    "price": 10.0,
                    "change_pct": 1.2,
                    "amount_wan": 10000.0,
                    "mcap_yi": 80.0,
                    "pe_ttm": 18.0,
                    "pb": 2.0,
                    "source": "fake",
                }
                for code in codes
            }

        pipeline = run_serenity_pipeline(
            "电网设备",
            available_cash=3085.61,
            total_assets=3085.61,
            quote_fetcher=fake_fetcher,
        )
        report = build_serenity_research_report(pipeline)

        first = pipeline["candidates"][0]
        assert first["quote_evidence"]["metrics"]["lot_value"] == 1000.0
        assert any(item.get("source") == "fake行情" for item in first["evidence_items"])
        assert "行情核验" in report
        assert "一手金额 1000.00 元" in report
        assert "买入" not in report
        assert "卖出" not in report

    def test_pipeline_quote_collection_fails_safe(self):
        async def failing_fetcher(codes):
            raise RuntimeError("network unavailable")

        pipeline = run_serenity_pipeline(
            "机器人",
            available_cash=3085.61,
            total_assets=3085.61,
            quote_fetcher=failing_fetcher,
        )

        assert pipeline["quote_status"]["status"] == "failed"
        assert pipeline["candidates"]
        assert all("quote_evidence" not in item for item in pipeline["candidates"])

    def test_save_serenity_report_accepts_quote_fetcher(self, tmp_path):
        async def fake_fetcher(codes):
            return {
                code: {
                    "price": 10.0,
                    "change_pct": 1.2,
                    "amount_wan": 10000.0,
                    "mcap_yi": 80.0,
                    "pe_ttm": 18.0,
                    "pb": 2.0,
                    "source": "fake",
                }
                for code in codes
            }

        result = save_serenity_report(
            "电网设备",
            report_date="2026-06-26",
            archive_dir=str(tmp_path),
            available_cash=3085.61,
            total_assets=3085.61,
            quote_fetcher=fake_fetcher,
        )
        report_path = Path(result["report_path"])
        report = report_path.read_text(encoding="utf-8")

        assert result["report_path"].endswith("2026-06-26_Serenity瓶颈选股报告-电网设备.md")
        assert report_path.parent == tmp_path / "2026" / "06" / "2026-06-26"
        assert "行情核验" in report
        assert "一手金额 1000.00 元" in report
