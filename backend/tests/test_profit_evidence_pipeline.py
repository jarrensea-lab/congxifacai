import pytest

from app.services.quant_lifecycle import TargetPoolStore
from app.services.evidence_ledger import (
    EvidenceLedgerStore,
    build_sentinel_evidence,
    build_sentinel_evidence_context,
    upsert_sentinel_evidence_to_target_pool,
)


def _sample_sentinel_package():
    return {
        "date": "2026-06-30",
        "event_count": 1705,
        "top_themes": [
            {"name": "半导体", "count": 167},
            {"name": "AI", "count": 225},
        ],
        "top_symbols": [
            {"name": "688008", "count": 3},
            {"name": "020988", "count": 5},
            {"name": "399808", "count": 2},
            {"name": "not-a-share", "count": 9},
        ],
        "risk_events": [
            {
                "id": "risk-1",
                "published_at": "2026-06-30T10:00:00",
                "matched_keywords": ["减持"],
                "excerpt": "某公司股东减持风险升高",
            }
        ],
        "serenity_deep_dives": [
            {
                "theme": "半导体",
                "theme_event_count": 167,
                "learning_report_path": "/tmp/serenity.md",
                "top_candidates": [
                    {
                        "code": "688008",
                        "name": "澜起科技",
                        "score": 62.5,
                        "chokepoint": "内存互连/服务器内存接口",
                        "chain_position": "芯片与关键组件",
                        "verify_next": "核验 DDR5/MRCD/MDB 产品收入",
                    }
                ],
            }
        ],
    }


def test_evidence_ledger_generates_stable_deduped_evidence(tmp_path):
    store = EvidenceLedgerStore(tmp_path / "evidence_ledger.jsonl")
    package = _sample_sentinel_package()

    evidence = build_sentinel_evidence(package)
    written_first = store.append_many(evidence)
    written_second = store.append_many(evidence)

    assert written_first == len(evidence)
    assert written_second == 0
    loaded = store.load_all()
    assert len(loaded) == len(evidence)
    assert all(item["evidence_id"].startswith("ev_") for item in loaded)


def test_sentinel_serenity_candidates_enter_target_pool_with_evidence(tmp_path):
    ledger = EvidenceLedgerStore(tmp_path / "evidence_ledger.jsonl")
    target_pool = TargetPoolStore(tmp_path / "target_pool.json")

    result = upsert_sentinel_evidence_to_target_pool(
        _sample_sentinel_package(),
        target_pool=target_pool,
        ledger=ledger,
    )

    item = target_pool.get("688008")
    assert result["upserted_targets"] == 1
    assert item["status"] == "candidate"
    assert item["source"] == "sentinel_serenity"
    assert item["serenity"]["score"] == 62.5
    assert item["sentinel"]["theme"] == "半导体"
    assert item["evidence_ids"]
    skipped_codes = {item["code"] for item in result["skipped"]}
    assert {"020988", "399808", "not-a-share"} <= skipped_codes
    assert all(item["reason"] == "invalid_a_share_code" for item in result["skipped"])


def test_sentinel_evidence_context_is_strategy_input_summary():
    context = build_sentinel_evidence_context(_sample_sentinel_package())

    assert "Sentinel evidence" in context
    assert "半导体" in context
    assert "澜起科技(688008)" in context
    assert "减持" in context


@pytest.mark.asyncio
async def test_run_debate_injects_sentinel_evidence_into_news_context(monkeypatch):
    import app.ai.debate as debate_module
    import app.engine.debate_tracker as tracker_module
    import app.ai.sentinel_role_performance as performance_module
    from app.engine.workshop import run_debate

    captured = {}

    class FakeEngine:
        async def debate(self, market_data, holdings_data, news_context="", role_performance="", overall_timeout=300.0):
            captured["news_context"] = news_context
            return {
                "debate": {},
                "final": {
                    "final_decision": "观望",
                    "confidence": 5,
                    "reasoning": "test",
                    "short_term": {"recommendations": []},
                    "mid_low_freq": {"recommendations": []},
                    "role_votes": {},
                },
                "quality": {},
            }

    monkeypatch.setattr(debate_module, "AIDebateEngine", FakeEngine)
    monkeypatch.setattr(tracker_module.DebateTracker, "save", lambda *args, **kwargs: None)
    monkeypatch.setattr(performance_module, "record_debate_predictions", lambda *args, **kwargs: [])

    await run_debate(
        {
            "market": {"indices": {}},
            "holdings_str": "无持仓",
            "news": [],
            "sentinel_evidence": "Sentinel evidence: 半导体 theme_count=167",
        }
    )

    assert "Sentinel evidence" in captured["news_context"]


def test_repaired_final_decision_contains_role_votes():
    from app.engine.workshop import _repair_final_decision

    result = {
        "final": {},
        "debate": {
            "hunter": {"recommendations": [{"code": "688008", "name": "澜起科技"}], "conviction": 7},
            "accountant": {"recommendations": [], "conviction": 5},
            "guardian": {"position_advice": "不追高", "conviction": 8},
            "researcher": {"analysis": "半导体瓶颈明确", "conviction": 6},
        },
    }

    final = _repair_final_decision(result)

    assert "role_votes" in final
    assert final["role_votes"]["688008"]["serenity"]["score"] == 6
    assert final["role_votes"]["688008"]["guardian"]["veto"] is True
