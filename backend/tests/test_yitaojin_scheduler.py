"""Scheduler and runtime safety tests for the Yitaojin integration."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest


def _account_snapshot(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "captured_at": "2026-07-26T08:55:01+08:00",
                "account_fingerprint": "sha256:" + "a" * 64,
                "total_assets": "3000",
                "available_cash": "3000",
                "frozen_cash": "0",
                "positions": [],
                "empty_positions_confirmed": True,
                "source": "yitaojin_ui",
            }
        ),
        encoding="utf-8",
    )


def _candidate_pool(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "updated_at": "2026-07-25T20:45:00+08:00",
                "items": {
                    "600000": {
                        "code": "600000",
                        "status": "executable",
                    },
                    "000001": {
                        "code": "000001",
                        "status": "research_reference",
                    },
                },
            }
        ),
        encoding="utf-8",
    )


@dataclass
class _FakeResult:
    status: str
    reasons: tuple[str, ...] = ()


class _FakeBridge:
    def __init__(self, probes=None):
        self.probes = list(
            probes
            or [
                {
                    "appRunning": True,
                    "applicationPathValid": True,
                    "accessibilityTrusted": True,
                    "loginState": "logged_in",
                    "interfaceSignature": "sha256:ui",
                }
            ]
        )
        self.calls = []

    def run(self, command, payload=None, *, timeout=15.0):
        self.calls.append((command.value, payload, timeout))
        return self.probes.pop(0) if len(self.probes) > 1 else self.probes[0]


class _FakeAccount:
    def __init__(self, events, result=None):
        self.events = events
        self.result = result or _FakeResult("validated")

    def sync_account(self, *, apply=False, bootstrap=False):
        self.events.append(("account", apply, bootstrap))
        return self.result


class _FakeWatchlist:
    def __init__(self, events, result=None):
        self.events = events
        self.result = result or _FakeResult("planned")

    def sync_watchlist(self, *, apply=False, bootstrap=False):
        self.events.append(("watchlist", apply, bootstrap))
        return self.result


class _FakeQuotes:
    def __init__(self, events, result=None):
        self.events = events
        self.result = result or _FakeResult("ok")

    def refresh(self, **kwargs):
        self.events.append(
            (
                "quotes",
                tuple(sorted(kwargs["critical_codes"])),
                tuple(sorted(kwargs["reference_quotes"])),
            )
        )
        return self.result


class _FakeMarket:
    async def fetch_batch(self, codes):
        return {
            code: {
                "price": 10,
                "quote_timestamp": "2026-07-26T08:55:02+08:00",
            }
            for code in codes
        }


def _paths(tmp_path):
    account_path = tmp_path / "account.json"
    pool_path = tmp_path / "pool.json"
    _account_snapshot(account_path)
    _candidate_pool(pool_path)
    return SimpleNamespace(
        runtime_status=tmp_path / "runtime-status.json",
        account_snapshot=account_path,
        candidate_pool=pool_path,
    )


def _services(tmp_path, events, *, account_result=None):
    from app.integrations.yitaojin.runtime import RuntimeServices

    paths = _paths(tmp_path)
    return paths, RuntimeServices(
        bridge=_FakeBridge(),
        account=_FakeAccount(events, result=account_result),
        watchlist=_FakeWatchlist(events),
        quotes=_FakeQuotes(events),
        account_snapshot_path=paths.account_snapshot,
        candidate_pool_path=paths.candidate_pool,
        bridge_build_id="sha256:test-build",
    )


@pytest.mark.asyncio
async def test_disabled_runtime_returns_without_building_bridge_or_opening_app(
    tmp_path,
):
    from app.integrations.yitaojin.runtime import run_yitaojin_task

    calls = []

    def forbidden_factory(*args, **kwargs):
        calls.append("factory")
        raise AssertionError("disabled integration must not build the bridge")

    result = await run_yitaojin_task(
        "morning",
        environment={
            "CONGXI_YITAOJIN_ENABLED": "false",
            "CONGXI_YITAOJIN_WRITE_ENABLED": "false",
        },
        paths=SimpleNamespace(runtime_status=tmp_path / "status.json"),
        services_factory=forbidden_factory,
    )

    assert result["state"] == "disabled"
    assert result["enabled"] is False
    assert calls == []


@pytest.mark.asyncio
async def test_morning_runtime_sequences_watchlist_then_quotes_off_loop(
    tmp_path,
    monkeypatch,
):
    import app.integrations.yitaojin.runtime as runtime

    events = []
    paths, services = _services(tmp_path, events)
    off_loop_calls = []
    real_to_thread = asyncio.to_thread

    async def tracked_to_thread(function, *args, **kwargs):
        off_loop_calls.append(
            getattr(function, "__name__", function.__class__.__name__)
        )
        return await real_to_thread(function, *args, **kwargs)

    monkeypatch.setattr(runtime.asyncio, "to_thread", tracked_to_thread)
    result = await runtime.run_yitaojin_task(
        "morning",
        environment={
            "CONGXI_YITAOJIN_ENABLED": "true",
            "CONGXI_YITAOJIN_WRITE_ENABLED": "false",
        },
        paths=paths,
        services_factory=lambda *_: services,
        market_source=_FakeMarket(),
    )

    assert result["state"] == "success"
    assert [event[0] for event in events] == ["watchlist", "quotes"]
    assert events[0] == ("watchlist", False, False)
    assert events[1][1] == ("600000",)
    assert "sync_account" not in off_loop_calls
    assert "sync_watchlist" in off_loop_calls
    assert "refresh" in off_loop_calls


@pytest.mark.asyncio
async def test_evening_task_only_applies_watchlist(tmp_path):
    from app.integrations.yitaojin.runtime import run_yitaojin_task

    events = []
    paths, services = _services(tmp_path, events)

    result = await run_yitaojin_task(
        "evening",
        environment={
            "CONGXI_YITAOJIN_ENABLED": "true",
            "CONGXI_YITAOJIN_WRITE_ENABLED": "true",
        },
        paths=paths,
        services_factory=lambda *_: services,
    )

    assert result["state"] == "success"
    assert events == [("watchlist", True, False)]


@pytest.mark.asyncio
async def test_close_account_is_independent_and_uses_account_specific_write_gate(
    tmp_path,
):
    from app.integrations.yitaojin.runtime import run_yitaojin_task

    events = []
    paths, services = _services(tmp_path, events)

    result = await run_yitaojin_task(
        "close_account",
        environment={
            "CONGXI_YITAOJIN_ENABLED": "true",
            "CONGXI_YITAOJIN_WRITE_ENABLED": "false",
            "CONGXI_YITAOJIN_ACCOUNT_WRITE_ENABLED": "true",
            "CONGXI_YITAOJIN_WATCHLIST_WRITE_ENABLED": "false",
        },
        paths=paths,
        services_factory=lambda *_: services,
    )

    assert result["state"] == "success"
    assert result["task"] == "close_account"
    assert events == [("account", True, False)]


def test_app_start_uses_fixed_application_path_and_never_shell(monkeypatch):
    from app.integrations.yitaojin.runtime import ensure_yitaojin_app_ready

    bridge = _FakeBridge(
        [
            {
                "appRunning": False,
                "applicationPathValid": False,
                "accessibilityTrusted": True,
                "loginState": "unknown",
                "interfaceSignature": None,
            },
            {
                "appRunning": True,
                "applicationPathValid": True,
                "accessibilityTrusted": True,
                "loginState": "logged_in",
                "interfaceSignature": "sha256:ui",
            },
        ]
    )
    commands = []

    def runner(args, **kwargs):
        commands.append((args, kwargs))
        return SimpleNamespace(returncode=0)

    probe = ensure_yitaojin_app_ready(
        bridge,
        app_path="/Applications/GF-Trader.app",
        open_runner=runner,
        sleep=lambda _: None,
        start_timeout_seconds=1,
    )

    assert probe["loginState"] == "logged_in"
    assert commands == [
        (
            ["open", "-a", "/Applications/GF-Trader.app"],
            {
                "check": False,
                "capture_output": True,
                "text": True,
                "timeout": 10,
            },
        )
    ]


@pytest.mark.parametrize(
    ("probe", "reason"),
    [
        (
            {
                "appRunning": True,
                "applicationPathValid": False,
                "accessibilityTrusted": True,
                "loginState": "logged_in",
            },
            "application_path_mismatch",
        ),
        (
            {
                "appRunning": True,
                "applicationPathValid": True,
                "accessibilityTrusted": False,
                "loginState": "unknown",
            },
            "accessibility_permission_missing",
        ),
        (
            {
                "appRunning": True,
                "applicationPathValid": True,
                "accessibilityTrusted": True,
                "loginState": "not_logged_in",
            },
            "not_logged_in",
        ),
    ],
)
def test_running_app_must_match_path_permission_and_login_without_auth_attempt(
    probe,
    reason,
):
    from app.integrations.yitaojin.runtime import (
        RuntimeTaskError,
        ensure_yitaojin_app_ready,
    )

    with pytest.raises(RuntimeTaskError, match=reason):
        ensure_yitaojin_app_ready(
            _FakeBridge([probe]),
            open_runner=lambda *args, **kwargs: (_ for _ in ()).throw(
                AssertionError("must not start or authenticate an already running app")
            ),
        )


@pytest.mark.asyncio
async def test_runtime_failure_is_sanitized_and_preserves_last_success(tmp_path):
    from app.integrations.yitaojin.runtime import (
        load_yitaojin_runtime_status,
        run_yitaojin_task,
    )

    events = []
    paths, services = _services(tmp_path, events)
    success = await run_yitaojin_task(
        "evening",
        environment={"CONGXI_YITAOJIN_ENABLED": "true"},
        paths=paths,
        services_factory=lambda *_: services,
    )
    failed_services = SimpleNamespace(
        **{
            **services.__dict__,
            "account": _FakeAccount(
                events,
                result=_FakeResult(
                    "blocked",
                    ("account_fingerprint_mismatch",),
                ),
            ),
        }
    )
    failed = await run_yitaojin_task(
        "close_account",
        environment={"CONGXI_YITAOJIN_ENABLED": "true"},
        paths=paths,
        services_factory=lambda *_: failed_services,
    )
    persisted = load_yitaojin_runtime_status(
        paths.runtime_status,
        environment={"CONGXI_YITAOJIN_ENABLED": "true"},
    )

    assert success["last_success_at"]
    assert failed["state"] == "failed"
    assert failed["last_success_at"] == success["last_success_at"]
    assert failed["last_failure_reason"] == (
        "account_blocked:account_fingerprint_mismatch"
    )
    assert failed["bridge_build_id"] == "sha256:test-build"
    serialized = json.dumps(persisted)
    assert "sha256:" + "a" * 64 not in serialized
    assert "total_assets" not in serialized


@pytest.mark.asyncio
async def test_main_wrapper_contains_runtime_failure_instead_of_aborting(monkeypatch):
    import app.integrations.yitaojin.runtime as runtime
    import app.main as main_module

    async def fail(_task):
        raise RuntimeError("SECRET-ACCOUNT-DATA")

    monkeypatch.setattr(runtime, "run_yitaojin_task", fail)

    result = await main_module._run_yitaojin_task_with_status("morning")

    assert result["state"] == "failed"
    assert result["last_failure_reason"] == "RuntimeError"
    assert "SECRET-ACCOUNT-DATA" not in json.dumps(result)


def test_scheduler_registers_bounded_jobs_without_second_intraday_cron():
    import app.main as main_module

    class FakeScheduler:
        def __init__(self):
            self.jobs = []

        def add_job(self, function, trigger, **kwargs):
            self.jobs.append((function, trigger, kwargs))

    scheduler = FakeScheduler()
    main_module.register_yitaojin_jobs(scheduler)
    by_id = {
        kwargs["id"]: (function, trigger, kwargs)
        for function, trigger, kwargs in scheduler.jobs
    }

    assert "hour='8', minute='55'" in str(by_id["yitaojin_morning"][1])
    assert "hour='11', minute='35'" in str(by_id["yitaojin_midday_quotes"][1])
    assert "hour='14', minute='55'" in str(by_id["yitaojin_close_quotes"][1])
    assert "hour='15', minute='10'" in str(by_id["yitaojin_close_account"][1])
    assert "hour='20', minute='45'" in str(by_id["yitaojin_evening"][1])
    assert all(item[2]["max_instances"] == 1 for item in by_id.values())
    assert all(item[2]["coalesce"] is True for item in by_id.values())
    assert not any("*/5" in str(trigger) for _, trigger, _ in scheduler.jobs)


def test_intraday_quote_validation_reuses_existing_five_minute_scan():
    import ast

    source = Path("backend/app/main.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == "_run_intraday_alert_scan_with_status"
    )
    function_source = ast.get_source_segment(source, function) or ""

    assert "_run_yitaojin_quotes_with_status" in function_source
    assert "create_task" in function_source


def test_live_quote_summary_reopens_only_the_quote_veto_and_keeps_hard_risk():
    import app.main as main_module

    summary = {
        "enabled": True,
        "status": "ok",
        "validations": {
            "600000": {
                "status": "fresh",
                "blocks_new_entry": False,
            }
        },
    }
    quote_only = {
        "entry_allowed": False,
        "state": "blocked",
        "reasons": ["quote_validation_blocked"],
    }
    reopened, validations = main_module._runtime_quote_gate_and_validations(
        quote_only,
        positions={},
        quote_summary=summary,
    )

    assert reopened["entry_allowed"] is True
    assert reopened["state"] == "allowed"
    assert validations == summary["validations"]

    hard_risk = {
        **quote_only,
        "reasons": ["hard_risk_veto", "quote_validation_blocked"],
    }
    still_blocked, _ = main_module._runtime_quote_gate_and_validations(
        hard_risk,
        positions={},
        quote_summary=summary,
    )

    assert still_blocked["entry_allowed"] is False
    assert still_blocked["reasons"] == ["hard_risk_veto"]

    disabled, disabled_validations = (
        main_module._runtime_quote_gate_and_validations(
            quote_only,
            positions={},
            quote_summary={
                "enabled": False,
                "status": "not_enabled",
                "validations": {},
            },
        )
    )
    assert disabled["entry_allowed"] is True
    assert disabled["reasons"] == []
    assert disabled_validations is None


@pytest.mark.asyncio
async def test_candidate_scan_consumes_structured_live_quote_validations(monkeypatch):
    import app.main as main_module

    captured = {}
    validation = {
        "600000": {
            "status": "fresh",
            "blocks_new_entry": False,
        }
    }

    async def fake_evaluate(*args, **kwargs):
        captured.update(kwargs)
        return {"scanned": 1, "alerts": []}

    monkeypatch.setattr(main_module, "evaluate_candidate_pool", fake_evaluate)
    monkeypatch.setattr(
        main_module,
        "_runtime_quote_gate_and_validations",
        lambda gate, positions: (gate, validation),
    )

    await main_module._scan_candidate_pool_and_push(
        "盘中",
        available_cash=3000,
        total_assets=3000,
        positions={},
        entry_gate={
            "entry_allowed": True,
            "state": "allowed",
            "reasons": [],
        },
    )

    assert captured["quote_validations"] == validation
    assert captured["entry_gate"]["entry_allowed"] is True


def test_runtime_paths_and_launchd_keep_integration_disabled_by_default(
    tmp_path,
    monkeypatch,
):
    from app.config import resolve_runtime_yitaojin_paths

    monkeypatch.setenv("CONGXI_STATE_DIR", str(tmp_path))
    paths = resolve_runtime_yitaojin_paths()
    launchd = Path("scripts/install-congxicai-v7-launchd.sh").read_text(
        encoding="utf-8"
    )

    assert paths.runtime_status == tmp_path / "yitaojin" / "runtime_status.json"
    assert 'CONGXI_YITAOJIN_ENABLED="${CONGXI_YITAOJIN_ENABLED:-false}"' in launchd
    assert (
        'CONGXI_YITAOJIN_WRITE_ENABLED="${CONGXI_YITAOJIN_WRITE_ENABLED:-false}"'
        in launchd
    )
    assert "/Applications/GF-Trader.app" in launchd


def test_user_launchd_enables_account_and_watchlist_specific_writes():
    import plistlib

    launchd_plist = plistlib.loads(Path(
        "scripts/com.zhuchenyuan.congxicai-v7.plist"
    ).read_bytes())
    environment = launchd_plist["EnvironmentVariables"]

    assert environment["CONGXI_YITAOJIN_ENABLED"] == "true"
    assert environment["CONGXI_YITAOJIN_ACCOUNT_WRITE_ENABLED"] == "true"
    assert environment["CONGXI_YITAOJIN_WATCHLIST_WRITE_ENABLED"] == "true"
    assert environment["CONGXI_YITAOJIN_WRITE_ENABLED"] == "false"
