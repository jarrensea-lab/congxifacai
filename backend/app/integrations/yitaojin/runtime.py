"""Async-safe runtime orchestration for scheduled Yitaojin tasks."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping
from zoneinfo import ZoneInfo

from app.integrations.yitaojin.models import (
    AccountSnapshot,
    BridgeCommand,
    SnapshotValidationError,
)


PROJECT_TIMEZONE = ZoneInfo("Asia/Shanghai")
EXPECTED_APP_PATH = "/Applications/GF-Trader.app"
SUPPORTED_TASKS = frozenset(
    {
        "morning",
        "close_account",
        "evening",
        "priority_quotes",
        "intraday_quotes",
    }
)


class RuntimeTaskError(RuntimeError):
    """A safe, non-sensitive runtime failure code."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class RuntimeServices:
    bridge: Any
    account: Any
    watchlist: Any
    quotes: Any
    account_snapshot_path: Path
    candidate_pool_path: Path
    bridge_build_id: str | None


def _enabled(environment: Mapping[str, str], name: str) -> bool:
    return str(environment.get(name) or "").strip().lower() == "true"


def _specific_write_enabled(
    environment: Mapping[str, str],
    name: str,
) -> bool:
    if name in environment:
        return _enabled(environment, name)
    return _enabled(environment, "CONGXI_YITAOJIN_WRITE_ENABLED")


def _positive_number(
    environment: Mapping[str, str],
    name: str,
    default: float,
) -> float:
    try:
        value = float(environment.get(name) or default)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _now_text() -> str:
    return datetime.now(tz=PROJECT_TIMEZONE).isoformat(timespec="seconds")


def _default_status(
    environment: Mapping[str, str],
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "enabled": _enabled(environment, "CONGXI_YITAOJIN_ENABLED"),
        "write_enabled": _enabled(
            environment,
            "CONGXI_YITAOJIN_WRITE_ENABLED",
        ),
        "state": "unknown",
        "task": None,
        "updated_at": None,
        "last_success_at": None,
        "last_failure_reason": None,
        "bridge_build_id": None,
        "steps": {},
    }


def load_yitaojin_runtime_status(
    path: str | Path,
    *,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Load only the allow-listed, non-account runtime health fields."""
    effective_environment = dict(os.environ if environment is None else environment)
    status = _default_status(effective_environment)
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return status
    if not isinstance(raw, Mapping) or raw.get("schema_version") != 1:
        return status
    for field in (
        "state",
        "task",
        "updated_at",
        "last_success_at",
        "last_failure_reason",
        "bridge_build_id",
    ):
        value = raw.get(field)
        if value is None or isinstance(value, str):
            status[field] = value
    raw_steps = raw.get("steps")
    if isinstance(raw_steps, Mapping):
        status["steps"] = {
            str(name): str(value)
            for name, value in raw_steps.items()
            if isinstance(name, str) and isinstance(value, str)
        }
    return status


def _atomic_status_write(path: Path, status: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(
        status,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
        allow_nan=False,
    )
    descriptor, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(encoded)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


def _bridge_build_id(path: Path) -> str | None:
    try:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return None
    return "sha256:" + digest.hexdigest()[:16]


def build_runtime_services(paths, environment: Mapping[str, str]) -> RuntimeServices:
    """Build concrete services only after the integration enable gate."""
    from app.integrations.yitaojin.account import YitaojinAccountService
    from app.integrations.yitaojin.bridge import YitaojinBridge
    from app.integrations.yitaojin.quotes import YitaojinQuoteService
    from app.integrations.yitaojin.service import YitaojinSyncService
    from app.integrations.yitaojin.state import YitaojinStateStore
    from app.services.portfolio_store import default_portfolio_path
    from app.services.quant_lifecycle import default_candidate_pool_path

    bridge = YitaojinBridge(paths.bridge)
    candidate_pool_path = default_candidate_pool_path()
    account = YitaojinAccountService(
        bridge=bridge,
        account_snapshot_path=paths.account_snapshot,
        fingerprint_salt_path=paths.account_fingerprint_salt,
        audit_path=paths.account_audit,
        portfolio_path=default_portfolio_path(),
    )
    watchlist = YitaojinSyncService(
        bridge=bridge,
        state_store=YitaojinStateStore(
            paths.watchlist_state,
            audit_path=paths.watchlist_audit,
        ),
        account_snapshot_path=paths.account_snapshot,
        candidate_pool_path=candidate_pool_path,
        environment=environment,
    )
    quotes = YitaojinQuoteService(
        bridge=bridge,
        snapshot_path=paths.quote_snapshot,
        environment=environment,
    )
    return RuntimeServices(
        bridge=bridge,
        account=account,
        watchlist=watchlist,
        quotes=quotes,
        account_snapshot_path=paths.account_snapshot,
        candidate_pool_path=candidate_pool_path,
        bridge_build_id=_bridge_build_id(Path(paths.bridge)),
    )


def _validate_ready_probe(probe: Mapping[str, Any]) -> None:
    if probe.get("applicationPathValid") is not True:
        raise RuntimeTaskError("application_path_mismatch")
    if probe.get("accessibilityTrusted") is not True:
        raise RuntimeTaskError("accessibility_permission_missing")
    if probe.get("loginState") != "logged_in":
        raise RuntimeTaskError("not_logged_in")


def ensure_yitaojin_app_ready(
    bridge,
    *,
    app_path: str = EXPECTED_APP_PATH,
    open_runner: Callable[..., Any] = subprocess.run,
    sleep: Callable[[float], None] = time.sleep,
    start_timeout_seconds: float = 30,
) -> Mapping[str, Any]:
    """Probe the exact app, optionally start it, and never attempt authentication."""
    if app_path != EXPECTED_APP_PATH:
        raise RuntimeTaskError("application_path_not_allowed")
    first = bridge.run(BridgeCommand.PROBE, timeout=15.0)
    if first.get("appRunning") is True:
        _validate_ready_probe(first)
        return first

    completed = open_runner(
        ["open", "-a", EXPECTED_APP_PATH],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if getattr(completed, "returncode", 1) != 0:
        raise RuntimeTaskError("application_start_failed")

    deadline = time.monotonic() + start_timeout_seconds
    while True:
        probe = bridge.run(BridgeCommand.PROBE, timeout=15.0)
        if probe.get("appRunning") is True:
            if (
                probe.get("applicationPathValid") is True
                and probe.get("accessibilityTrusted") is True
                and probe.get("loginState") == "logged_in"
            ):
                return probe
            if probe.get("loginState") == "not_logged_in":
                raise RuntimeTaskError("not_logged_in")
        if time.monotonic() >= deadline:
            raise RuntimeTaskError("application_start_timeout")
        sleep(min(1.0, max(0.0, deadline - time.monotonic())))


def _load_quote_inputs(
    services: RuntimeServices,
) -> tuple[Mapping[str, Any], tuple[Any, ...], set[str]]:
    from app.integrations.yitaojin.planner import PRODUCTION_STATUSES

    try:
        account_payload = json.loads(
            Path(services.account_snapshot_path).read_text(encoding="utf-8")
        )
        pool = json.loads(
            Path(services.candidate_pool_path).read_text(encoding="utf-8")
        )
        account = AccountSnapshot.from_persisted_dict(account_payload)
    except (
        OSError,
        json.JSONDecodeError,
        SnapshotValidationError,
        TypeError,
    ) as exc:
        raise RuntimeTaskError("quote_inputs_unavailable") from exc
    if not isinstance(pool, Mapping):
        raise RuntimeTaskError("candidate_pool_invalid")
    raw_items = pool.get("items")
    if isinstance(raw_items, Mapping):
        items = [
            (str(code), item)
            for code, item in raw_items.items()
            if isinstance(item, Mapping)
        ]
    elif isinstance(raw_items, list):
        items = [
            (str(item.get("code") or ""), item)
            for item in raw_items
            if isinstance(item, Mapping)
        ]
    else:
        raise RuntimeTaskError("candidate_pool_invalid")
    critical_codes = {
        str(item.get("code") or fallback).strip()
        for fallback, item in items
        if str(item.get("status") or "").strip().lower() == "executable"
    }
    critical_codes = {
        code for code in critical_codes if len(code) == 6 and code.isdigit()
    }
    return pool, account.positions, critical_codes


def _result_status(result: Any) -> str:
    return str(getattr(result, "status", "") or "").strip().lower()


def _result_reasons(result: Any) -> tuple[str, ...]:
    reasons = getattr(result, "reasons", ())
    if not isinstance(reasons, (tuple, list)):
        return ()
    return tuple(str(reason) for reason in reasons if isinstance(reason, str))


def _require_step(
    name: str,
    result: Any,
    allowed: set[str],
) -> str:
    status = _result_status(result)
    if status in allowed:
        return status
    reasons = _result_reasons(result)
    suffix = reasons[0] if reasons else (status or "unknown")
    raise RuntimeTaskError(f"{name}_{status or 'failed'}:{suffix}")


async def _offload(
    function: Callable[..., Any],
    /,
    *args,
    timeout: float,
    **kwargs,
) -> Any:
    return await asyncio.wait_for(
        asyncio.to_thread(function, *args, **kwargs),
        timeout=timeout,
    )


async def _refresh_quotes(
    services: RuntimeServices,
    *,
    market_source,
    timeout: float,
) -> str:
    from app.integrations.yitaojin.quotes import collect_quote_codes

    pool, positions, critical_codes = await _offload(
        _load_quote_inputs,
        services,
        timeout=timeout,
    )
    codes = collect_quote_codes(pool, positions)
    references = {}
    if len(codes) <= 32:
        references = await asyncio.wait_for(
            market_source.fetch_batch(list(codes)),
            timeout=min(timeout, 15.0),
        )
    result = await _offload(
        services.quotes.refresh,
        pool_payload=pool,
        positions=positions,
        reference_quotes=references,
        critical_codes=critical_codes,
        timeout=timeout,
    )
    return _require_step("quotes", result, {"ok", "blocked"})


async def run_yitaojin_task(
    task: str,
    *,
    environment: Mapping[str, str] | None = None,
    paths=None,
    services_factory: Callable[..., RuntimeServices] | None = None,
    market_source=None,
) -> dict[str, Any]:
    """Run one bounded broker task without blocking the FastAPI event loop."""
    if task not in SUPPORTED_TASKS:
        raise ValueError("unsupported yitaojin runtime task")
    from app.config import resolve_runtime_yitaojin_paths

    effective_environment = dict(os.environ if environment is None else environment)
    effective_paths = paths or resolve_runtime_yitaojin_paths()
    status_path = Path(effective_paths.runtime_status)
    previous = load_yitaojin_runtime_status(
        status_path,
        environment=effective_environment,
    )
    enabled = _enabled(effective_environment, "CONGXI_YITAOJIN_ENABLED")
    account_write_enabled = _specific_write_enabled(
        effective_environment,
        "CONGXI_YITAOJIN_ACCOUNT_WRITE_ENABLED",
    )
    watchlist_write_enabled = _specific_write_enabled(
        effective_environment,
        "CONGXI_YITAOJIN_WATCHLIST_WRITE_ENABLED",
    )
    write_enabled = account_write_enabled or watchlist_write_enabled
    if not enabled:
        disabled = {
            **previous,
            "enabled": False,
            "write_enabled": write_enabled,
            "state": "disabled",
            "task": task,
            "updated_at": _now_text(),
            "steps": {},
        }
        _atomic_status_write(status_path, disabled)
        return disabled

    timeout = _positive_number(
        effective_environment,
        "CONGXI_YITAOJIN_TASK_TIMEOUT_SECONDS",
        120.0,
    )
    start_timeout = _positive_number(
        effective_environment,
        "CONGXI_YITAOJIN_APP_START_TIMEOUT_SECONDS",
        30.0,
    )
    app_path = str(
        effective_environment.get("CONGXI_YITAOJIN_APP_PATH") or EXPECTED_APP_PATH
    )
    running = {
        **previous,
        "enabled": True,
        "write_enabled": write_enabled,
        "state": "running",
        "task": task,
        "updated_at": _now_text(),
        "last_failure_reason": None,
        "steps": {},
    }
    _atomic_status_write(status_path, running)
    steps: dict[str, str] = {}
    services: RuntimeServices | None = None
    try:
        factory = services_factory or build_runtime_services
        services = await _offload(
            factory,
            effective_paths,
            effective_environment,
            timeout=min(timeout, 15.0),
        )
        running["bridge_build_id"] = services.bridge_build_id
        await _offload(
            ensure_yitaojin_app_ready,
            services.bridge,
            app_path=app_path,
            start_timeout_seconds=start_timeout,
            timeout=min(timeout, start_timeout + 20.0),
        )
        steps["probe"] = "ready"

        if task == "close_account":
            account_result = await _offload(
                services.account.sync_account,
                apply=account_write_enabled,
                bootstrap=False,
                timeout=timeout,
            )
            steps["account"] = _require_step(
                "account",
                account_result,
                {"validated", "applied"},
            )
        if task in {"morning", "evening"}:
            watchlist_result = await _offload(
                services.watchlist.sync_watchlist,
                apply=watchlist_write_enabled,
                bootstrap=False,
                timeout=timeout,
            )
            steps["watchlist"] = _require_step(
                "watchlist",
                watchlist_result,
                {"planned", "applied", "bootstrapped"},
            )

        if task in {"morning", "priority_quotes", "intraday_quotes"}:
            if market_source is None:
                from app.data_sources.realtime_market_data import (
                    FastRealtimeMarketDataSource,
                )

                market_source = FastRealtimeMarketDataSource()
            steps["quotes"] = await _refresh_quotes(
                services,
                market_source=market_source,
                timeout=timeout,
            )

        success_at = _now_text()
        success = {
            **running,
            "state": "success",
            "updated_at": success_at,
            "last_success_at": success_at,
            "last_failure_reason": None,
            "steps": steps,
        }
        _atomic_status_write(status_path, success)
        return success
    except RuntimeTaskError as exc:
        reason = exc.reason
    except asyncio.TimeoutError:
        reason = "task_timeout"
    except Exception as exc:  # noqa: BLE001 - status must fail closed and stay sanitized
        reason = exc.__class__.__name__

    failed = {
        **running,
        "bridge_build_id": (
            services.bridge_build_id
            if services is not None
            else running.get("bridge_build_id")
        ),
        "state": "failed",
        "updated_at": _now_text(),
        "last_failure_reason": reason,
        "steps": steps,
    }
    _atomic_status_write(status_path, failed)
    return failed
