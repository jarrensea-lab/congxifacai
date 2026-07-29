"""Serenity-to-Long-Thesis research materialization tests."""
from __future__ import annotations

import copy
import base64
import json

import pytest

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
            "status": "success",
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
    assert forming["missing_verification"] == [
        "financial",
        "financial_core_metrics",
    ]

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


def test_long_horizon_overlay_preserves_existing_production_target(tmp_path):
    from app.services.long_horizon_pipeline import (
        materialize_serenity_long_horizon,
    )

    thesis_store = LongThesisStore(tmp_path / "long_thesis.json")
    ledger = EvidenceLedgerStore(tmp_path / "evidence_ledger.jsonl")
    target_pool = TargetPoolStore(tmp_path / "target_pool.json")
    scoring_decision = {
        "action": "buy",
        "score": 82,
        "missing_data": [],
        "source_status": {
            "quote": "ok",
            "kline": "ok",
            "fund_flow": "ok",
            "financial": "ok",
        },
    }
    target_pool.upsert_target(
        code="688001",
        name="验证材料",
        status="executable",
        source="target_scoring",
        scoring_decision=scoring_decision,
        current_price=18.6,
        available_cash=50000,
        total_assets=50000,
    )
    before = target_pool.get("688001")
    package = _package()
    package["serenity_deep_dives"] = [package["serenity_deep_dives"][0]]

    summary = materialize_serenity_long_horizon(
        package,
        "2026-07-26",
        thesis_store,
        ledger,
        target_pool,
    )

    assert summary["status"] == "success"
    item = target_pool.get("688001")
    for key in (
        "status",
        "source",
        "provenance",
        "production_eligibility",
        "production_approval",
        "scoring_decision",
        "execution",
    ):
        assert item[key] == before[key]
    assert item["status"] == "executable"
    assert item["production_eligibility"]["eligible"] is True
    assert item["current_long_evidence_ids"]
    assert item["research_overlays"]["long_horizon"]["status"] == "long_research"
    assert {row["code"] for row in target_pool.active_items()} == {"688001"}

    target_pool.upsert_target(
        code="688001",
        name="验证材料",
        status="executable",
        source="target_scoring",
        scoring_decision=scoring_decision,
        current_price=18.6,
        available_cash=50000,
        total_assets=50000,
    )
    assert target_pool.get("688001")["status"] == "executable"


def test_broken_long_thesis_blocks_only_when_formal_scoring_runs(tmp_path):
    from app.services.long_horizon_pipeline import (
        materialize_serenity_long_horizon,
    )
    from app.services.target_scoring import score_target

    thesis_store = LongThesisStore(tmp_path / "long_thesis.json")
    target_pool = TargetPoolStore(tmp_path / "target_pool.json")
    target_pool.upsert_target(
        code="688001",
        name="验证材料",
        status="executable",
        source="target_scoring",
        scoring_decision={
            "action": "buy",
            "score": 82,
            "missing_data": [],
            "source_status": {
                "quote": "ok",
                "kline": "ok",
                "fund_flow": "ok",
                "financial": "ok",
            },
        },
        current_price=18.6,
        available_cash=50000,
        total_assets=50000,
    )
    package = _package()
    package["serenity_deep_dives"] = [package["serenity_deep_dives"][0]]
    package["serenity_deep_dives"][0]["top_candidates"][0]["red_lines"][0][
        "status"
    ] = "triggered"

    materialize_serenity_long_horizon(
        package,
        "2026-07-26",
        thesis_store,
        EvidenceLedgerStore(tmp_path / "evidence_ledger.jsonl"),
        target_pool,
    )

    materialized = target_pool.get("688001")
    assert materialized["status"] == "executable"
    assert materialized["production_eligibility"]["eligible"] is True
    scored = score_target(
        {
            **materialized,
            "quote": {"status": "ok", "price": 18.6},
            "kline": {"status": "ok", "bars": []},
            "fund_flow": {"status": "ok"},
            "financial": {"status": "ok"},
        },
        available_cash=50000,
        total_assets=50000,
        long_thesis=thesis_store.get("688001"),
    )
    assert scored["action"] == "watch"
    assert scored["block_reason"] == "long_thesis_broken"


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


@pytest.mark.parametrize(
    ("corrupt_store", "payload", "expected_store"),
    [
        (
            "long",
            {"version": 1, "items": {"688001": "not-an-object"}},
            "long_thesis",
        ),
        (
            "long",
            {"version": 1, "items": {"bad-key": {"symbol": "bad-key"}}},
            "long_thesis",
        ),
        (
            "long",
            {"version": 1, "items": {"688001": {"symbol": "000001"}}},
            "long_thesis",
        ),
        (
            "target",
            {"version": 1, "items": {"688001": "not-an-object"}},
            "target_pool",
        ),
        (
            "target",
            {"version": 1, "items": {"bad-key": {"code": "bad-key"}}},
            "target_pool",
        ),
        (
            "target",
            {"version": 1, "items": {"688001": {"code": "000001"}}},
            "target_pool",
        ),
        (
            "ledger",
            '{"evidence_id":"ev_valid"}\n{broken\n',
            "evidence_ledger",
        ),
        (
            "ledger",
            "[]\n",
            "evidence_ledger",
        ),
    ],
    ids=[
        "long-value",
        "long-key",
        "long-code-mismatch",
        "target-value",
        "target-key",
        "target-code-mismatch",
        "ledger-json",
        "ledger-record",
    ],
)
def test_store_history_corruption_fails_before_any_write(
    tmp_path,
    corrupt_store,
    payload,
    expected_store,
):
    from app.services.long_horizon_pipeline import (
        materialize_serenity_long_horizon,
    )

    thesis_store = LongThesisStore(tmp_path / "long_thesis.json")
    ledger = EvidenceLedgerStore(tmp_path / "evidence_ledger.jsonl")
    target_pool = TargetPoolStore(tmp_path / "target_pool.json")
    corrupt_path = {
        "long": thesis_store.path,
        "ledger": ledger.path,
        "target": target_pool.path,
    }[corrupt_store]
    if isinstance(payload, str):
        corrupt_path.write_text(payload, encoding="utf-8")
    else:
        corrupt_path.write_text(
            json.dumps(payload, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    paths = (thesis_store.path, ledger.path, target_pool.path)
    before = {
        path: path.read_bytes() if path.exists() else None
        for path in paths
    }

    summary = materialize_serenity_long_horizon(
        _package(),
        "2026-07-26",
        thesis_store,
        ledger,
        target_pool,
    )

    assert summary["status"] == "failed"
    assert summary["write_count"] == 0
    assert summary["thesis_count"] == 0
    assert summary["evidence_count"] == 0
    assert summary["target_count"] == 0
    assert summary["diagnostics"][0]["reason"] == "store_history_corrupted"
    assert summary["diagnostics"][0]["store"] == expected_store
    after = {
        path: path.read_bytes() if path.exists() else None
        for path in paths
    }
    assert after == before


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


def test_materializer_accepts_current_bse_920_code(tmp_path):
    from app.services.long_horizon_pipeline import (
        materialize_serenity_long_horizon,
    )

    package = _package()
    package["serenity_deep_dives"] = [package["serenity_deep_dives"][0]]
    package["serenity_deep_dives"][0]["top_candidates"][0]["code"] = "920001"
    thesis_store = LongThesisStore(tmp_path / "long_thesis.json")
    ledger = EvidenceLedgerStore(tmp_path / "evidence_ledger.jsonl")
    target_pool = TargetPoolStore(tmp_path / "target_pool.json")

    summary = materialize_serenity_long_horizon(
        package,
        "2026-07-26",
        thesis_store,
        ledger,
        target_pool,
    )

    assert summary["status"] == "success"
    assert thesis_store.get("920001") is not None
    assert target_pool.get("920001")["status"] == "long_research"
    assert all(
        item["enters_target_pool"] is True
        for item in ledger.load_all()
    )


@pytest.mark.parametrize(
    ("metrics", "verified"),
    [
        ({"report_period": "2026Q1"}, False),
        ({"revenue_yoy_pct": float("nan")}, False),
        ({"net_profit": float("inf")}, False),
        ({"gross_margin_pct": "31.5"}, True),
        ({"operating_cashflow_yoy_pct": 0.0}, True),
    ],
)
def test_financial_verification_requires_a_finite_core_metric(
    tmp_path,
    metrics,
    verified,
):
    from app.services.long_horizon_pipeline import (
        materialize_serenity_long_horizon,
    )

    package = _package()
    package["serenity_deep_dives"] = [package["serenity_deep_dives"][0]]
    candidate = package["serenity_deep_dives"][0]["top_candidates"][0]
    candidate["financial_evidence"] = {
        "fact": "财务接口返回数据。",
        "strength": "medium",
        "status": "success",
        "metrics": metrics,
    }
    thesis_store = LongThesisStore(tmp_path / "long_thesis.json")

    materialize_serenity_long_horizon(
        package,
        "2026-07-26",
        thesis_store,
        EvidenceLedgerStore(tmp_path / "evidence_ledger.jsonl"),
        TargetPoolStore(tmp_path / "target_pool.json"),
    )

    thesis = thesis_store.get("688001")
    if verified:
        assert thesis["verification_status"] == "verified"
        assert thesis["thesis_status"] == "healthy"
        assert "financial_core_metrics" not in thesis["missing_verification"]
    else:
        assert thesis["verification_status"] == "incomplete"
        assert thesis["thesis_status"] == "forming"
        assert "financial_core_metrics" in thesis["missing_verification"]


@pytest.mark.parametrize(
    ("financial_status", "strength", "evidence_status"),
    [
        ({"status": "failed"}, "strong", "success"),
        ({"status": "success"}, "weak", "success"),
        ({"status": "success"}, "Weak", "success"),
        ({"status": "success"}, "strong", "failed"),
    ],
)
def test_financial_verification_rejects_failed_or_weak_evidence(
    tmp_path,
    financial_status,
    strength,
    evidence_status,
):
    from app.services.long_horizon_pipeline import (
        materialize_serenity_long_horizon,
    )

    package = _package()
    package["serenity_deep_dives"] = [package["serenity_deep_dives"][0]]
    dive = package["serenity_deep_dives"][0]
    dive["financial_status"] = financial_status
    dive["top_candidates"][0]["financial_evidence"]["strength"] = strength
    dive["top_candidates"][0]["financial_evidence"]["status"] = evidence_status
    thesis_store = LongThesisStore(tmp_path / "long_thesis.json")

    materialize_serenity_long_horizon(
        package,
        "2026-07-26",
        thesis_store,
        EvidenceLedgerStore(tmp_path / "evidence_ledger.jsonl"),
        TargetPoolStore(tmp_path / "target_pool.json"),
    )

    thesis = thesis_store.get("688001")
    assert thesis["verification_status"] == "incomplete"
    assert thesis["thesis_status"] == "forming"
    assert "financial" in thesis["missing_verification"]


def test_repeated_materialization_does_not_refresh_thesis_or_target(
    monkeypatch,
    tmp_path,
):
    from app.services import long_thesis, quant_lifecycle
    from app.services.long_horizon_pipeline import (
        materialize_serenity_long_horizon,
    )

    clock = {"now": "2026-07-26 20:00:00"}
    monkeypatch.setattr(long_thesis, "_now", lambda: clock["now"])
    monkeypatch.setattr(quant_lifecycle, "_now", lambda: clock["now"])
    package = _package()
    package["serenity_deep_dives"] = [copy.deepcopy(package["serenity_deep_dives"][0])]
    thesis_store = LongThesisStore(tmp_path / "long_thesis.json")
    ledger = EvidenceLedgerStore(tmp_path / "evidence_ledger.jsonl")
    target_pool = TargetPoolStore(tmp_path / "target_pool.json")

    materialize_serenity_long_horizon(
        package,
        "2026-07-26",
        thesis_store,
        ledger,
        target_pool,
    )
    thesis_bytes = thesis_store.path.read_bytes()
    target_bytes = target_pool.path.read_bytes()
    thesis_updated_at = thesis_store.get("688001")["updated_at"]
    target_updated_at = target_pool.get("688001")["updated_at"]
    clock["now"] = "2026-07-26 20:05:00"

    repeated = materialize_serenity_long_horizon(
        package,
        "2026-07-26",
        thesis_store,
        ledger,
        target_pool,
    )

    assert repeated["evidence_count"] == 0
    assert repeated["unchanged_count"] == 1
    assert repeated["unchanged_thesis_count"] == 1
    assert repeated["unchanged_target_count"] == 1
    assert repeated["skipped_count"] == 2
    assert thesis_store.path.read_bytes() == thesis_bytes
    assert target_pool.path.read_bytes() == target_bytes
    assert thesis_store.get("688001")["updated_at"] == thesis_updated_at
    assert target_pool.get("688001")["updated_at"] == target_updated_at


@pytest.mark.parametrize("failed_stage", ["ledger", "thesis", "target"])
def test_materializer_rolls_back_all_stores_after_each_write_stage_failure(
    monkeypatch,
    tmp_path,
    failed_stage,
):
    from app.services.long_horizon_pipeline import (
        materialize_serenity_long_horizon,
    )

    thesis_store = LongThesisStore(tmp_path / "long_thesis.json")
    ledger = EvidenceLedgerStore(tmp_path / "evidence_ledger.jsonl")
    target_pool = TargetPoolStore(tmp_path / "target_pool.json")
    transaction_path = tmp_path / "long_horizon_transaction.json"
    thesis_store.upsert({
        "symbol": "000001",
        "name": "原始 thesis",
        "core_thesis": "原始内容",
    })
    ledger.append_many([{
        "evidence_id": "ev_original",
        "type": "test",
        "summary": "原始 evidence",
    }])
    target_pool.upsert_target(
        code="000001",
        name="原始 target",
        status="long_research",
        source="long_horizon",
    )
    paths = (thesis_store.path, ledger.path, target_pool.path)
    before = {path: path.read_bytes() for path in paths}
    owner = {
        "ledger": ledger,
        "thesis": thesis_store,
        "target": target_pool,
    }[failed_stage]
    method_name = {
        "ledger": "append_many",
        "thesis": "upsert",
        "target": "merge_research_overlay",
    }[failed_stage]
    original = getattr(owner, method_name)

    def write_then_fail(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError(f"injected_{failed_stage}_failure")

    monkeypatch.setattr(owner, method_name, write_then_fail)

    summary = materialize_serenity_long_horizon(
        {
            **_package(),
            "serenity_deep_dives": [_package()["serenity_deep_dives"][0]],
        },
        "2026-07-26",
        thesis_store,
        ledger,
        target_pool,
        transaction_path=transaction_path,
    )

    assert summary["status"] == "failed"
    assert summary["write_count"] == 0
    assert summary["thesis_count"] == 0
    assert summary["evidence_count"] == 0
    assert summary["target_count"] == 0
    assert summary["diagnostics"][-1]["reason"] == "batch_transaction_failed"
    assert summary["recovery_summary"]["status"] == "rolled_back"
    assert {path: path.read_bytes() for path in paths} == before
    assert not transaction_path.exists()


def test_materializer_recovers_pending_journal_before_preflight(tmp_path):
    from app.services.long_horizon_pipeline import (
        materialize_serenity_long_horizon,
    )

    thesis_store = LongThesisStore(tmp_path / "long_thesis.json")
    ledger = EvidenceLedgerStore(tmp_path / "evidence_ledger.jsonl")
    target_pool = TargetPoolStore(tmp_path / "target_pool.json")
    transaction_path = tmp_path / "long_horizon_transaction.json"
    stores = [
        ("long_thesis", thesis_store.path),
        ("evidence_ledger", ledger.path),
        ("target_pool", target_pool.path),
    ]
    for _, path in stores:
        path.write_bytes(b"crash residue")
    transaction_path.write_text(
        json.dumps({
            "version": 1,
            "batch_id": "batch-crashed",
            "status": "pending",
            "stores": [
                {
                    "name": name,
                    "path": str(path),
                    "existed": False,
                    "content_b64": base64.b64encode(b"").decode("ascii"),
                }
                for name, path in stores
            ],
        }),
        encoding="utf-8",
    )

    summary = materialize_serenity_long_horizon(
        {"serenity_deep_dives": []},
        "2026-07-26",
        thesis_store,
        ledger,
        target_pool,
        transaction_path=transaction_path,
    )

    assert summary["status"] == "success"
    assert summary["recovery_summary"] == {
        "status": "recovered",
        "batch_id": "batch-crashed",
        "restored_stores": [
            "long_thesis",
            "evidence_ledger",
            "target_pool",
        ],
    }
    assert all(not path.exists() for _, path in stores)
    assert not transaction_path.exists()


def test_materializer_fails_before_writes_when_snapshot_exceeds_limit(
    monkeypatch,
    tmp_path,
):
    from app.services.long_horizon_pipeline import (
        materialize_serenity_long_horizon,
    )

    thesis_store = LongThesisStore(tmp_path / "long_thesis.json")
    ledger = EvidenceLedgerStore(tmp_path / "evidence_ledger.jsonl")
    target_pool = TargetPoolStore(tmp_path / "target_pool.json")
    transaction_path = tmp_path / "long_horizon_transaction.json"
    thesis_store.upsert({
        "symbol": "000001",
        "name": "原始 thesis",
        "core_thesis": "原始内容",
    })
    ledger.append_many([{
        "evidence_id": "ev_original_capacity",
        "type": "test",
        "summary": "原始 evidence",
    }])
    target_pool.upsert_target(
        code="000001",
        name="原始 target",
        status="long_research",
        source="long_horizon",
    )
    paths = (thesis_store.path, ledger.path, target_pool.path)
    before = {path: path.read_bytes() for path in paths}
    monkeypatch.setenv("CONGXI_LONG_HORIZON_MAX_SNAPSHOT_BYTES", "1")

    summary = materialize_serenity_long_horizon(
        {
            **_package(),
            "serenity_deep_dives": [_package()["serenity_deep_dives"][0]],
        },
        "2026-07-26",
        thesis_store,
        ledger,
        target_pool,
        transaction_path=transaction_path,
    )

    assert summary["status"] == "failed"
    assert summary["write_count"] == 0
    assert summary["diagnostics"][-1]["reason"] == (
        "transaction_snapshot_too_large"
    )
    assert {path: path.read_bytes() for path in paths} == before
    assert not transaction_path.exists()


def test_forming_to_verified_appends_revision_and_updates_current_ids(tmp_path):
    from app.services.long_horizon_pipeline import (
        materialize_serenity_long_horizon,
    )

    thesis_store = LongThesisStore(tmp_path / "long_thesis.json")
    ledger = EvidenceLedgerStore(tmp_path / "evidence_ledger.jsonl")
    target_pool = TargetPoolStore(tmp_path / "target_pool.json")
    forming_package = _package()
    forming_package["serenity_deep_dives"] = [
        forming_package["serenity_deep_dives"][1]
    ]
    forming_package["serenity_deep_dives"][0]["top_candidates"] = [
        forming_package["serenity_deep_dives"][0]["top_candidates"][0]
    ]
    materialize_serenity_long_horizon(
        forming_package,
        "2026-07-26",
        thesis_store,
        ledger,
        target_pool,
    )
    old_records = copy.deepcopy(ledger.load_all())
    old_ids = {item["evidence_id"] for item in old_records}
    old_logical_ids = {
        item["logical_evidence_id"] for item in old_records
    }

    verified_package = _package()
    verified_package["serenity_deep_dives"] = [
        verified_package["serenity_deep_dives"][0]
    ]
    verified_package["serenity_deep_dives"][0]["top_candidates"][0][
        "code"
    ] = "000001"
    verified_package["serenity_deep_dives"][0]["top_candidates"][0][
        "name"
    ] = "待核验公司"
    materialize_serenity_long_horizon(
        verified_package,
        "2026-07-26",
        thesis_store,
        ledger,
        target_pool,
    )

    all_records = ledger.load_all()
    assert all_records[:len(old_records)] == old_records
    new_records = all_records[len(old_records):]
    new_ids = {item["evidence_id"] for item in new_records}
    assert new_ids.isdisjoint(old_ids)
    assert {item["logical_evidence_id"] for item in new_records} == old_logical_ids
    assert {
        item["semantic_revision"] for item in new_records
    }.isdisjoint({
        item["semantic_revision"] for item in old_records
    })
    thesis = thesis_store.get("000001")
    target = target_pool.get("000001")
    assert set(thesis["current_long_evidence_ids"]) == new_ids
    assert set(target["current_long_evidence_ids"]) == new_ids
    assert set(thesis["evidence_ids"]) == old_ids | new_ids
    assert set(target["evidence_ids"]) == old_ids | new_ids


@pytest.mark.parametrize("revision_kind", ["verified_to_forming", "financial_revision"])
def test_verified_downgrade_or_financial_change_appends_revision(
    tmp_path,
    revision_kind,
):
    from app.services.long_horizon_pipeline import (
        materialize_serenity_long_horizon,
    )

    thesis_store = LongThesisStore(tmp_path / "long_thesis.json")
    ledger = EvidenceLedgerStore(tmp_path / "evidence_ledger.jsonl")
    target_pool = TargetPoolStore(tmp_path / "target_pool.json")
    initial = _package()
    initial["serenity_deep_dives"] = [initial["serenity_deep_dives"][0]]
    materialize_serenity_long_horizon(
        initial,
        "2026-07-26",
        thesis_store,
        ledger,
        target_pool,
    )
    old_records = copy.deepcopy(ledger.load_all())
    old_ids = {item["evidence_id"] for item in old_records}

    revised = copy.deepcopy(initial)
    candidate = revised["serenity_deep_dives"][0]["top_candidates"][0]
    if revision_kind == "verified_to_forming":
        revised["serenity_deep_dives"][0]["financial_status"] = {
            "status": "failed"
        }
    else:
        candidate["financial_evidence"]["metrics"]["gross_margin_pct"] = 35.5
    materialize_serenity_long_horizon(
        revised,
        "2026-07-26",
        thesis_store,
        ledger,
        target_pool,
    )

    all_records = ledger.load_all()
    assert all_records[:len(old_records)] == old_records
    new_records = all_records[len(old_records):]
    new_ids = {item["evidence_id"] for item in new_records}
    assert new_ids
    assert new_ids.isdisjoint(old_ids)
    assert {
        item["logical_evidence_id"] for item in new_records
    } == {
        item["logical_evidence_id"] for item in old_records
    }
    thesis = thesis_store.get("688001")
    assert set(thesis["current_long_evidence_ids"]) == new_ids
    if revision_kind == "verified_to_forming":
        assert thesis["verification_status"] == "incomplete"
        assert thesis["thesis_status"] == "forming"
    else:
        assert (
            thesis["financial_evidence"]["metrics"]["gross_margin_pct"]
            == 35.5
        )
