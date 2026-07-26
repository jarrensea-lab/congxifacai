"""Sentinel decision-input freshness and injection gate tests."""

import json

import pytest


def test_recent_sentinel_loader_checks_only_today_then_previous_two_days():
    from app.services.sentinel_input_gate import load_recent_sentinel_package

    calls = []

    def fake_loader(package_date, *, output_root):
        calls.append((package_date, output_root))
        if package_date == "2026-07-02":
            return {"date": package_date, "event_count": 3}
        return None

    result = load_recent_sentinel_package(
        "2026-07-04",
        output_root="/sentinel-test",
        package_loader=fake_loader,
    )

    assert [item[0] for item in calls] == [
        "2026-07-04",
        "2026-07-03",
        "2026-07-02",
    ]
    assert result["date"] == "2026-07-02"
    assert result["fallback_used"] is True
    assert result["requested_date"] == "2026-07-04"
    assert result["package_file_date"] == "2026-07-02"


def test_recent_sentinel_loader_does_not_scan_beyond_two_days():
    from app.services.sentinel_input_gate import load_recent_sentinel_package

    calls = []

    def fake_loader(package_date, *, output_root):
        calls.append(package_date)
        return None

    assert (
        load_recent_sentinel_package(
            "2026-07-04",
            output_root="/sentinel-test",
            package_loader=fake_loader,
        )
        is None
    )
    assert calls == ["2026-07-04", "2026-07-03", "2026-07-02"]


def test_recent_loader_does_not_bypass_first_file_with_invalid_internal_date():
    from app.services.sentinel_input_gate import (
        classify_sentinel_package,
        load_recent_sentinel_package,
    )

    calls = []

    def fake_loader(package_date):
        calls.append(package_date)
        if package_date == "2026-07-04":
            return {"date": "not-a-date"}
        return {"date": package_date}

    loaded = load_recent_sentinel_package(
        "2026-07-04",
        package_loader=fake_loader,
    )

    assert calls == ["2026-07-04"]
    assert loaded["package_file_date"] == "2026-07-04"
    assert loaded["date"] == "not-a-date"
    assert classify_sentinel_package(
        loaded,
        "2026-07-04",
    )["status"] == "unknown_date"


@pytest.mark.parametrize(
    ("entry_name", "offset_days"),
    [
        ("daily_report", 1),
        ("daily_report", 2),
        ("backend_premarket", 1),
        ("backend_premarket", 2),
        ("legacy_premarket", 1),
        ("legacy_premarket", 2),
    ],
)
def test_each_entry_discovers_and_injects_recent_sentinel_fallback(
    monkeypatch,
    tmp_path,
    entry_name,
    offset_days,
):
    from datetime import date, timedelta

    from app import main as app_main
    from app.services import evidence_ledger
    from scripts import daily_report, run_premarket

    report_day = date(2026, 7, 4)
    package_day = report_day - timedelta(days=offset_days)
    package_root = tmp_path / entry_name / str(offset_days)
    package_dir = package_root / "research_packages"
    package_dir.mkdir(parents=True)
    package = {
        "date": package_day.isoformat(),
        "event_count": offset_days,
    }
    (package_dir / f"{package_day.isoformat()}.json").write_text(
        json.dumps(package),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        daily_report,
        "SENTINEL_OUTPUT_ROOT",
        package_root,
    )
    loaders = {
        "daily_report": lambda: daily_report.load_sentinel_research_package(
            report_day.isoformat()
        ),
        "backend_premarket": lambda: app_main._load_premarket_sentinel_package(
            report_day.isoformat(),
            output_root=package_root,
        ),
        "legacy_premarket": lambda: run_premarket.load_premarket_sentinel_package(
            report_day.isoformat(),
            output_root=package_root,
        ),
    }
    gates = {
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

    loaded = loaders[entry_name]()
    market_data = {}
    result = gates[entry_name](
        loaded,
        report_day.isoformat(),
        market_data,
    )

    assert loaded["date"] == package_day.isoformat()
    assert loaded["package_file_date"] == package_day.isoformat()
    assert result["active"] is True
    assert result["age_days"] == offset_days
    assert calls == ["context", "upsert"]
    assert market_data["sentinel_evidence"] == "ctx"


@pytest.mark.parametrize(
    ("internal_date", "expected_status"),
    [
        ("2026-07-01", "stale"),
        ("2026-07-05", "future"),
        ("not-a-date", "unknown_date"),
    ],
)
def test_filename_date_never_overrides_invalid_internal_package_date(
    tmp_path,
    internal_date,
    expected_status,
):
    from app.services.sentinel_input_gate import (
        inject_active_sentinel_evidence,
        load_recent_sentinel_package,
    )

    package_dir = tmp_path / "research_packages"
    package_dir.mkdir()
    (package_dir / "2026-07-03.json").write_text(
        json.dumps({"date": internal_date, "event_count": 3}),
        encoding="utf-8",
    )

    loaded = load_recent_sentinel_package(
        "2026-07-04",
        output_root=tmp_path,
    )
    calls = []
    market_data = {}
    result = inject_active_sentinel_evidence(
        loaded,
        "2026-07-04",
        market_data,
        context_builder=lambda value: calls.append("context") or "ctx",
        target_upserter=lambda value: calls.append("upsert") or {},
    )

    assert loaded["package_file_date"] == "2026-07-03"
    assert loaded["date"] == internal_date
    assert result["status"] == expected_status
    assert result["active"] is False
    assert calls == []
    assert "sentinel_evidence" not in market_data


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
    assert (
        daily_report._load_recent_sentinel_package
        is sentinel_input_gate.load_recent_sentinel_package
    )
    assert (
        app_main.load_recent_sentinel_package
        is sentinel_input_gate.load_recent_sentinel_package
    )
    assert (
        run_premarket.load_recent_sentinel_package
        is sentinel_input_gate.load_recent_sentinel_package
    )
