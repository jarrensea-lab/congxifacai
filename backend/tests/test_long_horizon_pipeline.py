"""Serenity-to-Long-Thesis research materialization tests."""
from __future__ import annotations

import json

from app.services.evidence_ledger import EvidenceLedgerStore
from app.services.long_thesis import LongThesisStore
from app.services.quant_lifecycle import TargetPoolStore


def _candidate(
    code: str,
    name: str,
    *,
    quote_verified: bool = True,
    financial_verified: bool = True,
) -> dict:
    candidate = {
        "code": code,
        "name": name,
        "score": 78.5,
        "chokepoint": "高纯材料良率",
        "chain_position": "上游关键材料",
        "verify_next": "核验订单与毛利率传导",
        "long_assumptions": [
            {
                "id": "demand_persistence",
                "claim": "需求能跨季度持续传导。",
                "status": "intact",
            }
        ],
        "red_lines": [
            {
                "id": "substitution_or_bypass",
                "condition": "替代技术绕过当前瓶颈。",
                "severity": "high",
                "status": "clear",
            }
        ],
        "valuation_questions": ["估值是否已经反映瓶颈持续性？"],
        "quarterly_verification_tasks": ["每季度复核订单、毛利率和库存。"],
        "bottleneck_duration": "1-3年",
        "bottleneck_map": {
            "code": code,
            "chain_position": "上游关键材料",
            "chokepoint": "高纯材料良率",
            "duration": "1-3年",
            "substitution_risk": "待跟踪",
        },
    }
    if quote_verified:
        candidate["quote_evidence"] = {
            "fact": f"{name}行情核验有效。",
            "strength": "medium",
            "metrics": {"price": 18.6, "pe_ttm": 24.2, "pb": 2.8},
        }
    if financial_verified:
        candidate["financial_evidence"] = {
            "fact": f"{name}财务核验有效。",
            "strength": "strong",
            "metrics": {
                "report_period": "2026Q1",
                "revenue_yoy_pct": 18.0,
                "gross_margin_pct": 32.0,
            },
        }
    return candidate


def _package() -> dict:
    return {
        "date": "2026-07-26",
        "boundary": "research_only",
        "serenity_deep_dives": [
            {
                "theme": "半导体材料",
                "learning_report_path": "/tmp/verified-serenity.md",
                "quote_status": {"status": "success"},
                "financial_status": {"status": "success"},
                "top_candidates": [_candidate("688001", "验证材料")],
                "boundary": "research_only",
            },
            {
                "theme": "机器人",
                "learning_report_path": "/tmp/forming-serenity.md",
                "quote_status": {"status": "success"},
                "financial_status": {"status": "unavailable"},
                "top_candidates": [
                    _candidate(
                        "000001",
                        "待核验公司",
                        financial_verified=False,
                    ),
                    _candidate("399808", "指数不是股票"),
                ],
                "boundary": "research_only",
            },
        ],
    }


def test_materialize_serenity_long_horizon_persists_verified_and_forming_research(
    tmp_path,
):
    from app.services.long_horizon_pipeline import (
        materialize_serenity_long_horizon,
    )

    thesis_store = LongThesisStore(tmp_path / "long_thesis.json")
    ledger = EvidenceLedgerStore(tmp_path / "evidence_ledger.jsonl")
    target_pool = TargetPoolStore(tmp_path / "target_pool.json")

    first = materialize_serenity_long_horizon(
        _package(),
        "2026-07-26",
        thesis_store=thesis_store,
        ledger=ledger,
        target_pool=target_pool,
    )
    ledger_after_first = ledger.load_all()
    second = materialize_serenity_long_horizon(
        _package(),
        "2026-07-26",
        thesis_store=thesis_store,
        ledger=ledger,
        target_pool=target_pool,
    )

    assert first["status"] == "partial"
    assert first["thesis_count"] == 2
    assert first["target_count"] == 2
    assert first["verified_count"] == 1
    assert first["forming_count"] == 1
    assert first["evidence_count"] == len(ledger_after_first)
    assert second["evidence_count"] == 0
    assert ledger.load_all() == ledger_after_first
    assert any(
        item["reason"] == "invalid_a_share_code" and item["symbol"] == "399808"
        for item in first["diagnostics"]
    )

    verified = thesis_store.get("688001")
    assert verified["thesis_status"] == "healthy"
    assert verified["verification_status"] == "verified"
    assert verified["missing_verification"] == []
    assert verified["boundary"] == "shadow_only"
    assert verified["source_report_path"] == "/tmp/verified-serenity.md"
    assert verified["data_cutoff_date"] == "2026-07-26"
    assert verified["assumptions"] == _candidate("688001", "验证材料")["long_assumptions"]
    assert verified["valuation_questions"]
    assert verified["quarterly_verification_tasks"]
    assert verified["bottleneck_duration"] == "1-3年"
    assert verified["bottleneck_map"]["chokepoint"] == "高纯材料良率"
    assert verified["quote_evidence"]["metrics"]["price"] == 18.6
    assert verified["financial_evidence"]["metrics"]["gross_margin_pct"] == 32.0

    forming = thesis_store.get("000001")
    assert forming["thesis_status"] == "forming"
    assert forming["verification_status"] == "incomplete"
    assert forming["missing_verification"] == ["financial"]

    for code in ("688001", "000001"):
        target = target_pool.get(code)
        assert target["status"] == "long_research"
        assert target["source"] == "long_horizon"
        assert target["production_approval"] == {}
        assert target["production_eligibility"]["eligible"] is False
        assert target["provenance"]["research_only"] is True
        assert "buy" not in json.dumps(target, ensure_ascii=False).lower()

    assert ledger_after_first
    assert all(item["type"].startswith("long_") for item in ledger_after_first)
    assert all(item["enters_strategy"] is False for item in ledger_after_first)


def test_materializer_fails_closed_when_store_history_is_corrupted(tmp_path):
    from app.services.long_horizon_pipeline import (
        materialize_serenity_long_horizon,
    )

    thesis_path = tmp_path / "long_thesis.json"
    thesis_path.write_text("{broken", encoding="utf-8")
    ledger = EvidenceLedgerStore(tmp_path / "evidence_ledger.jsonl")
    target_pool = TargetPoolStore(tmp_path / "target_pool.json")

    summary = materialize_serenity_long_horizon(
        _package(),
        "2026-07-26",
        thesis_store=LongThesisStore(thesis_path),
        ledger=ledger,
        target_pool=target_pool,
    )

    assert summary["status"] == "failed"
    assert summary["thesis_count"] == 0
    assert summary["evidence_count"] == 0
    assert summary["target_count"] == 0
    assert summary["diagnostics"][0]["reason"] == "store_history_corrupted"
    assert thesis_path.read_text(encoding="utf-8") == "{broken"
    assert not ledger.path.exists()
    assert not target_pool.path.exists()


def test_materializer_accepts_all_injected_stores_positionally(tmp_path):
    from app.services.long_horizon_pipeline import (
        materialize_serenity_long_horizon,
    )

    summary = materialize_serenity_long_horizon(
        {"serenity_deep_dives": []},
        "2026-07-26",
        LongThesisStore(tmp_path / "long_thesis.json"),
        EvidenceLedgerStore(tmp_path / "evidence_ledger.jsonl"),
        TargetPoolStore(tmp_path / "target_pool.json"),
    )

    assert summary["status"] == "success"
    assert summary["thesis_count"] == 0
