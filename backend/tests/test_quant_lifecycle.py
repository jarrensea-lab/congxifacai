import pytest

from app.services.quant_lifecycle import (
    CandidatePoolStore,
    PositionWatchStore,
    evaluate_candidate_pool,
    evaluate_position_watch,
    normalize_alert_level,
)


class FakeQuoteSource:
    def __init__(self, quotes):
        self.quotes = quotes

    async def fetch_batch(self, codes):
        return {code: self.quotes[code] for code in codes if code in self.quotes}


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
    assert result["alerts"][0]["suggestion"] == "可试仓，必须人工确认价格和仓位"
    assert store.get("002123")["status"] == "actionable"


def test_position_watch_stop_loss_and_target_emit_alerts(tmp_path):
    store = PositionWatchStore(tmp_path / "position_watch.json")
    store.upsert_plan("002123", "测试持仓", stop_loss_price=3.0, target_price=3.8)

    stop_alerts = evaluate_position_watch(store, {"002123": {"price": 2.95}})
    target_alerts = evaluate_position_watch(store, {"002123": {"price": 3.85}})

    assert stop_alerts[0]["action"] == "stop_loss"
    assert stop_alerts[0]["level"] == "high"
    assert target_alerts[0]["action"] == "take_profit"
    assert target_alerts[0]["level"] == "mid"


def test_alert_level_normalizes_medium_to_mid():
    assert normalize_alert_level("medium") == "mid"
    assert normalize_alert_level("mid") == "mid"
    assert normalize_alert_level("high") == "high"
