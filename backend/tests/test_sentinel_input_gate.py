"""Sentinel decision-input freshness and injection gate tests."""

import pytest


def test_sentinel_package_age_days_preserves_signed_age():
    from app.services.sentinel_input_gate import sentinel_package_age_days

    assert sentinel_package_age_days({"date": "2026-07-02"}, "2026-07-04") == 2
    assert sentinel_package_age_days({"date": "2026-07-05"}, "2026-07-04") == -1


@pytest.mark.parametrize(
    ("package", "report_date", "status", "age_days", "active"),
    [
        (None, "2026-07-04", "unavailable", None, False),
        ({"date": "2026-07-04"}, "2026-07-04", "active", 0, True),
        ({"date": "2026-07-03"}, "2026-07-04", "active", 1, True),
        ({"date": "2026-07-02"}, "2026-07-04", "active", 2, True),
        ({"date": "2026-07-01"}, "2026-07-04", "stale", 3, False),
        ({"date": "2026-07-05"}, "2026-07-04", "future", -1, False),
        ({}, "2026-07-04", "unknown_date", None, False),
        ({"date": "not-a-date"}, "2026-07-04", "unknown_date", None, False),
        ({"date": "2026-07-04"}, "not-a-date", "unknown_date", None, False),
        (["not", "a", "package"], "2026-07-04", "unavailable", None, False),
    ],
)
def test_classify_sentinel_package_uses_one_signed_freshness_contract(
    package,
    report_date,
    status,
    age_days,
    active,
):
    from app.services.sentinel_input_gate import classify_sentinel_package

    result = classify_sentinel_package(package, report_date)

    assert result["status"] == status
    assert result["age_days"] == age_days
    assert result["active"] is active


@pytest.mark.parametrize(
    ("package", "expected_status"),
    [
        (None, "unavailable"),
        ({"date": "2026-07-01"}, "stale"),
        ({"date": "2026-07-05"}, "future"),
        ({}, "unknown_date"),
        ({"date": "invalid"}, "unknown_date"),
    ],
)
def test_inactive_sentinel_never_calls_context_or_upsert(
    package,
    expected_status,
):
    from app.services.sentinel_input_gate import inject_active_sentinel_evidence

    market_data = {}
    calls = []

    result = inject_active_sentinel_evidence(
        package,
        "2026-07-04",
        market_data,
        context_builder=lambda value: calls.append(("context", value)) or "ctx",
        target_upserter=lambda value: calls.append(("upsert", value)) or {},
    )

    assert result["status"] == expected_status
    assert result["active"] is False
    assert calls == []
    assert "sentinel_evidence" not in market_data


def test_active_sentinel_injects_only_after_context_and_upsert_succeed():
    from app.services.sentinel_input_gate import inject_active_sentinel_evidence

    package = {"date": "2026-07-03"}
    market_data = {}
    calls = []

    result = inject_active_sentinel_evidence(
        package,
        "2026-07-04",
        market_data,
        context_builder=lambda value: calls.append("context") or "ctx",
        target_upserter=lambda value: calls.append("upsert") or {
            "evidence_count": 1,
            "upserted_targets": 1,
        },
    )

    assert calls == ["context", "upsert"]
    assert market_data["sentinel_evidence"] == "ctx"
    assert result["active"] is True
    assert result["ingest_result"]["upserted_targets"] == 1


def test_active_sentinel_upsert_failure_does_not_leak_context_into_ai_input():
    from app.services.sentinel_input_gate import inject_active_sentinel_evidence

    market_data = {}

    with pytest.raises(RuntimeError, match="failed"):
        inject_active_sentinel_evidence(
            {"date": "2026-07-04"},
            "2026-07-04",
            market_data,
            context_builder=lambda value: "ctx",
            target_upserter=lambda value: (_ for _ in ()).throw(
                RuntimeError("upsert failed")
            ),
        )

    assert "sentinel_evidence" not in market_data


@pytest.mark.parametrize(
    ("entry_name", "package", "expected_status"),
    [
        ("daily_report", {"date": "2026-07-01"}, "stale"),
        ("daily_report", {"date": "2026-07-05"}, "future"),
        ("daily_report", {"date": "invalid"}, "unknown_date"),
        ("backend_premarket", {"date": "2026-07-01"}, "stale"),
        ("backend_premarket", {"date": "2026-07-05"}, "future"),
        ("backend_premarket", {"date": "invalid"}, "unknown_date"),
        ("legacy_premarket", {"date": "2026-07-01"}, "stale"),
        ("legacy_premarket", {"date": "2026-07-05"}, "future"),
        ("legacy_premarket", {"date": "invalid"}, "unknown_date"),
    ],
)
def test_each_decision_entry_keeps_inactive_sentinel_out_of_ai_input(
    monkeypatch,
    entry_name,
    package,
    expected_status,
):
    from app import main as app_main
    from app.services import evidence_ledger
    from scripts import daily_report, run_premarket

    entries = {
        "daily_report": daily_report._inject_active_sentinel_evidence,
        "backend_premarket": app_main._apply_premarket_sentinel_input,
        "legacy_premarket": run_premarket.apply_premarket_sentinel_input,
    }
    calls = []
    monkeypatch.setattr(
        evidence_ledger,
        "build_sentinel_evidence_context",
        lambda value: calls.append("context") or "ctx",
    )
    monkeypatch.setattr(
        evidence_ledger,
        "upsert_sentinel_evidence_to_target_pool",
        lambda value: calls.append("upsert") or {},
    )
    market_data = {}

    result = entries[entry_name](
        package,
        "2026-07-04",
        market_data,
    )

    assert result["status"] == expected_status
    assert result["active"] is False
    assert calls == []
    assert "sentinel_evidence" not in market_data


def test_all_decision_entries_use_public_sentinel_gate_functions():
    from app import main as app_main
    from app.services import sentinel_input_gate
    from scripts import daily_report, run_premarket

    assert (
        daily_report._sentinel_package_age_days
        is sentinel_input_gate.sentinel_package_age_days
    )
    assert (
        daily_report._sentinel_package_freshness
        is sentinel_input_gate.classify_sentinel_package
    )
    assert (
        daily_report._sentinel_audit_only_message
        is sentinel_input_gate.sentinel_audit_only_message
    )
    assert (
        daily_report._inject_active_sentinel_evidence
        is sentinel_input_gate.inject_active_sentinel_evidence
    )
    assert (
        app_main.inject_active_sentinel_evidence
        is sentinel_input_gate.inject_active_sentinel_evidence
    )
    assert (
        run_premarket.inject_active_sentinel_evidence
        is sentinel_input_gate.inject_active_sentinel_evidence
    )
