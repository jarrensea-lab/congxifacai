from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from app.services.quant_lifecycle import (
    CandidatePoolStore,
    LONG_HORIZON_STATUSES,
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


def full_score_decision(action="buy"):
    return {
        "action": action,
        "score": 78.5,
        "block_reason": "",
        "decision_reason": "完整数据评分通过",
        "missing_data": [],
        "playbook": "breakout_entry",
        "source_status": {
            "quote": "ok",
            "kline": "ok",
            "fund_flow": "ok",
            "financial": "ok",
        },
        "evaluated_at": "2026-07-20 09:40:00",
    }


def seed_breakout_contract(store, code, price):
    payload = store.load()
    item = payload["items"][code]
    item["trigger_price"] = price
    item["kline"] = {
        "bars": [
            {
                "open": price * 0.95,
                "close": price * 0.98,
                "high": price,
                "low": price * 0.9,
            }
            for _ in range(20)
        ]
    }
    store.save(payload)


async def seed_active_breakout_signal(tmp_path):
    store = TargetPoolStore(tmp_path / "candidate_pool.json")
    store.upsert_target(
        code="002123",
        name="低价突破",
        status="executable",
        source="target_scoring",
        scoring_decision=full_score_decision(),
        current_price=3.2,
        available_cash=6085.61,
        total_assets=6085.61,
    )
    seed_breakout_contract(store, "002123", 3.2)
    quote_source = FakeQuoteSource(
        {
            "002123": {
                "price": 3.2,
                "change_pct": 4.2,
                "vol_ratio": 2.6,
                "amount_wan": 18000,
                "quote_timestamp": "2026-07-20T09:45:00+08:00",
                "freshness": "fresh",
            }
        }
    )
    await evaluate_candidate_pool(
        store, quote_source, available_cash=6085.61, total_assets=6085.61
    )
    quote_source.quotes["002123"]["quote_timestamp"] = "2026-07-20T09:50:00+08:00"
    await evaluate_candidate_pool(
        store, quote_source, available_cash=6085.61, total_assets=6085.61
    )
    return store, quote_source


def blocked_entry_gate():
    return {
        "report_date": "2026-07-20",
        "target_date": "2026-07-21",
        "generated_at": "2026-07-20T20:00:00+08:00",
        "entry_allowed": False,
        "reasons": ["main_report_entry_not_triggered"],
        "state": "blocked",
    }


@pytest.mark.asyncio
async def test_blocked_visible_gate_never_creates_pending_or_active_signal(tmp_path):
    store = TargetPoolStore(tmp_path / "candidate_pool.json")
    store.upsert_target(
        code="002123",
        name="低价突破",
        status="executable",
        source="target_scoring",
        scoring_decision=full_score_decision(),
        current_price=3.2,
        available_cash=6085.61,
        total_assets=6085.61,
    )
    seed_breakout_contract(store, "002123", 3.2)
    quote_source = FakeQuoteSource(
        {
            "002123": {
                "price": 3.2,
                "change_pct": 4.2,
                "vol_ratio": 2.6,
                "amount_wan": 18000,
                "quote_timestamp": "2026-07-20T09:45:00+08:00",
                "freshness": "fresh",
            }
        }
    )

    first = await evaluate_candidate_pool(
        store,
        quote_source,
        available_cash=6085.61,
        total_assets=6085.61,
        entry_gate=blocked_entry_gate(),
    )
    quote_source.quotes["002123"]["quote_timestamp"] = "2026-07-20T09:50:00+08:00"
    second = await evaluate_candidate_pool(
        store,
        quote_source,
        available_cash=6085.61,
        total_assets=6085.61,
        entry_gate=blocked_entry_gate(),
    )

    assert first["alerts"] == second["alerts"] == []
    signal = store.get("002123")["entry_signal"]
    assert signal["state"] == "decision_gate_blocked"
    assert signal["reason"] == "visible_decision_gate_blocked"


@pytest.mark.asyncio
async def test_stale_yitaojin_validation_blocks_entry_signal(tmp_path):
    """Catches lifecycle bypassing the structured broker quote validation."""
    store = TargetPoolStore(tmp_path / "candidate_pool.json")
    store.upsert_target(
        code="002123",
        name="低价突破",
        status="executable",
        source="target_scoring",
        scoring_decision=full_score_decision(),
        current_price=3.2,
        available_cash=6085.61,
        total_assets=6085.61,
    )
    seed_breakout_contract(store, "002123", 3.2)
    quote_source = FakeQuoteSource(
        {
            "002123": {
                "price": 3.2,
                "change_pct": 4.2,
                "vol_ratio": 2.6,
                "amount_wan": 18000,
                "quote_timestamp": "2026-07-20T09:45:00+08:00",
                "freshness": "fresh",
            }
        }
    )

    result = await evaluate_candidate_pool(
        store,
        quote_source,
        available_cash=6085.61,
        total_assets=6085.61,
        quote_validations={
            "002123": {
                "code": "002123",
                "status": "stale",
                "blocks_new_entry": True,
                "requires_manual_price_check": True,
                "reasons": ["quote_stale"],
            }
        },
    )

    assert result["alerts"] == []
    signal = store.get("002123")["entry_signal"]
    assert signal["state"] == "quote_validation_blocked"
    assert signal["reason"] == "yitaojin_quote_stale"


@pytest.mark.asyncio
async def test_blocked_visible_gate_cancels_pending_before_second_confirmation(
    tmp_path,
):
    store = TargetPoolStore(tmp_path / "candidate_pool.json")
    store.upsert_target(
        code="002123",
        name="低价突破",
        status="executable",
        source="target_scoring",
        scoring_decision=full_score_decision(),
        current_price=3.2,
        available_cash=6085.61,
        total_assets=6085.61,
    )
    seed_breakout_contract(store, "002123", 3.2)
    quote_source = FakeQuoteSource(
        {
            "002123": {
                "price": 3.2,
                "change_pct": 4.2,
                "vol_ratio": 2.6,
                "amount_wan": 18000,
                "quote_timestamp": "2026-07-20T09:45:00+08:00",
                "freshness": "fresh",
            }
        }
    )

    await evaluate_candidate_pool(
        store,
        quote_source,
        available_cash=6085.61,
        total_assets=6085.61,
    )
    pending_signal_id = store.get("002123")["entry_signal"]["signal_id"]
    quote_source.quotes["002123"]["quote_timestamp"] = "2026-07-20T09:50:00+08:00"
    blocked = await evaluate_candidate_pool(
        store,
        quote_source,
        available_cash=6085.61,
        total_assets=6085.61,
        entry_gate=blocked_entry_gate(),
    )

    assert blocked["alerts"] == []
    signal = store.get("002123")["entry_signal"]
    assert signal["state"] == "cancelled"
    assert signal["reason"] == "visible_decision_gate_blocked"
    assert signal["signal_id"] == pending_signal_id


@pytest.mark.asyncio
async def test_blocked_visible_gate_cancels_active_even_with_stale_quote(tmp_path):
    store, quote_source = await seed_active_breakout_signal(tmp_path)
    active_signal_id = store.get("002123")["entry_signal"]["signal_id"]
    quote_source.quotes["002123"].update(
        quote_timestamp="2026-07-20T09:55:00+08:00",
        freshness="stale",
    )

    result = await evaluate_candidate_pool(
        store,
        quote_source,
        available_cash=6085.61,
        total_assets=6085.61,
        entry_gate=blocked_entry_gate(),
    )

    assert result["alerts"][0]["action"] == "entry_cancelled"
    assert result["alerts"][0]["signal_id"] == active_signal_id
    signal = store.get("002123")["entry_signal"]
    assert signal["state"] == "cancelled"
    assert signal["reason"] == "visible_decision_gate_blocked"
    assert signal["signal_id"] == active_signal_id


@pytest.mark.asyncio
async def test_dip_entry_uses_stable_setup_price_across_small_quote_moves(tmp_path):
    store = TargetPoolStore(tmp_path / "candidate_pool.json")
    store.upsert_target(
        code="002123",
        name="回踩低吸",
        status="executable",
        source="target_scoring",
        scoring_decision=full_score_decision(),
        current_price=9.9,
        available_cash=6085.61,
        total_assets=6085.61,
    )
    payload = store.load()
    payload["items"]["002123"]["kline"] = {
        "bars": [
            {"open": 10.0, "close": 10.0, "high": 10.3, "low": 9.8} for _ in range(5)
        ]
    }
    store.save(payload)
    quote_source = FakeQuoteSource(
        {
            "002123": {
                "price": 9.90,
                "change_pct": 0.5,
                "vol_ratio": 1.2,
                "amount_wan": 6000,
                "quote_timestamp": "2026-07-20T09:45:00+08:00",
                "freshness": "fresh",
            }
        }
    )

    first = await evaluate_candidate_pool(
        store, quote_source, available_cash=6085.61, total_assets=6085.61
    )
    pending = store.get("002123")["entry_signal"]
    quote_source.quotes["002123"].update(
        price=9.91,
        quote_timestamp="2026-07-20T09:50:00+08:00",
    )
    second = await evaluate_candidate_pool(
        store, quote_source, available_cash=6085.61, total_assets=6085.61
    )

    assert first["alerts"] == []
    assert second["alerts"][0]["action"] == "actionable"
    assert pending["setup_price"] == 9.8
    assert store.get("002123")["entry_signal"]["signal_id"] == pending["signal_id"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "second_timestamp", ["2026-07-20T09:44:00+08:00", "not-a-time"]
)
async def test_entry_confirmation_requires_strictly_increasing_observation_time(
    tmp_path, second_timestamp
):
    store = TargetPoolStore(tmp_path / "candidate_pool.json")
    store.upsert_target(
        code="002123",
        name="低价突破",
        status="executable",
        source="target_scoring",
        scoring_decision=full_score_decision(),
        current_price=3.2,
        available_cash=6085.61,
        total_assets=6085.61,
    )
    seed_breakout_contract(store, "002123", 3.2)
    quote_source = FakeQuoteSource(
        {
            "002123": {
                "price": 3.2,
                "change_pct": 4.2,
                "vol_ratio": 2.6,
                "amount_wan": 18000,
                "quote_timestamp": "2026-07-20T09:45:00+08:00",
                "freshness": "fresh",
            }
        }
    )
    await evaluate_candidate_pool(
        store, quote_source, available_cash=6085.61, total_assets=6085.61
    )

    quote_source.quotes["002123"]["quote_timestamp"] = second_timestamp
    result = await evaluate_candidate_pool(
        store, quote_source, available_cash=6085.61, total_assets=6085.61
    )

    assert result["alerts"] == []
    assert store.get("002123")["entry_signal"]["confirmations"] <= 1


@pytest.mark.asyncio
@pytest.mark.parametrize("freshness", ["", "unknown"])
async def test_entry_confirmation_requires_explicit_fresh_quote(tmp_path, freshness):
    store = TargetPoolStore(tmp_path / "candidate_pool.json")
    store.upsert_target(
        code="002123",
        name="低价突破",
        status="executable",
        source="target_scoring",
        scoring_decision=full_score_decision(),
        current_price=3.2,
        available_cash=6085.61,
        total_assets=6085.61,
    )
    seed_breakout_contract(store, "002123", 3.2)

    result = await evaluate_candidate_pool(
        store,
        FakeQuoteSource(
            {
                "002123": {
                    "price": 3.2,
                    "change_pct": 4.2,
                    "vol_ratio": 2.6,
                    "amount_wan": 18000,
                    "quote_timestamp": "2026-07-20T09:45:00+08:00",
                    "freshness": freshness,
                }
            }
        ),
        available_cash=6085.61,
        total_assets=6085.61,
    )

    assert result["alerts"] == []
    assert store.get("002123")["entry_signal"]["state"] == "data_unavailable"


@pytest.mark.asyncio
async def test_mixed_naive_and_aware_signal_times_compare_in_one_timezone(tmp_path):
    store, quote_source = await seed_active_breakout_signal(tmp_path)
    payload = store.load()
    payload["items"]["002123"]["entry_signal"]["expires_at"] = "2026-07-20T09:51:00"
    store.save(payload)

    quote_source.quotes["002123"]["quote_timestamp"] = "2026-07-20T09:55:00+08:00"
    result = await evaluate_candidate_pool(
        store, quote_source, available_cash=6085.61, total_assets=6085.61
    )

    assert result["alerts"][0]["action"] == "entry_cancelled"
    assert store.get("002123")["entry_signal"]["reason"] == "signal_expired"


@pytest.mark.asyncio
async def test_active_signal_with_invalid_legacy_expiry_fails_closed(tmp_path):
    store, quote_source = await seed_active_breakout_signal(tmp_path)
    payload = store.load()
    payload["items"]["002123"]["entry_signal"]["expires_at"] = "invalid-legacy-time"
    store.save(payload)

    quote_source.quotes["002123"]["quote_timestamp"] = "2026-07-20T09:55:00+08:00"
    result = await evaluate_candidate_pool(
        store, quote_source, available_cash=6085.61, total_assets=6085.61
    )

    assert result["alerts"][0]["action"] == "entry_cancelled"
    assert store.get("002123")["entry_signal"]["reason"] == "signal_time_invalid"


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


def test_candidate_pool_record_scan_preserves_concurrent_updates(tmp_path):
    path = tmp_path / "candidate_pool.json"
    CandidatePoolStore(path).upsert_recommendations(
        [{"code": "002131", "name": "利欧股份", "reason": "test"}],
        source="manual",
    )
    worker_count = 16
    start = Barrier(worker_count)

    def record_once(index):
        start.wait()
        CandidatePoolStore(path).record_scan(
            "002131",
            alert={"action": "blocked_chasing", "scan_id": f"scan-{index}"},
        )

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        list(executor.map(record_once, range(worker_count)))

    history = CandidatePoolStore(path).get("002131")["decision_history"]
    assert {item["scan_id"] for item in history} == {
        f"scan-{index}" for index in range(worker_count)
    }


@pytest.mark.asyncio
async def test_candidate_pool_marks_limit_up_candidate_blocked_but_alerts(tmp_path):
    store = CandidatePoolStore(tmp_path / "candidate_pool.json")
    store.upsert_recommendations(
        [{"code": "000629", "name": "钒钛股份", "reason": "钛白粉题材"}],
        source="manual",
    )

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
                    "freshness": "fresh",
                }
            }
        ),
        available_cash=6085.61,
    )

    assert result["alerts"][0]["action"] == "blocked_chasing"
    assert "禁止追高" in result["alerts"][0]["message"]
    assert store.get("000629")["status"] == "blocked_chasing"


@pytest.mark.asyncio
async def test_watching_candidate_cannot_emit_actionable_entry(tmp_path):
    store = CandidatePoolStore(tmp_path / "candidate_pool.json")
    store.upsert_recommendations(
        [{"code": "002123", "name": "低价突破", "reason": "放量突破"}], source="manual"
    )
    seed_breakout_contract(store, "002123", 3.2)

    result = await evaluate_candidate_pool(
        store,
        FakeQuoteSource(
            {
                "002123": {
                    "price": 3.2,
                    "change_pct": 4.2,
                    "vol_ratio": 2.6,
                    "amount_wan": 18000,
                    "freshness": "fresh",
                }
            }
        ),
        available_cash=6085.61,
    )

    assert result["alerts"] == []
    item = store.get("002123")
    assert item["status"] == "watching"
    assert item["entry_signal"]["state"] == "authorization_required"
    assert item["entry_signal"]["reason"] == "full_score_executable_required"


@pytest.mark.asyncio
async def test_lio_incident_snapshot_cannot_bypass_full_score_authorization(tmp_path):
    store = CandidatePoolStore(tmp_path / "candidate_pool.json")
    store.upsert_recommendations(
        [{"code": "002131", "name": "利欧股份", "reason": "AI营销/低价高波动"}],
        source="small_account_discovery",
    )
    seed_breakout_contract(store, "002131", 3.97)

    result = await evaluate_candidate_pool(
        store,
        FakeQuoteSource(
            {
                "002131": {
                    "price": 3.97,
                    "change_pct": 3.39,
                    "vol_ratio": 3.33,
                    "amount_wan": 32688,
                    "quote_timestamp": "2026-07-20T09:45:00+08:00",
                    "freshness": "fresh",
                }
            }
        ),
        available_cash=5902.5,
        total_assets=5902.5,
    )

    assert result["alerts"] == []
    item = store.get("002131")
    assert item["status"] == "watching"
    assert item["entry_signal"]["state"] == "authorization_required"


@pytest.mark.asyncio
async def test_candidate_pool_honors_evidence_trigger_price(tmp_path):
    store = TargetPoolStore(tmp_path / "candidate_pool.json")
    store.upsert_target(
        code="002131",
        name="利欧股份",
        status="executable",
        source="target_scoring",
        evidence={"trigger_price": 4.52},
        scoring_decision=full_score_decision(),
        current_price=3.97,
        available_cash=5902.5,
        total_assets=5902.5,
    )
    payload = store.load()
    payload["items"]["002131"]["trigger_price"] = "0"
    payload["items"]["002131"]["kline"] = {
        "bars": [
            {"open": 3.8, "close": 3.9, "high": 4.2, "low": 3.6} for _ in range(20)
        ]
    }
    store.save(payload)

    result = await evaluate_candidate_pool(
        store,
        FakeQuoteSource(
            {
                "002131": {
                    "price": 3.97,
                    "change_pct": 3.39,
                    "vol_ratio": 3.33,
                    "amount_wan": 32688,
                    "freshness": "fresh",
                }
            }
        ),
        available_cash=5902.5,
        total_assets=5902.5,
    )

    assert result["alerts"] == []
    assert "entry_signal" not in store.get("002131")


@pytest.mark.asyncio
async def test_candidate_pool_treats_non_dict_evidence_as_missing_trigger(tmp_path):
    store = TargetPoolStore(tmp_path / "candidate_pool.json")
    store.upsert_target(
        code="002131",
        name="利欧股份",
        status="executable",
        source="target_scoring",
        evidence={"trigger_price": 4.0},
        scoring_decision=full_score_decision(),
        current_price=4.2,
        available_cash=5902.5,
        total_assets=5902.5,
    )
    payload = store.load()
    item = payload["items"]["002131"]
    item["trigger_price"] = "invalid"
    item["evidence"] = "invalid-evidence"
    item["kline"] = {
        "bars": [
            {"open": 3.8, "close": 3.9, "high": 4.2, "low": 3.6} for _ in range(20)
        ]
    }
    store.save(payload)

    result = await evaluate_candidate_pool(
        store,
        FakeQuoteSource(
            {
                "002131": {
                    "price": 4.2,
                    "change_pct": 4.2,
                    "vol_ratio": 2.6,
                    "amount_wan": 18000,
                    "freshness": "fresh",
                }
            }
        ),
        available_cash=5902.5,
        total_assets=5902.5,
    )

    assert result["alerts"] == []
    assert "entry_signal" not in store.get("002131")


@pytest.mark.asyncio
async def test_target_pool_normalizes_dirty_evidence_before_upsert_and_trigger_scan(
    tmp_path,
):
    store = TargetPoolStore(tmp_path / "candidate_pool.json")
    store.upsert_target(
        code="002131",
        name="利欧股份",
        status="executable",
        source="target_scoring",
        evidence={"trigger_price": 4.0},
        scoring_decision=full_score_decision(),
        current_price=3.97,
        available_cash=5902.5,
        total_assets=5902.5,
    )
    payload = store.load()
    payload["items"]["002131"]["evidence"] = "dirty-existing-evidence"
    store.save(payload)

    assert (
        store.upsert_target(
            code="002131",
            name="利欧股份",
            status="executable",
            source="target_scoring",
            evidence="dirty-incoming-evidence",
            scoring_decision=full_score_decision(),
            current_price=3.97,
            available_cash=5902.5,
            total_assets=5902.5,
        )
        is True
    )
    assert store.get("002131")["evidence"] == {}

    store.upsert_target(
        code="002131",
        name="利欧股份",
        status="executable",
        source="target_scoring",
        evidence={"trigger_price": 4.52},
        scoring_decision=full_score_decision(),
        current_price=3.97,
        available_cash=5902.5,
        total_assets=5902.5,
    )
    payload = store.load()
    payload["items"]["002131"]["kline"] = {
        "bars": [
            {"open": 3.8, "close": 3.9, "high": 4.2, "low": 3.6} for _ in range(20)
        ]
    }
    store.save(payload)

    result = await evaluate_candidate_pool(
        store,
        FakeQuoteSource(
            {
                "002131": {
                    "price": 3.97,
                    "change_pct": 3.39,
                    "vol_ratio": 3.33,
                    "amount_wan": 32688,
                    "freshness": "fresh",
                }
            }
        ),
        available_cash=5902.5,
        total_assets=5902.5,
    )

    assert store.get("002131")["evidence"]["trigger_price"] == 4.52
    assert result["alerts"] == []
    assert "entry_signal" not in store.get("002131")


@pytest.mark.asyncio
async def test_executable_candidate_requires_two_consecutive_confirmations(tmp_path):
    store = TargetPoolStore(tmp_path / "candidate_pool.json")
    store.upsert_target(
        code="002123",
        name="低价突破",
        status="executable",
        source="target_scoring",
        scoring_decision=full_score_decision(),
        current_price=3.2,
        available_cash=6085.61,
        total_assets=6085.61,
    )
    seed_breakout_contract(store, "002123", 3.2)
    quote_source = FakeQuoteSource(
        {
            "002123": {
                "price": 3.2,
                "change_pct": 4.2,
                "vol_ratio": 2.6,
                "amount_wan": 18000,
                "quote_timestamp": "2026-07-20T09:45:00+08:00",
                "freshness": "fresh",
            }
        }
    )

    first = await evaluate_candidate_pool(
        store,
        quote_source,
        available_cash=6085.61,
        total_assets=6085.61,
    )
    pending = store.get("002123")

    assert first["alerts"] == []
    assert pending["status"] == "executable"
    assert pending["entry_signal"]["state"] == "pending"
    assert pending["entry_signal"]["confirmations"] == 1

    duplicate = await evaluate_candidate_pool(
        store,
        quote_source,
        available_cash=6085.61,
        total_assets=6085.61,
    )

    assert duplicate["alerts"] == []
    assert store.get("002123")["entry_signal"]["confirmations"] == 1

    quote_source.quotes["002123"]["quote_timestamp"] = "2026-07-20T09:50:00+08:00"
    second = await evaluate_candidate_pool(
        store,
        quote_source,
        available_cash=6085.61,
        total_assets=6085.61,
    )

    assert second["alerts"][0]["action"] == "actionable"
    assert second["alerts"][0]["playbook"] == "breakout_entry"
    assert second["alerts"][0]["stop_loss"] == 2.88
    assert second["alerts"][0]["target_price"] == 3.84
    assert second["alerts"][0]["risk_budget"] == 121.71
    assert second["alerts"][0]["position_shares"] == 300
    assert second["alerts"][0]["risk_amount"] == 96.0
    assert "人工复核后可试仓" in second["alerts"][0]["suggestion"]
    assert "完整评分78.5" in second["alerts"][0]["decision_basis"]
    assert "连续2次确认" in second["alerts"][0]["decision_basis"]
    assert second["alerts"][0]["scorecard"]["source_status"]["fund_flow"] == "ok"
    assert second["alerts"][0]["recommendation_id"].startswith("rec_")
    active = store.get("002123")
    assert active["status"] == "executable"
    assert active["entry_signal"]["state"] == "active"
    assert active["entry_signal"]["confirmations"] == 2
    from app.services.execution_ledger import ExecutionLedger

    audited = ExecutionLedger(
        tmp_path / "execution_ledger.jsonl"
    ).read_with_diagnostics()
    assert audited["ok"] is True
    assert [event["event_type"] for event in audited["events"]] == [
        "signal",
        "authorization",
        "recommendation",
    ]


@pytest.mark.asyncio
async def test_active_entry_signal_has_stable_identity_and_only_notifies_once(tmp_path):
    store = TargetPoolStore(tmp_path / "candidate_pool.json")
    store.upsert_target(
        code="002123",
        name="低价突破",
        status="executable",
        source="target_scoring",
        scoring_decision=full_score_decision(),
        current_price=3.2,
        available_cash=6085.61,
        total_assets=6085.61,
    )
    seed_breakout_contract(store, "002123", 3.2)
    quote_source = FakeQuoteSource(
        {
            "002123": {
                "price": 3.2,
                "change_pct": 4.2,
                "vol_ratio": 2.6,
                "amount_wan": 18000,
                "quote_timestamp": "2026-07-20T09:45:00+08:00",
                "freshness": "fresh",
            }
        }
    )

    await evaluate_candidate_pool(
        store, quote_source, available_cash=6085.61, total_assets=6085.61
    )
    pending_signal = store.get("002123")["entry_signal"]
    quote_source.quotes["002123"]["quote_timestamp"] = "2026-07-20T09:50:00+08:00"
    confirmed = await evaluate_candidate_pool(
        store, quote_source, available_cash=6085.61, total_assets=6085.61
    )
    active_signal = store.get("002123")["entry_signal"]
    quote_source.quotes["002123"]["quote_timestamp"] = "2026-07-20T09:55:00+08:00"
    repeated = await evaluate_candidate_pool(
        store, quote_source, available_cash=6085.61, total_assets=6085.61
    )
    observed_signal = store.get("002123")["entry_signal"]

    assert confirmed["alerts"][0]["signal_id"] == active_signal["signal_id"]
    assert (
        pending_signal["signal_id"]
        == active_signal["signal_id"]
        == observed_signal["signal_id"]
    )
    assert repeated["alerts"] == []
    assert active_signal["first_scan_id"] == "2026-07-20T09:45:00+08:00"
    assert active_signal["created_at"] == "2026-07-20T09:45:00+08:00"
    assert active_signal["observed_at"] == "2026-07-20T09:50:00+08:00"
    assert active_signal["expires_at"]
    assert observed_signal["observed_at"] == "2026-07-20T09:55:00+08:00"


@pytest.mark.asyncio
async def test_expired_active_entry_signal_is_cancelled_even_if_trigger_remains(
    tmp_path,
):
    store = TargetPoolStore(tmp_path / "candidate_pool.json")
    store.upsert_target(
        code="002123",
        name="低价突破",
        status="executable",
        source="target_scoring",
        scoring_decision=full_score_decision(),
        current_price=3.2,
        available_cash=6085.61,
        total_assets=6085.61,
    )
    seed_breakout_contract(store, "002123", 3.2)
    quote_source = FakeQuoteSource(
        {
            "002123": {
                "price": 3.2,
                "change_pct": 4.2,
                "vol_ratio": 2.6,
                "amount_wan": 18000,
                "quote_timestamp": "2026-07-20T09:45:00+08:00",
                "freshness": "fresh",
            }
        }
    )
    await evaluate_candidate_pool(
        store, quote_source, available_cash=6085.61, total_assets=6085.61
    )
    quote_source.quotes["002123"]["quote_timestamp"] = "2026-07-20T09:50:00+08:00"
    await evaluate_candidate_pool(
        store, quote_source, available_cash=6085.61, total_assets=6085.61
    )
    active_signal = store.get("002123")["entry_signal"]
    payload = store.load()
    payload["items"]["002123"]["entry_signal"]["expires_at"] = (
        "2026-07-20T09:51:00+08:00"
    )
    store.save(payload)

    quote_source.quotes["002123"]["quote_timestamp"] = "2026-07-20T09:55:00+08:00"
    expired = await evaluate_candidate_pool(
        store, quote_source, available_cash=6085.61, total_assets=6085.61
    )

    assert expired["alerts"][0]["action"] == "entry_cancelled"
    assert expired["alerts"][0]["signal_id"] == active_signal["signal_id"]
    assert store.get("002123")["entry_signal"]["state"] == "cancelled"
    assert store.get("002123")["entry_signal"]["reason"] == "signal_expired"


@pytest.mark.asyncio
async def test_active_entry_signal_emits_cancellation_when_trigger_disappears(tmp_path):
    store = TargetPoolStore(tmp_path / "candidate_pool.json")
    store.upsert_target(
        code="002123",
        name="低价突破",
        status="executable",
        source="target_scoring",
        scoring_decision=full_score_decision(),
        current_price=3.2,
        available_cash=6085.61,
        total_assets=6085.61,
    )
    seed_breakout_contract(store, "002123", 3.2)
    triggered = FakeQuoteSource(
        {
            "002123": {
                "price": 3.2,
                "change_pct": 4.2,
                "vol_ratio": 2.6,
                "amount_wan": 18000,
                "quote_timestamp": "2026-07-20T09:45:00+08:00",
                "freshness": "fresh",
            }
        }
    )
    await evaluate_candidate_pool(
        store, triggered, available_cash=6085.61, total_assets=6085.61
    )
    triggered.quotes["002123"]["quote_timestamp"] = "2026-07-20T09:50:00+08:00"
    await evaluate_candidate_pool(
        store, triggered, available_cash=6085.61, total_assets=6085.61
    )

    cancelled = await evaluate_candidate_pool(
        store,
        FakeQuoteSource(
            {
                "002123": {
                    "price": 3.1,
                    "change_pct": 2.4,
                    "vol_ratio": 1.5,
                    "amount_wan": 9000,
                    "quote_timestamp": "2026-07-20T09:55:00+08:00",
                    "freshness": "fresh",
                }
            }
        ),
        available_cash=6085.61,
        total_assets=6085.61,
    )

    assert cancelled["alerts"][0]["action"] == "entry_cancelled"
    assert "买入信号已失效" in cancelled["alerts"][0]["message"]
    item = store.get("002123")
    assert item["status"] == "executable"
    assert item["entry_signal"]["state"] == "cancelled"
    assert item["entry_signal"]["reason"] == "trigger_lost"


@pytest.mark.asyncio
async def test_active_entry_signal_is_cancelled_when_playbook_changes(tmp_path):
    store = TargetPoolStore(tmp_path / "candidate_pool.json")
    store.upsert_target(
        code="002123",
        name="低价突破",
        status="executable",
        source="target_scoring",
        scoring_decision=full_score_decision(),
        current_price=3.2,
        available_cash=6085.61,
        total_assets=6085.61,
    )
    seed_breakout_contract(store, "002123", 3.2)
    quote_source = FakeQuoteSource(
        {
            "002123": {
                "price": 3.2,
                "change_pct": 4.2,
                "vol_ratio": 2.6,
                "amount_wan": 18000,
                "quote_timestamp": "2026-07-20T09:45:00+08:00",
                "freshness": "fresh",
            }
        }
    )
    await evaluate_candidate_pool(
        store, quote_source, available_cash=6085.61, total_assets=6085.61
    )
    quote_source.quotes["002123"]["quote_timestamp"] = "2026-07-20T09:50:00+08:00"
    await evaluate_candidate_pool(
        store, quote_source, available_cash=6085.61, total_assets=6085.61
    )

    quote_source.quotes["002123"] = {
        "price": 3.0,
        "change_pct": 0.5,
        "vol_ratio": 1.2,
        "amount_wan": 6000,
        "quote_timestamp": "2026-07-20T09:55:00+08:00",
        "freshness": "fresh",
    }
    changed = await evaluate_candidate_pool(
        store, quote_source, available_cash=6085.61, total_assets=6085.61
    )

    assert changed["alerts"][0]["action"] == "entry_cancelled"
    assert store.get("002123")["entry_signal"]["reason"] == "playbook_changed"


@pytest.mark.asyncio
async def test_active_entry_signal_is_cancelled_when_authorization_is_lost(tmp_path):
    store = TargetPoolStore(tmp_path / "candidate_pool.json")
    store.upsert_target(
        code="002123",
        name="低价突破",
        status="executable",
        source="target_scoring",
        scoring_decision=full_score_decision(),
        current_price=3.2,
        available_cash=6085.61,
        total_assets=6085.61,
    )
    seed_breakout_contract(store, "002123", 3.2)
    quote_source = FakeQuoteSource(
        {
            "002123": {
                "price": 3.2,
                "change_pct": 4.2,
                "vol_ratio": 2.6,
                "amount_wan": 18000,
                "quote_timestamp": "2026-07-20T09:45:00+08:00",
                "freshness": "fresh",
            }
        }
    )
    await evaluate_candidate_pool(
        store, quote_source, available_cash=6085.61, total_assets=6085.61
    )
    quote_source.quotes["002123"]["quote_timestamp"] = "2026-07-20T09:50:00+08:00"
    await evaluate_candidate_pool(
        store, quote_source, available_cash=6085.61, total_assets=6085.61
    )
    payload = store.load()
    payload["items"]["002123"]["status"] = "watching"
    store.save(payload)

    quote_source.quotes["002123"]["quote_timestamp"] = "2026-07-20T09:55:00+08:00"
    cancelled = await evaluate_candidate_pool(
        store, quote_source, available_cash=6085.61, total_assets=6085.61
    )

    assert cancelled["alerts"][0]["action"] == "entry_cancelled"
    assert store.get("002123")["entry_signal"]["reason"] == "authorization_lost"


@pytest.mark.asyncio
async def test_authorization_loss_cancels_active_signal_even_with_stale_quote(tmp_path):
    store, quote_source = await seed_active_breakout_signal(tmp_path)
    active_signal_id = store.get("002123")["entry_signal"]["signal_id"]
    payload = store.load()
    payload["items"]["002123"]["status"] = "watching"
    store.save(payload)
    quote_source.quotes["002123"].update(
        quote_timestamp="2026-07-20T09:55:00+08:00",
        freshness="stale",
    )

    result = await evaluate_candidate_pool(
        store, quote_source, available_cash=6085.61, total_assets=6085.61
    )

    assert result["alerts"][0]["action"] == "entry_cancelled"
    assert result["alerts"][0]["signal_id"] == active_signal_id
    signal = store.get("002123")["entry_signal"]
    assert signal["state"] == "cancelled"
    assert signal["reason"] == "authorization_lost"
    assert signal["signal_id"] == active_signal_id


@pytest.mark.asyncio
async def test_active_entry_signal_is_cancelled_when_trigger_price_changes(tmp_path):
    store = TargetPoolStore(tmp_path / "candidate_pool.json")
    store.upsert_target(
        code="002123",
        name="低价突破",
        status="executable",
        source="target_scoring",
        scoring_decision=full_score_decision(),
        current_price=3.2,
        available_cash=6085.61,
        total_assets=6085.61,
    )
    seed_breakout_contract(store, "002123", 3.2)
    quote_source = FakeQuoteSource(
        {
            "002123": {
                "price": 3.2,
                "change_pct": 4.2,
                "vol_ratio": 2.6,
                "amount_wan": 18000,
                "quote_timestamp": "2026-07-20T09:45:00+08:00",
                "freshness": "fresh",
            }
        }
    )
    await evaluate_candidate_pool(
        store, quote_source, available_cash=6085.61, total_assets=6085.61
    )
    quote_source.quotes["002123"]["quote_timestamp"] = "2026-07-20T09:50:00+08:00"
    await evaluate_candidate_pool(
        store, quote_source, available_cash=6085.61, total_assets=6085.61
    )
    active_signal_id = store.get("002123")["entry_signal"]["signal_id"]
    payload = store.load()
    payload["items"]["002123"]["trigger_price"] = 3.1
    store.save(payload)

    quote_source.quotes["002123"]["quote_timestamp"] = "2026-07-20T09:55:00+08:00"
    cancelled = await evaluate_candidate_pool(
        store, quote_source, available_cash=6085.61, total_assets=6085.61
    )

    assert cancelled["alerts"][0]["action"] == "entry_cancelled"
    assert cancelled["alerts"][0]["signal_id"] == active_signal_id
    signal = store.get("002123")["entry_signal"]
    assert signal["state"] == "cancelled"
    assert signal["reason"] == "trigger_changed"
    assert signal["signal_id"] == active_signal_id


@pytest.mark.asyncio
async def test_active_entry_signal_is_cancelled_when_price_becomes_chasing(tmp_path):
    store = TargetPoolStore(tmp_path / "candidate_pool.json")
    store.upsert_target(
        code="002123",
        name="低价突破",
        status="executable",
        source="target_scoring",
        scoring_decision=full_score_decision(),
        current_price=3.2,
        available_cash=6085.61,
        total_assets=6085.61,
    )
    seed_breakout_contract(store, "002123", 3.2)
    quote_source = FakeQuoteSource(
        {
            "002123": {
                "price": 3.2,
                "change_pct": 4.2,
                "vol_ratio": 2.6,
                "amount_wan": 18000,
                "quote_timestamp": "2026-07-20T09:45:00+08:00",
                "freshness": "fresh",
            }
        }
    )
    await evaluate_candidate_pool(
        store, quote_source, available_cash=6085.61, total_assets=6085.61
    )
    quote_source.quotes["002123"]["quote_timestamp"] = "2026-07-20T09:50:00+08:00"
    await evaluate_candidate_pool(
        store, quote_source, available_cash=6085.61, total_assets=6085.61
    )
    active_signal_id = store.get("002123")["entry_signal"]["signal_id"]

    quote_source.quotes["002123"] = {
        "price": 3.2,
        "change_pct": 9.2,
        "vol_ratio": 3.0,
        "amount_wan": 25000,
        "limit_up": 3.2,
        "quote_timestamp": "2026-07-20T09:55:00+08:00",
        "freshness": "fresh",
    }
    cancelled = await evaluate_candidate_pool(
        store, quote_source, available_cash=6085.61, total_assets=6085.61
    )

    assert cancelled["alerts"][0]["action"] == "entry_cancelled"
    assert cancelled["alerts"][0]["signal_id"] == active_signal_id
    signal = store.get("002123")["entry_signal"]
    assert signal["state"] == "cancelled"
    assert signal["reason"] == "blocked_chasing"
    assert signal["signal_id"] == active_signal_id


@pytest.mark.asyncio
async def test_legacy_actionable_status_is_migrated_to_watching_after_cancellation(
    tmp_path,
):
    store = CandidatePoolStore(tmp_path / "candidate_pool.json")
    store.upsert_recommendations(
        [{"code": "002123", "name": "旧版买入状态", "reason": "历史信号"}],
        source="manual",
    )
    store.record_decision(
        "002123",
        "actionable",
        {
            "stock_code": "002123",
            "stock_name": "旧版买入状态",
            "action": "actionable",
            "playbook": "breakout_entry",
            "price": 3.2,
        },
    )

    result = await evaluate_candidate_pool(
        store,
        FakeQuoteSource(
            {
                "002123": {
                    "price": 3.1,
                    "change_pct": 2.4,
                    "vol_ratio": 1.5,
                    "amount_wan": 9000,
                    "freshness": "fresh",
                }
            }
        ),
        available_cash=6085.61,
        total_assets=6085.61,
    )

    assert result["alerts"][0]["action"] == "entry_cancelled"
    assert store.get("002123")["status"] == "watching"


@pytest.mark.asyncio
async def test_active_entry_signal_is_not_cancelled_when_quote_is_missing(tmp_path):
    store = TargetPoolStore(tmp_path / "candidate_pool.json")
    store.upsert_target(
        code="002123",
        name="低价突破",
        status="executable",
        source="target_scoring",
        scoring_decision=full_score_decision(),
        current_price=3.2,
        available_cash=6085.61,
        total_assets=6085.61,
    )
    seed_breakout_contract(store, "002123", 3.2)
    triggered = FakeQuoteSource(
        {
            "002123": {
                "price": 3.2,
                "change_pct": 4.2,
                "vol_ratio": 2.6,
                "amount_wan": 18000,
                "quote_timestamp": "2026-07-20T09:45:00+08:00",
                "freshness": "fresh",
            }
        }
    )
    await evaluate_candidate_pool(
        store, triggered, available_cash=6085.61, total_assets=6085.61
    )
    triggered.quotes["002123"]["quote_timestamp"] = "2026-07-20T09:50:00+08:00"
    await evaluate_candidate_pool(
        store, triggered, available_cash=6085.61, total_assets=6085.61
    )

    missing = await evaluate_candidate_pool(
        store,
        FakeQuoteSource({}),
        available_cash=6085.61,
        total_assets=6085.61,
    )

    assert missing["alerts"] == []
    signal = store.get("002123")["entry_signal"]
    assert signal["state"] == "active"
    assert signal["reason"] == "quote_unavailable"


@pytest.mark.asyncio
async def test_stale_quote_cannot_start_entry_confirmation(tmp_path):
    store = TargetPoolStore(tmp_path / "candidate_pool.json")
    store.upsert_target(
        code="002123",
        name="低价突破",
        status="executable",
        source="target_scoring",
        scoring_decision=full_score_decision(),
        current_price=3.2,
        available_cash=6085.61,
        total_assets=6085.61,
    )

    result = await evaluate_candidate_pool(
        store,
        FakeQuoteSource(
            {
                "002123": {
                    "price": 3.2,
                    "change_pct": 4.2,
                    "vol_ratio": 2.6,
                    "amount_wan": 18000,
                    "quote_timestamp": "2026-07-18T15:00:00+08:00",
                    "freshness": "stale",
                }
            }
        ),
        available_cash=6085.61,
        total_assets=6085.61,
    )

    assert result["alerts"] == []
    signal = store.get("002123")["entry_signal"]
    assert signal["state"] == "data_unavailable"
    assert signal["confirmations"] == 0


@pytest.mark.asyncio
async def test_candidate_pool_blocks_high_position_breakout_after_kline_enrichment(
    tmp_path,
):
    store = CandidatePoolStore(tmp_path / "candidate_pool.json")
    store.upsert_recommendations(
        [{"code": "002123", "name": "高位突破", "reason": "放量突破"}], source="manual"
    )
    payload = store.load()
    payload["items"]["002123"]["trigger_price"] = 6.8
    store.save(payload)
    bars = [
        {
            "open": 6.0 + idx * 0.05,
            "close": 6.0 + idx * 0.05,
            "high": 6.05 + idx * 0.05,
            "low": 5.95 + idx * 0.05,
        }
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
                    "freshness": "fresh",
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
    store.upsert_recommendations(
        [{"code": "002123", "name": "高波动低价", "reason": "放量突破"}],
        source="manual",
    )
    seed_breakout_contract(store, "002123", 13.0)

    result = await evaluate_candidate_pool(
        store,
        FakeQuoteSource(
            {
                "002123": {
                    "price": 13.0,
                    "change_pct": 4.2,
                    "vol_ratio": 2.6,
                    "amount_wan": 18000,
                    "freshness": "fresh",
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
    store = TargetPoolStore(tmp_path / "candidate_pool.json")
    store.upsert_target(
        code="300002",
        name="神州泰岳",
        status="executable",
        source="target_scoring",
        scoring_decision=full_score_decision(action="add"),
        current_price=7.8,
        available_cash=6000,
        total_assets=12000,
    )
    seed_breakout_contract(store, "300002", 7.8)

    first = await evaluate_candidate_pool(
        store,
        FakeQuoteSource(
            {
                "300002": {
                    "price": 7.8,
                    "change_pct": 4.2,
                    "vol_ratio": 2.6,
                    "amount_wan": 18000,
                    "quote_timestamp": "2026-07-20T09:45:00+08:00",
                    "freshness": "fresh",
                }
            }
        ),
        available_cash=6000,
        total_assets=12000,
        positions={"300002": {"shares": 100, "market_value": 780}},
    )
    result = await evaluate_candidate_pool(
        store,
        FakeQuoteSource(
            {
                "300002": {
                    "price": 7.8,
                    "change_pct": 4.2,
                    "vol_ratio": 2.6,
                    "amount_wan": 18000,
                    "quote_timestamp": "2026-07-20T09:50:00+08:00",
                    "freshness": "fresh",
                }
            }
        ),
        available_cash=6000,
        total_assets=12000,
        positions={"300002": {"shares": 100, "market_value": 780}},
    )

    assert first["alerts"] == []
    assert result["alerts"][0]["action"] == "add_position"
    assert "加仓" in result["alerts"][0]["message"]
    assert store.get("300002")["status"] == "executable"
    assert store.get("300002")["entry_signal"]["state"] == "active"


@pytest.mark.asyncio
async def test_candidate_pool_marks_position_limit_reached_for_add(tmp_path):
    store = CandidatePoolStore(tmp_path / "candidate_pool.json")
    store.upsert_recommendations(
        [{"code": "300002", "name": "神州泰岳", "reason": "放量突破"}], source="manual"
    )
    seed_breakout_contract(store, "300002", 7.8)

    result = await evaluate_candidate_pool(
        store,
        FakeQuoteSource(
            {
                "300002": {
                    "price": 7.8,
                    "change_pct": 4.2,
                    "vol_ratio": 2.6,
                    "amount_wan": 18000,
                    "freshness": "fresh",
                }
            }
        ),
        available_cash=6000,
        total_assets=12000,
        positions={"300002": {"shares": 500, "market_value": 5900}},
    )

    assert result["alerts"][0]["action"] == "position_limit_reached"
    assert "仓位上限" in result["alerts"][0]["message"]


def test_target_scoring_executable_requires_auditable_scorecard(tmp_path):
    store = TargetPoolStore(tmp_path / "target_pool.json")

    store.upsert_target(
        code="002123",
        name="缺评分卡标的",
        status="executable",
        source="target_scoring",
        current_price=3.2,
        available_cash=6085.61,
        total_assets=6085.61,
    )

    item = store.get("002123")
    assert item["status"] == "watching"
    assert item["scoring_decision"]["authorization_valid"] is False
    assert (
        item["scoring_decision"]["authorization_reason"]
        == "scorecard_missing_or_incomplete"
    )


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

    assert (
        store.upsert_target(
            code="000725", name="京东方A", status="risk_budget_too_small"
        )
        is True
    )
    assert store.get("000725")["status"] == "risk_budget_too_small"

    assert (
        store.upsert_target(code="000100", name="TCL科技", status="regime_blocks_dip")
        is True
    )
    assert store.get("000100")["status"] == "regime_blocks_dip"

    assert (
        store.upsert_target(
            code="000725", name="京东方A", status="blocked_high_position"
        )
        is True
    )
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


@pytest.mark.parametrize(
    "long_status",
    sorted(LONG_HORIZON_STATUSES),
)
@pytest.mark.parametrize("requested_status", ["watching", "research_reference"])
def test_target_scoring_cannot_downgrade_long_horizon_status(
    tmp_path,
    long_status,
    requested_status,
):
    store = TargetPoolStore(tmp_path / "target_pool.json")
    store.upsert_target(
        code="002123",
        name="长期标的",
        status=long_status,
        source="long_horizon",
        current_long_evidence_ids=["long-thesis:002123:v2"],
    )

    store.upsert_target(
        code="002123",
        name="长期标的",
        status=requested_status,
        source="target_scoring",
        scoring_decision={
            "action": "watch",
            "score": 88,
            "source_status": {
                "quote": "ok",
                "kline": "ok",
                "fund_flow": "ok",
                "financial": "ok",
            },
            "current_long_evidence_ids": ["long-thesis:002123:v2"],
        },
        current_long_evidence_ids=["long-thesis:002123:v2"],
        current_price=3.2,
        available_cash=6085.61,
        total_assets=6085.61,
    )

    item = store.get("002123")
    assert item["status"] == long_status
    assert item["current_long_evidence_ids"] == ["long-thesis:002123:v2"]
    assert item["scoring_decision"]["current_long_evidence_ids"] == [
        "long-thesis:002123:v2"
    ]


@pytest.mark.parametrize(
    "long_status",
    sorted(LONG_HORIZON_STATUSES),
)
def test_target_scoring_cannot_promote_long_horizon_without_production_approval(
    tmp_path,
    long_status,
):
    store = TargetPoolStore(tmp_path / "target_pool.json")
    store.upsert_target(
        code="002123",
        name="长期标的",
        status=long_status,
        source="long_horizon",
    )
    assert store.get("002123")["production_eligibility"]["eligible"] is False

    store.upsert_target(
        code="002123",
        name="长期标的",
        status="executable",
        source="target_scoring",
        scoring_decision=full_score_decision(action="buy"),
        current_price=3.2,
        available_cash=6085.61,
        total_assets=6085.61,
    )

    assert store.get("002123")["status"] == long_status


def test_target_scoring_can_explicitly_remove_long_horizon_target(tmp_path):
    store = TargetPoolStore(tmp_path / "target_pool.json")
    store.upsert_target(
        code="002123",
        name="长期标的",
        status="long_watch",
        source="long_horizon",
    )

    store.upsert_target(
        code="002123",
        name="长期标的",
        status="removed",
        source="target_scoring",
        scoring_decision={"action": "remove"},
    )

    assert store.get("002123")["status"] == "removed"


def test_target_pool_batch_upsert_reuses_preloaded_map_without_reloading(
    monkeypatch,
    tmp_path,
):
    store = TargetPoolStore(tmp_path / "target_pool.json")
    store.upsert_target(
        code="002123",
        name="批量评分一",
        status="watching",
        source="manual",
    )
    store.upsert_target(
        code="000001",
        name="批量评分二",
        status="watching",
        source="manual",
    )
    payload = store.load()
    load_calls = 0
    original_load = store.load

    def counting_load():
        nonlocal load_calls
        load_calls += 1
        return original_load()

    monkeypatch.setattr(store, "load", counting_load)
    batch_upsert = getattr(store, "upsert_targets", None)

    assert callable(batch_upsert)
    assert batch_upsert(
        [
            {
                "code": "002123",
                "name": "批量评分一",
                "status": "watching",
                "source": "target_scoring",
                "scoring_decision": full_score_decision(action="watch"),
            },
            {
                "code": "000001",
                "name": "批量评分二",
                "status": "watching",
                "source": "target_scoring",
                "scoring_decision": full_score_decision(action="watch"),
            },
        ],
        payload=payload,
    ) == 2
    assert load_calls == 0
    assert original_load()["items"]["002123"]["source"] == "target_scoring"
    assert original_load()["items"]["000001"]["source"] == "target_scoring"


def test_target_pool_cooldown_after_loss_is_durable_and_not_scan_active(tmp_path):
    store = TargetPoolStore(tmp_path / "target_pool.json")
    store.upsert_target(
        code="000725",
        name="京东方A",
        status="cooldown_after_loss",
        source="user_confirmed_loss_exit",
        current_price=6.38,
        available_cash=2103.25,
        total_assets=5975.25,
    )

    assert store.active_items() == []
    assert store.get("000725")["production_eligibility"]["eligible"] is False

    store.upsert_target(
        code="000725",
        name="京东方A",
        status="watching",
        source="target_scoring",
        current_price=6.50,
        available_cash=2103.25,
        total_assets=5975.25,
    )

    item = store.get("000725")
    assert item["status"] == "cooldown_after_loss"
    assert item["production_eligibility"]["eligible"] is False
    assert item["production_eligibility"]["reason"] == "cooldown_after_loss"


def test_target_pool_routes_unaffordable_serenity_candidate_to_research_reference(
    tmp_path,
):
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


def test_target_pool_research_overlay_entry_is_fail_closed_for_new_target(tmp_path):
    store = TargetPoolStore(tmp_path / "target_pool.json")
    merge_overlay = getattr(store, "merge_research_overlay", None)

    assert callable(merge_overlay)
    outcome = merge_overlay(
        code="688008",
        name="澜起科技",
        overlay_name="sentinel_serenity",
        status="research_reference",
        source="sentinel_serenity",
        evidence_ids=["ev_test"],
        evidence={
            "stage": "shadow_only",
            "boundary": "research_only",
            "research_only": True,
        },
        serenity={"score": 62.5},
    )

    item = store.get("688008")
    assert outcome["accepted"] is True
    assert outcome["changed"] is True
    assert item["status"] == "research_reference"
    assert item["source"] == "sentinel_serenity"
    assert item["production_eligibility"]["eligible"] is False
    assert item["provenance"]["research_only"] is True
    assert item["evidence"].get("stage") is None
    assert item["evidence"].get("boundary") is None
    assert item["evidence"].get("research_only") is None
    assert item["research_overlays"]["sentinel_serenity"]["evidence"] == {
        "stage": "shadow_only",
        "boundary": "research_only",
        "research_only": True,
    }


def test_target_pool_source_rewrite_cannot_promote_research_only_item(tmp_path):
    store = TargetPoolStore(tmp_path / "target_pool.json")
    store.upsert_target(
        code="002123",
        name="梦网科技",
        status="research_reference",
        source="sentinel_serenity",
        current_price=3.2,
        available_cash=6085.61,
        total_assets=6085.61,
    )

    store.upsert_target(
        code="002123",
        name="梦网科技",
        status="executable",
        source="target_scoring",
        scoring_decision=full_score_decision(),
        current_price=3.2,
        available_cash=6085.61,
        total_assets=6085.61,
    )

    item = store.get("002123")
    assert item["status"] == "research_reference"
    assert item["provenance"]["original_status"] == "research_reference"
    assert item["provenance"]["original_source"] == "sentinel_serenity"
    assert item["provenance"]["research_only"] is True
    assert item["production_eligibility"]["eligible"] is False


def test_full_score_reauthorization_recovers_legacy_polluted_production_item(
    tmp_path,
):
    store = TargetPoolStore(tmp_path / "target_pool.json")
    store.upsert_target(
        code="002123",
        name="历史污染标的",
        status="executable",
        source="target_scoring",
        scoring_decision=full_score_decision(),
        current_price=3.2,
        available_cash=6085.61,
        total_assets=6085.61,
    )
    payload = store.load()
    polluted = payload["items"]["002123"]
    polluted["status"] = "research_reference"
    polluted["source"] = "long_horizon"
    polluted["provenance"] = {
        "original_status": "executable",
        "original_source": "target_scoring",
        "research_only": True,
    }
    polluted["production_eligibility"] = {
        "eligible": False,
        "research_only": True,
        "approved": False,
        "original_status": "executable",
        "original_source": "target_scoring",
        "reason": "research_only_provenance",
        "approval": {},
    }
    polluted["research_overlays"] = {
        "long_horizon": {
            "status": "long_research",
            "source": "long_horizon",
            "research_only": True,
        }
    }
    store.save(payload)

    store.upsert_target(
        code="002123",
        name="历史污染标的",
        status="executable",
        source="target_scoring",
        scoring_decision=full_score_decision(),
        current_price=3.2,
        available_cash=6085.61,
        total_assets=6085.61,
    )

    restored = store.get("002123")
    assert restored["status"] == "executable"
    assert restored["source"] == "target_scoring"
    assert restored["production_eligibility"]["eligible"] is True
    assert restored["production_eligibility"]["research_only"] is False
    assert restored["provenance"] == {
        "original_status": "executable",
        "original_source": "target_scoring",
        "research_only": False,
    }
    assert restored["scoring_decision"]["authorization_valid"] is True
    assert {item["code"] for item in store.active_items()} == {"002123"}


def test_target_pool_rejects_self_declared_approval_for_research_only_item(tmp_path):
    store = TargetPoolStore(tmp_path / "target_pool.json")
    store.upsert_target(
        code="002123",
        name="梦网科技",
        status="research_reference",
        source="sentinel_serenity",
    )

    store.upsert_target(
        code="002123",
        name="梦网科技",
        status="executable",
        source="manual_production_approval",
        production_approval={
            "state": "approved",
            "approved_by": "portfolio_owner",
            "approved_at": "2026-07-15T09:30:00+08:00",
            "reason": "manual production review passed",
        },
        current_price=3.2,
        available_cash=6085.61,
        total_assets=6085.61,
    )

    item = store.get("002123")
    assert item["status"] == "research_reference"
    assert item["provenance"]["research_only"] is True
    assert item["production_eligibility"]["eligible"] is False
    assert item["production_eligibility"]["approved"] is False
    assert item["production_approval"] == {}


def test_target_pool_execution_gate_preserves_cash_reserve(tmp_path):
    store = TargetPoolStore(tmp_path / "target_pool.json")

    store.upsert_target(
        code="601857",
        name="中国石油",
        status="watching",
        current_price=10.0,
        available_cash=1469.57,
        total_assets=6052.57,
    )

    item = store.get("601857")
    assert item["status"] == "research_reference"
    assert item["execution"]["executable_budget"] == 864.31
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
        FakeQuoteSource(
            {
                "688008": {
                    "price": 70,
                    "change_pct": 4.0,
                    "vol_ratio": 3.0,
                    "amount_wan": 50000,
                    "freshness": "fresh",
                }
            }
        ),
        available_cash=6085.61,
    )

    assert result["scanned"] == 0
    assert result["alerts"] == []
    assert store.get("688008")["status"] == "research_reference"
