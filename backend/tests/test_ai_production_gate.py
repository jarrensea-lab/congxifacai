import pytest


@pytest.mark.asyncio
async def test_output_validator_fails_closed_when_validator_service_errors(monkeypatch):
    from app.ai.cloud_client import cloud
    from app.ai.debate import AIDebateEngine

    async def fail_chat(*args, **kwargs):
        raise RuntimeError("validator unavailable")

    monkeypatch.setattr(cloud, "chat", fail_chat)

    result = await AIDebateEngine().validate_output('{"final_decision":"买入"}')

    assert result["pass"] is False
    assert result["score"] == 0
    assert "校验异常" in result["summary"]


def test_production_gate_blocks_degraded_role_even_when_quality_score_passes():
    from app.engine.workshop import build_production_gate

    gate = build_production_gate({
        "quality": {"pass": True, "score": 9},
        "debate": {
            "hunter": {"degraded": True, "error": "AI服务暂不可用"},
            "accountant": {"recommendations": []},
        },
    })

    assert gate["allowed"] is False
    assert "degraded_role_output" in gate["reasons"]
    assert "validator_route_missing" in gate["reasons"]


def test_production_gate_requires_observed_usable_validator_route():
    from app.engine.workshop import build_production_gate

    base = {
        "quality": {"pass": True, "score": 9},
        "debate": {},
        "final": {},
        "model_runtime_status": {
            "status": "success",
            "providers": ["DeepSeek"],
            "calls": [],
            "degradation_reasons": [],
        },
    }

    missing = build_production_gate(base)
    usable = build_production_gate({
        **base,
        "model_runtime_status": {
            **base["model_runtime_status"],
            "calls": [{
                "role": "输出校验",
                "provider": "DeepSeek",
                "attempted_provider": "DeepSeek",
                "requested_provider": "DeepSeek",
                "status": "success",
                "output_usable": True,
            }],
        },
    })

    assert missing["allowed"] is False
    assert "validator_route_missing" in missing["reasons"]
    assert usable["allowed"] is True
    assert usable["reasons"] == []


def test_premarket_production_write_requires_explicit_gate_approval(tmp_path):
    from app.main import persist_premarket_recommendations
    from app.services.quant_lifecycle import CandidatePoolStore

    decision = {
        "short_term": {
            "recommendations": [{
                "code": "000725",
                "name": "京东方A",
                "reason": "测试推荐",
            }]
        },
        "mid_low_freq": {"recommendations": []},
    }
    store = CandidatePoolStore(tmp_path / "candidate_pool.json")

    blocked = persist_premarket_recommendations(
        {"production_gate": {"allowed": False, "reasons": ["quality_check_failed"]}},
        decision,
        store=store,
    )

    assert blocked == 0
    assert store.get("000725") is None

    written = persist_premarket_recommendations(
        {"production_gate": {"allowed": True, "reasons": []}},
        decision,
        store=store,
    )

    assert written == 1
    assert store.get("000725")["source"] == "premarket"
