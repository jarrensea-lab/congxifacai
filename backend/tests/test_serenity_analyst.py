"""tests for serenity_analyst module"""
from app.ai.serenity_analyst import (
    score_company, score_summary_table, check_red_flags, summarize_red_flags,
    get_theme_chokepoints, get_chokepoint_prompt, evidence_summary,
    RESEARCHER_DEBATE_PROMPT, VALUE_CHAIN_LAYERS, RED_FLAGS,
)


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
