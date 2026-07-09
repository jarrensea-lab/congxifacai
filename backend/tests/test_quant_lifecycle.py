import pytest

from app.services.quant_lifecycle import (
    CandidatePoolStore,
    PositionWatchStore,
    TargetPoolStore,
    evaluate_candidate_pool,
    evaluate_position_watch,
    lot_size_for_code,
    normalize_alert_level,
)


class FakeQuoteSource:
    def __init__(self, quotes, klines=None):
        self.quotes = quotes
        self.klines = klines or {}

    async def fetch_batch(self, codes):
        return {code: self.quotes[code] for code in codes if code in self.quotes}

    async def fetch_kline(self, code, period="day", count=20):
        return self.klines.get(code, {"code": code, "period": period, "bars": []})


def test_candidate_pool_keeps_data_insufficient_recommendation_for_followup(tmp_path):
    store = CandidatePoolStore(tmp_path / "candidate_pool.json")

    stored = store.upsert_recommendations(
        [
            {
                "code": "300750",
                "name": "宁德时代",
                "reason": "新能源龙头，但日内买点数据不足",
                "buy_range": "数据不足，建议观望",
                "target": "数据不足，建议观望",
            }
        ],
        source="premarket",
    )

    assert stored == 1
    item = store.get("300750")
    assert item["status"] == "watching"
    assert item["watch_reason"] == "data_insufficient"
    assert item["evidence"]["reason"] == "新能源龙头，但日内买点数据不足"


@pytest.mark.asyncio
async def test_candidate_pool_marks_limit_up_candidate_blocked_but_alerts(tmp_path):
    store = CandidatePoolStore(tmp_path / "candidate_pool.json")
    store.upsert_recommendations([{"code": "000629", "name": "钒钛股份", "reason": "钛白粉题材"}], source="manual")

    result = await evaluate_candidate_pool(
        store,
        FakeQuoteSource(
            {
                "000629": {
                    "price": 3.67,
                    "change_pct": 9.88,
                    "vol_ratio": 3.51,
                    "amount_wan": 109266,
                    "limit_up": 3.67,
                }
            }
        ),
        available_cash=6085.61,
    )

    assert result["alerts"][0]["action"] == "blocked_chasing"
    assert "禁止追高" in result["alerts"][0]["message"]
    assert store.get("000629")["status"] == "blocked_chasing"


@pytest.mark.asyncio
async def test_candidate_pool_marks_affordable_volume_breakout_actionable(tmp_path):
    store = CandidatePoolStore(tmp_path / "candidate_pool.json")
    store.upsert_recommendations([{"code": "002123", "name": "低价突破", "reason": "放量突破"}], source="manual")

    result = await evaluate_candidate_pool(
        store,
        FakeQuoteSource(
            {
                "002123": {
                    "price": 3.2,
                    "change_pct": 4.2,
                    "vol_ratio": 2.6,
                    "amount_wan": 18000,
                }
            }
        ),
        available_cash=6085.61,
    )

    assert result["alerts"][0]["action"] == "actionable"
    assert result["alerts"][0]["playbook"] == "breakout_entry"
    assert result["alerts"][0]["position_shares"] == 300
    assert result["alerts"][0]["risk_amount"] == 48.0
    assert "人工复核后可试仓" in result["alerts"][0]["suggestion"]
    assert store.get("002123")["status"] == "actionable"


@pytest.mark.asyncio
async def test_candidate_pool_blocks_high_position_breakout_after_kline_enrichment(tmp_path):
    store = CandidatePoolStore(tmp_path / "candidate_pool.json")
    store.upsert_recommendations([{"code": "002123", "name": "高位突破", "reason": "放量突破"}], source="manual")
    bars = [
        {"close": 6.0 + idx * 0.04, "high": 6.05 + idx * 0.04, "low": 5.95 + idx * 0.04}
        for idx in range(20)
    ]

    result = await evaluate_candidate_pool(
        store,
        FakeQuoteSource(
            {
                "002123": {
                    "price": 6.82,
                    "change_pct": 4.2,
                    "vol_ratio": 2.6,
                    "amount_wan": 18000,
                }
            },
            {"002123": {"code": "002123", "period": "day", "bars": bars}},
        ),
        available_cash=6085.61,
        total_assets=6085.61,
    )

    assert result["alerts"][0]["action"] == "blocked_high_position"
    assert result["alerts"][0]["playbook"] == "breakout_watch"
    assert "不再按突破追买" in result["alerts"][0]["message"]
    assert store.get("002123")["status"] == "blocked_high_position"


@pytest.mark.asyncio
async def test_candidate_pool_alert_blocks_when_risk_budget_is_too_small(tmp_path):
    store = CandidatePoolStore(tmp_path / "candidate_pool.json")
    store.upsert_recommendations([{"code": "002123", "name": "高波动低价", "reason": "放量突破"}], source="manual")

    result = await evaluate_candidate_pool(
        store,
        FakeQuoteSource(
            {
                "002123": {
                    "price": 13.0,
                    "change_pct": 4.2,
                    "vol_ratio": 2.6,
                    "amount_wan": 18000,
                }
            }
        ),
        available_cash=6085.61,
        total_assets=6085.61,
    )

    assert result["alerts"][0]["action"] == "risk_budget_too_small"
    assert result["alerts"][0]["playbook"] == "breakout_entry"
    assert store.get("002123")["status"] == "risk_budget_too_small"


def test_position_watch_stop_loss_and_target_emit_alerts(tmp_path):
    store = PositionWatchStore(tmp_path / "position_watch.json")
    store.upsert_plan("002123", "测试持仓", stop_loss_price=3.0, target_price=3.8)

    stop_alerts = evaluate_position_watch(store, {"002123": {"price": 2.95}})
    target_alerts = evaluate_position_watch(store, {"002123": {"price": 3.85}})

    assert stop_alerts[0]["action"] == "stop_loss"
    assert stop_alerts[0]["level"] == "high"
    assert target_alerts[0]["action"] == "take_profit"
    assert target_alerts[0]["level"] == "mid"


def test_position_watch_infers_default_take_profit_from_stop_loss(tmp_path):
    store = PositionWatchStore(tmp_path / "position_watch.json")

    store.upsert_plan("600900", "长江电力", stop_loss_price=26.64)

    item = store.get("600900")
    assert item["stop_loss_price"] == 26.64
    assert item["target_price"] == 30.29


@pytest.mark.asyncio
async def test_candidate_pool_distinguishes_existing_position_add_alert(tmp_path):
    store = CandidatePoolStore(tmp_path / "candidate_pool.json")
    store.upsert_recommendations([{"code": "300002", "name": "神州泰岳", "reason": "放量突破"}], source="manual")

    result = await evaluate_candidate_pool(
        store,
        FakeQuoteSource(
            {
                "300002": {
                    "price": 7.8,
                    "change_pct": 4.2,
                    "vol_ratio": 2.6,
                    "amount_wan": 18000,
                }
            }
        ),
        available_cash=6000,
        total_assets=12000,
        positions={"300002": {"shares": 100, "market_value": 780}},
    )

    assert result["alerts"][0]["action"] == "add_position"
    assert "加仓" in result["alerts"][0]["message"]
    assert store.get("300002")["status"] == "add_position"


@pytest.mark.asyncio
async def test_candidate_pool_marks_position_limit_reached_for_add(tmp_path):
    store = CandidatePoolStore(tmp_path / "candidate_pool.json")
    store.upsert_recommendations([{"code": "300002", "name": "神州泰岳", "reason": "放量突破"}], source="manual")

    result = await evaluate_candidate_pool(
        store,
        FakeQuoteSource(
            {
                "300002": {
                    "price": 7.8,
                    "change_pct": 4.2,
                    "vol_ratio": 2.6,
                    "amount_wan": 18000,
                }
            }
        ),
        available_cash=6000,
        total_assets=12000,
        positions={"300002": {"shares": 500, "market_value": 5900}},
    )

    assert result["alerts"][0]["action"] == "position_limit_reached"
    assert "仓位上限" in result["alerts"][0]["message"]


def test_alert_level_normalizes_medium_to_mid():
    assert normalize_alert_level("medium") == "mid"
    assert normalize_alert_level("mid") == "mid"
    assert normalize_alert_level("high") == "high"


def test_lot_size_for_code_respects_board_rules():
    assert lot_size_for_code("600000") == 100
    assert lot_size_for_code("300750") == 100
    assert lot_size_for_code("688008") == 200
    assert lot_size_for_code("838000") == 100


def test_target_pool_accepts_v8_blocking_statuses(tmp_path):
    store = TargetPoolStore(tmp_path / "target_pool.json")

    assert store.upsert_target(code="000725", name="京东方A", status="risk_budget_too_small") is True
    assert store.get("000725")["status"] == "risk_budget_too_small"

    assert store.upsert_target(code="000100", name="TCL科技", status="regime_blocks_dip") is True
    assert store.get("000100")["status"] == "regime_blocks_dip"

    assert store.upsert_target(code="000725", name="京东方A", status="blocked_high_position") is True
    assert store.get("000725")["status"] == "blocked_high_position"


def test_target_pool_long_horizon_states_are_not_intraday_scan_active(tmp_path):
    store = TargetPoolStore(tmp_path / "target_pool.json")

    for code, status in [
        ("000001", "long_research"),
        ("000002", "long_watch"),
        ("000003", "accumulation_zone"),
        ("000004", "tactical_watch"),
        ("000005", "thesis_review"),
        ("000006", "exit_candidate"),
    ]:
        assert store.upsert_target(code=code, name=f"长期{code}", status=status) is True

    assert {item["code"] for item in store.active_items()} == set()
    assert store.get("000003")["status"] == "accumulation_zone"


def test_target_pool_routes_unaffordable_serenity_candidate_to_research_reference(tmp_path):
    store = TargetPoolStore(tmp_path / "target_pool.json")

    ok = store.upsert_target(
        code="688008",
        name="澜起科技",
        status="candidate",
        source="sentinel_serenity",
        evidence_ids=["ev_test"],
        serenity={"score": 62.5},
        available_cash=6085.61,
        total_assets=6085.61,
        current_price=68.5,
    )

    item = store.get("688008")
    assert ok is True
    assert item["status"] == "research_reference"
    assert item["execution"]["lot_size"] == 200
    assert item["execution"]["lot_value"] == 13700.0
    assert item["execution"]["block_reason"] == "lot_size_exceeded"


@pytest.mark.asyncio
async def test_candidate_pool_scans_research_reference_without_action_alert(tmp_path):
    store = TargetPoolStore(tmp_path / "target_pool.json")
    store.upsert_target(
        code="688008",
        name="澜起科技",
        status="research_reference",
        source="sentinel_serenity",
        current_price=68.5,
        available_cash=6085.61,
        total_assets=6085.61,
    )

    result = await evaluate_candidate_pool(
        store,
        FakeQuoteSource({"688008": {"price": 70, "change_pct": 4.0, "vol_ratio": 3.0, "amount_wan": 50000}}),
        available_cash=6085.61,
    )

    assert result["scanned"] == 0
    assert result["alerts"] == []
    assert store.get("688008")["status"] == "research_reference"
