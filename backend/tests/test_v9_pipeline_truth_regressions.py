from __future__ import annotations

import asyncio
from datetime import date, datetime

class _EmptyBatchQuoteSource:
    def __init__(self):
        self.batch_calls = 0
        self.single_calls: list[str] = []

    async def fetch_batch(self, _codes):
        self.batch_calls += 1
        return {}

    async def fetch(self, code):
        self.single_calls.append(code)
        return {
            "code": code,
            "price": 4.24,
            "source": "tencent",
            "quote_timestamp": "2026-07-30T16:14:54+08:00",
            "freshness": "valid_close",
        }


def test_quote_recovery_retries_batch_then_falls_back_per_symbol():
    from scripts import daily_report

    source = _EmptyBatchQuoteSource()
    quotes = asyncio.run(
        daily_report._fetch_quotes_with_recovery(
            source,
            ["sz002131", "sh600900"],
            max_attempts=2,
            sleeper=lambda _: asyncio.sleep(0),
        )
    )

    assert source.batch_calls == 2
    assert source.single_calls == ["sz002131", "sh600900"]
    assert set(quotes) == {"sz002131", "sh600900"}
    assert all(item["freshness"] == "valid_close" for item in quotes.values())


def test_report_date_close_quotes_are_accepted_after_the_intraday_fresh_window():
    from app.services.market_data_health import aggregate_market_quote_truth

    result = aggregate_market_quote_truth(
        {
            "sh000001": {
                "price": 3804.69,
                "change_pct": 0.16,
                "source": "tencent",
                "quote_timestamp": "2026-07-30T16:14:54+08:00",
                "freshness": "valid_close",
            },
        },
        ["sh000001"],
        default_provider="tencent",
        now=datetime.fromisoformat("2026-07-31T02:04:00+08:00"),
        valid_close_date=date.fromisoformat("2026-07-30"),
    )

    assert result["market_source_status"]["status"] == "ok"
    assert result["quotes"]["sh000001"]["price"] == 3804.69


def test_report_audit_treats_same_day_official_close_as_healthy():
    from scripts.daily_report import build_data_source_audit

    audit = "\n".join(build_data_source_audit(
        market_data={
            "indices": {
                "shanghai": 3804.69,
                "shenzhen": 13285.8,
                "cyb": 3244.62,
            },
            "market_source_status": {
                "status": "ok",
                "provider": "tencent",
                "data_cutoff": "2026-07-30T16:14:00+08:00",
                "freshness_status": "valid_close",
                "error": "",
                "coverage": {"expected": 3, "verified": 3},
                "missing_sources": [],
                "rejected_sources": [],
            },
        },
        sentinel_package=None,
        report_date="2026-07-30",
    ))

    assert "| 行情数据 | 成功 |" in audit
    assert "有效收盘" in audit


def test_report_exposes_consumed_market_evidence_counts_without_fake_gaps():
    from app.report_engine.templates.next_day import render_next_day_sections
    from scripts.daily_report import build_data_source_audit

    audit = "\n".join(build_data_source_audit(
        market_data={
            "indices": {},
            "sectors": [{"name": "行业"}, {"name": "概念"}],
            "news": [{"title": "新闻"}],
            "lhb": [{"code": "002131"}],
            "big_deals": [{"name": "利欧股份"}],
        },
        sentinel_package=None,
    ))
    report = "\n".join(render_next_day_sections({
        "target_date": "2026-07-31",
        "holdings": [],
        "candidates": [],
        "candidate_missing_reason": "当前预算不足",
        "long_horizon": [],
        "research_reference": [],
        "budget_blocked_count": 0,
        "audit_lines": [],
        "review_lines": [],
    }))

    assert "行业/概念 2 项" in audit
    assert "龙虎榜 1 条" in audit
    assert "大单 1 条" in audit
    assert "当前阻断条件：当前预算不足" in report
    assert "待补信号/数据" not in report


def test_budget_blocked_names_are_hidden_from_user_facing_report_sections():
    from app.report_engine.templates.next_day import render_next_day_sections
    from scripts.daily_report import build_v9_opportunity_section

    budget_name = "贵州茅台"
    report = "\n".join(
        build_v9_opportunity_section({
            "account": {
                "available_cash": 1383.25,
                "cash_reserve": 612.43,
                "buy_budget": 770.82,
            },
            "metrics": {
                "scanned_count": 5197,
                "affordable_count": 1281,
                "scored_count": 33,
            },
            "scorecards": [],
            "lifecycle_events": [{
                "code": "600519",
                "name": budget_name,
                "event": "removed",
                "block_reason": "lot_size_exceeded",
                "reason": "最小交易单位金额超过当前单票预算",
            }],
            "health": {"status": "succeeded"},
        })
    )
    report += "\n" + "\n".join(
        render_next_day_sections({
            "target_date": "2026-07-31",
            "holdings": [],
            "candidates": [],
            "candidate_missing_reason": "68 只候选已按预算过滤",
            "long_horizon": [],
            "budget_blocked": [{
                "label": f"{budget_name}(600519)",
                "lot_value_text": "¥136,176.00",
                "reason": "超过预算",
                "next_signal": "资金增加后重新评分",
            }],
            "budget_blocked_count": 68,
            "research_reference": [],
            "audit_lines": [],
            "review_lines": [],
        })
    )

    assert "预算阻断 68 只" in report
    assert budget_name not in report
    assert "今日剔除" not in report


def test_full_market_discovery_recognizes_star_and_chinext_registration_codes():
    from app.services.small_account_discovery import (
        discover_affordable_market_candidates,
    )

    result = discover_affordable_market_candidates(
        market_rows=[
            {"code": "301001", "name": "创业板样本", "price": 3.0},
            {"code": "688001", "name": "科创板样本", "price": 3.0},
        ],
        available_cash=2000,
        total_assets=4000,
        priority_codes={"301001", "688001"},
        max_candidates=2,
    )

    assert {item["code"] for item in result["candidates"]} == {
        "301001",
        "688001",
    }
    assert result["rejected"]["priority_missing"] == []


def test_zero_long_score_without_thesis_is_not_rendered_as_missing_thesis():
    from scripts.daily_report import _long_horizon_view

    rows = _long_horizon_view({
        "target_scores": [{
            "code": "601766",
            "name": "中国中车",
            "score": 61,
            "long_quality_score": 0,
            "thesis_status": "",
            "red_line_status": "",
        }]
    })

    assert rows == []


def test_current_stock_holding_without_long_thesis_is_rendered_as_unbuilt():
    from scripts.daily_report import _long_horizon_view

    rows = _long_horizon_view(
        {"target_scores": []},
        positions=[{
            "code": "600900",
            "name": "长江电力",
            "shares": 100,
        }],
    )

    assert rows == [{
        "label": "长江电力(600900)",
        "thesis_status": "未建论文",
        "valuation_zone": "待估值",
        "long_quality_score": "未知",
        "red_line_status": "红线状态未知",
        "action_nature": "持仓论文待建",
        "next_signal": (
            "当前为真实持仓，但尚未建立可验证的中长期论文；"
            "下一步补齐盈利质量、自由现金流、负债与分红、行业周期和估值证据。"
        ),
    }]


def test_current_etf_holding_without_long_thesis_uses_etf_review_evidence():
    from scripts.daily_report import _long_horizon_view

    rows = _long_horizon_view(
        {"target_scores": []},
        positions=[{
            "code": "159915",
            "name": "创业板ETF易方达",
            "shares": 200,
        }],
    )

    assert len(rows) == 1
    assert rows[0]["thesis_status"] == "未建论文"
    assert "跟踪指数估值、行业权重、波动回撤、费率和流动性" in rows[0]["next_signal"]


def test_long_horizon_section_discloses_unbuilt_holding_theses():
    from app.report_engine.templates.next_day import render_next_day_sections

    rendered = "\n".join(render_next_day_sections({
        "target_date": "2026-08-06",
        "holdings": [],
        "candidates": [],
        "long_horizon": [{
            "label": "长江电力(600900)",
            "thesis_status": "未建论文",
        }],
    }))

    assert "真实持仓即使未建论文也会显示" in rendered
    assert "未建论文不等于长期看多" in rendered


def test_empty_sentinel_news_is_degraded_not_success():
    from scripts.run_sentinel import _sentinel_result_exit_code

    result = {
        "mode": "news",
        "event_count": 0,
        "fallback_attempted": True,
        "long_horizon_summary": {"status": "success"},
    }

    assert _sentinel_result_exit_code(result, mode="news") == 2


def test_sentinel_fallback_extracts_actionable_market_themes():
    from scripts.run_sentinel import _fallback_news_themes

    assert _fallback_news_themes(
        "人工智能算力基础设施与半导体设备迎来政策催化"
    ) == ["AI算力", "AI半导体"]


def test_realtime_kline_uses_registered_offline_history_after_online_sources_fail():
    from app.data_sources.realtime_market_data import FastRealtimeMarketDataSource

    class EmptyKline:
        async def fetch_kline(self, *_args, **_kwargs):
            return {"bars": [], "source": "online"}

    class OfflineKline:
        async def fetch_kline(self, *_args, **_kwargs):
            return {
                "bars": [{"date": "2026-07-06", "close": 4.28}],
                "source": "offline_minute_archive",
                "data_cutoff": "2026-07-06 15:00:00",
            }

    result = asyncio.run(
        FastRealtimeMarketDataSource(
            quote_source=object(),
            kline_source=EmptyKline(),
            fallback_kline_source=EmptyKline(),
            offline_kline_source=OfflineKline(),
        ).fetch_kline("002131", count=120)
    )

    assert result["source"] == "offline_minute_archive+historical_fallback"
    assert result["bars"][-1]["close"] == 4.28


def test_realtime_kline_recovers_when_online_sources_raise():
    from app.data_sources.realtime_market_data import FastRealtimeMarketDataSource

    class BrokenKline:
        async def fetch_kline(self, *_args, **_kwargs):
            raise RuntimeError("vendor unavailable")

    class OfflineKline:
        async def fetch_kline(self, *_args, **_kwargs):
            return {
                "bars": [{"date": "2026-07-06", "close": 4.28}],
                "source": "offline_minute_archive",
            }

    result = asyncio.run(
        FastRealtimeMarketDataSource(
            quote_source=object(),
            kline_source=BrokenKline(),
            fallback_kline_source=BrokenKline(),
            offline_kline_source=OfflineKline(),
        ).fetch_kline("002131", count=120)
    )

    assert result["source"] == "offline_minute_archive+historical_fallback"
    assert result["history_only"] is True


def test_target_snapshot_consumes_lhb_and_big_deal_evidence():
    from app.services.target_snapshot import build_target_snapshot

    class QuoteSource:
        async def fetch_batch(self, codes):
            return {codes[0]: {"price": 4.24}}

        async def fetch_kline(self, *_args, **_kwargs):
            return {"bars": [{"date": "2026-07-30", "close": 4.24}]}

    class MarketSource:
        async def fetch_fund_flow_individual(self):
            return [{"code": "002131", "net": "1.2亿"}]

        async def fetch_hsgt_flow(self):
            return []

        async def fetch_lhb_stats(self):
            return [{
                "code": "002131",
                "name": "利欧股份",
                "count": "2",
                "net": "3600万",
            }]

        async def fetch_big_deals(self):
            return [{
                "name": "利欧股份",
                "direction": "买盘",
                "amount": "1800",
            }]

    snapshot = asyncio.run(
        build_target_snapshot(
            "002131",
            name="利欧股份",
            quote_source=QuoteSource(),
            market_source=MarketSource(),
        )
    )

    assert snapshot["lhb"]["status"] == "ok"
    assert snapshot["lhb"]["net"] == "3600万"
    assert snapshot["big_deals"]["status"] == "ok"
    assert snapshot["big_deals"]["direction"] == "买盘"


def test_composite_score_uses_lhb_as_confirming_capital_evidence():
    from app.services.composite_score import build_composite_score

    snapshot = {
        "quote": {
            "status": "ok",
            "price": 4.24,
            "change_pct": 3.67,
            "vol_ratio": 4.1,
            "amount_wan": 397_400,
        },
        "kline": {
            "status": "ok",
            "bars": [{"close": 4.0}, {"close": 4.1}, {"close": 4.24}],
        },
        "fund_flow": {"status": "ok", "net": "-100万"},
        "lhb": {"status": "ok", "net": "3600万", "count": "2"},
        "big_deals": {"status": "ok", "direction": "买盘", "amount": "1800"},
        "financial": {"status": "ok", "revenue_yoy_pct": 5},
        "news": {"status": "ok", "items": [{"title": "催化"}]},
        "market_regime": {"status": "ok", "label": "neutral"},
    }

    result = build_composite_score(
        snapshot,
        entry_price=4.24,
        stop_loss=3.98,
        target_price=4.75,
    )

    assert result["components"]["fund_flow"] >= 10
    assert "龙虎榜" in next(
        reason for reason in result["top_reasons"] if "资金" in reason or "龙虎榜" in reason
    )


def test_market_analysis_context_loads_existing_sector_news_lhb_and_big_deals():
    from scripts.daily_report import _load_market_analysis_context

    class MarketClient:
        async def fetch_fund_flow_industry(self):
            return [{"name": "半导体"}]

        async def fetch_fund_flow_concept(self):
            return [{"name": "机器人"}]

        async def fetch_lhb_stats(self):
            return [{"code": "002131"}]

        async def fetch_big_deals(self):
            return [{"name": "利欧股份"}]

    class NewsClient:
        async def fetch_cjzc(self):
            return [{"title": "政策新闻"}]

        async def fetch_global_news(self):
            return [{"title": "市场新闻"}]

    context = asyncio.run(
        _load_market_analysis_context(MarketClient(), NewsClient())
    )

    assert [item["kind"] for item in context["sectors"]] == [
        "industry",
        "concept",
    ]
    assert len(context["news"]) == 2
    assert context["lhb"][0]["code"] == "002131"
    assert context["big_deals"][0]["name"] == "利欧股份"


def test_market_analysis_context_does_not_call_akshare_adapters_concurrently():
    from scripts.daily_report import _load_market_analysis_context

    state = {"active": 0}

    async def guarded(value):
        state["active"] += 1
        try:
            await asyncio.sleep(0)
            if state["active"] > 1:
                raise RuntimeError("adapter_not_thread_safe")
            return value
        finally:
            state["active"] -= 1

    class MarketClient:
        async def fetch_fund_flow_industry(self):
            return await guarded([{"name": "行业"}])

        async def fetch_fund_flow_concept(self):
            return await guarded([{"name": "概念"}])

        async def fetch_lhb_stats(self):
            return await guarded([{"code": "002131"}])

        async def fetch_big_deals(self):
            return await guarded([{"name": "利欧股份"}])

    class NewsClient:
        async def fetch_cjzc(self):
            return await guarded([{"title": "早餐"}])

        async def fetch_global_news(self):
            return await guarded([{"title": "全球"}])

    context = asyncio.run(
        _load_market_analysis_context(MarketClient(), NewsClient())
    )

    assert len(context["sectors"]) == 2
    assert len(context["news"]) == 2
    assert len(context["lhb"]) == 1
    assert len(context["big_deals"]) == 1


def test_market_universe_misses_are_removed_instead_of_reported_as_data_gaps():
    from app.services.opportunity_pipeline import _lifecycle_only_scorecards

    rows = _lifecycle_only_scorecards({
        "metrics": {"scanned_count": 5197},
        "rejected": {
            "priority_missing": [{"code": "301707", "name": "301707"}],
        },
    })

    assert rows == [{
        "code": "301707",
        "name": "301707",
        "score": 0,
        "grade": "C",
        "action": "remove",
        "block_reason": "not_in_market_universe",
        "decision_reason": "今日全市场5197只清单中未找到该代码，按无效或不可交易标的自动剔除",
        "lifecycle_only": True,
        "discovery_source": "target_pool_daily_refresh",
    }]


def test_market_universe_miss_removes_existing_target_immediately(tmp_path):
    from app.services.opportunity_pipeline import apply_scorecard_lifecycle
    from app.services.quant_lifecycle import TargetPoolStore

    store = TargetPoolStore(tmp_path / "candidate_pool.json")
    store.save({
        "version": 1,
        "items": {
            "301707": {
                "code": "301707",
                "name": "旧研究代码",
                "status": "watching",
                "source": "sentinel_serenity",
                "scoring_decision": {"score": 70},
            },
        },
    })

    events = apply_scorecard_lifecycle(
        store,
        [{
            "code": "301707",
            "name": "旧研究代码",
            "score": 0,
            "action": "remove",
            "block_reason": "not_in_market_universe",
            "lifecycle_only": True,
            "discovery_source": "target_pool_daily_refresh",
        }],
        available_cash=1000,
        total_assets=1000,
    )

    assert events[0]["event"] == "removed"
    assert store.load()["items"]["301707"]["status"] == "removed"


def test_budget_blocked_existing_target_becomes_silent_research_reference(tmp_path):
    from app.services.opportunity_pipeline import apply_scorecard_lifecycle
    from app.services.quant_lifecycle import TargetPoolStore

    store = TargetPoolStore(tmp_path / "candidate_pool.json")
    store.save({
        "version": 1,
        "items": {
            "600519": {
                "code": "600519",
                "name": "贵州茅台",
                "status": "watching",
                "source": "dynamic_market_discovery",
                "scoring_decision": {"score": 72},
            },
        },
    })

    events = apply_scorecard_lifecycle(
        store,
        [{
            "code": "600519",
            "name": "贵州茅台",
            "score": 0,
            "action": "watch",
            "block_reason": "lot_size_exceeded",
            "lifecycle_only": True,
            "discovery_source": "target_pool_daily_refresh",
        }],
        available_cash=1383.25,
        total_assets=5000,
    )

    stored = store.load()["items"]["600519"]
    assert stored["status"] == "research_reference"
    assert events[0]["block_reason"] == "lot_size_exceeded"


def test_sentinel_uses_fallback_events_when_primary_archive_is_empty(
    tmp_path,
    monkeypatch,
):
    from scripts import run_sentinel

    fallback_events = [{
        "id": "fallback-1",
        "source": "eastmoney_akshare",
        "channel": "global_market",
        "published_at": "2026-07-31T08:00:00+08:00",
        "fetched_at": "2026-07-31T08:01:00+08:00",
        "content": "半导体设备板块出现新催化",
        "is_key": True,
        "symbols": [],
        "themes": ["AI半导体"],
        "dedupe_key": "fallback-1",
        "raw_hash": "fallback-1",
    }]
    monkeypatch.setattr(
        run_sentinel,
        "import_default_tushare_news_events",
        lambda _date: [],
    )
    monkeypatch.setattr(
        run_sentinel,
        "build_serenity_deep_dives",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        run_sentinel,
        "materialize_serenity_long_horizon",
        lambda *_args, **_kwargs: {
            "status": "success",
            "thesis_count": 0,
            "evidence_count": 0,
            "target_count": 0,
        },
    )

    result = run_sentinel.run_news_job(
        "2026-07-31",
        output_root=tmp_path,
        quote_fetcher=lambda *_: {},
        financial_fetcher=lambda *_: {},
        fallback_news_fetcher=lambda _date: fallback_events,
    )

    assert result["event_count"] == 1
    assert result["fallback_used"] is True


def test_report_renderer_receives_success_after_analysis_stages_complete(
    tmp_path,
):
    from app.services.opportunity_pipeline import OpportunityPipeline
    from app.services.pipeline_run_store import PipelineRunStore

    class Discovery:
        async def fetch_fund_flow_individual(self):
            return [{
                "code": "000001",
                "name": "测试标的",
                "price": 3.0,
                "amount": 200_000_000,
                "net_amount": 20_000_000,
            }]

    async def snapshot(candidate):
        return {
            "code": candidate["code"],
            "name": candidate["name"],
            "quote": {"status": "ok", "price": 3.0},
            "kline": {"status": "ok", "bars": [{"close": 2.8}, {"close": 3.0}]},
            "fund_flow": {"status": "ok", "net": 20_000_000},
            "financial": {"status": "ok", "revenue_yoy_pct": 10},
        }

    rendered_health: list[str] = []

    def render(result):
        rendered_health.append(result["health"]["status"])
        return (
            "## 今日可操作结论\n账户：测试\n000001\n"
            "没有买入建议的原因\n### 今日变化\n### 管线状态\n"
        )

    pipeline = OpportunityPipeline(
        discovery_source=Discovery(),
        snapshot_builder=snapshot,
        scorer=lambda item, **_: {
            "code": item["code"],
            "name": item["name"],
            "score": 61,
            "action": "watch",
        },
        renderer=render,
        run_store=PipelineRunStore(tmp_path / "pipeline.db"),
        artifact_dir=tmp_path / "artifacts",
        sleeper=lambda _: asyncio.sleep(0),
    )

    result = asyncio.run(
        pipeline.run(
            "2026-07-31",
            available_cash=800,
            total_assets=1600,
        )
    )

    assert result["health"]["status"] == "succeeded"
    assert rendered_health == ["succeeded"]
