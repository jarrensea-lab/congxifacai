"""Command-line entrypoint for manual Yitaojin sync and diagnostics."""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Sequence


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="广发易淘金安全桥：默认只预演，不提供任意代码写入口。",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    probe = subparsers.add_parser("probe", help="只读检查 App 与辅助功能状态")
    probe.add_argument("--json", action="store_true", help="输出 JSON")

    for name, help_text in (
        ("account", "校验或同步账户持仓快照"),
        ("watchlist", "预演或同步目标池到自选股"),
    ):
        command = subparsers.add_parser(name, help=help_text)
        mode = command.add_mutually_exclusive_group()
        mode.add_argument(
            "--apply",
            action="store_true",
            help="明确请求落地；仍受环境开关与安全状态机约束",
        )
        mode.add_argument(
            "--dry-run",
            dest="apply",
            action="store_false",
            help="只校验/预演（默认）",
        )
        command.set_defaults(apply=False)
        command.add_argument(
            "--bootstrap",
            action="store_true",
            help="首次绑定账户或保护现有自选股，不执行自选股增删",
        )
        command.add_argument("--json", action="store_true", help="输出 JSON")
    return parser


def _json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    return value


def account_result_payload(result) -> dict[str, Any]:
    snapshot = result.snapshot
    snapshot_summary = None
    if snapshot is not None:
        snapshot_summary = {
            "captured_at": snapshot.captured_at,
            "total_assets": snapshot.total_assets,
            "available_cash": snapshot.available_cash,
            "frozen_cash": snapshot.frozen_cash,
            "position_count": len(snapshot.positions),
            "positions": [
                {
                    "code": position.code,
                    "name": position.name,
                    "shares": position.shares,
                    "available_shares": position.available_shares,
                }
                for position in snapshot.positions
            ],
        }
    return _json_safe(
        {
            "status": result.status,
            "requested_apply": result.requested_apply,
            "applied": result.applied,
            "requires_manual_review": result.requires_manual_review,
            "reasons": result.reasons,
            "snapshot": snapshot_summary,
            "diff": asdict(result.diff) if result.diff is not None else None,
        }
    )


def watchlist_result_payload(result) -> dict[str, Any]:
    return _json_safe(
        {
            "status": result.status,
            "requested_apply": result.requested_apply,
            "applied": result.applied,
            "reasons": result.reasons,
            "plan": result.plan.to_dict() if result.plan is not None else None,
            "actions": [asdict(action) for action in result.actions],
        }
    )


def _blocked_payload(reason: str) -> dict[str, Any]:
    return {
        "status": "blocked",
        "requested_apply": False,
        "applied": False,
        "reasons": [reason],
    }


def _emit(payload: dict[str, Any], *, as_json: bool) -> None:
    safe = _json_safe(payload)
    if as_json:
        print(
            json.dumps(
                safe,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
                allow_nan=False,
            )
        )
        return
    reasons = "、".join(str(item) for item in safe.get("reasons") or [])
    message = f"状态：{safe.get('status', 'unknown')}"
    if reasons:
        message += f"；原因：{reasons}"
    print(message)
    plan = safe.get("plan")
    if isinstance(plan, dict):
        print(
            "自选股计划："
            f"新增 {len(plan.get('add') or [])}，"
            f"删除 {len(plan.get('remove') or [])}，"
            f"保留 {len(plan.get('keep') or [])}"
        )


def _build_services():
    from app.config import resolve_runtime_yitaojin_paths
    from app.integrations.yitaojin.account import YitaojinAccountService
    from app.integrations.yitaojin.bridge import YitaojinBridge
    from app.integrations.yitaojin.service import YitaojinSyncService
    from app.integrations.yitaojin.state import YitaojinStateStore
    from app.services.portfolio_store import default_portfolio_path
    from app.services.quant_lifecycle import default_candidate_pool_path

    paths = resolve_runtime_yitaojin_paths()
    bridge = YitaojinBridge(paths.bridge)
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
        candidate_pool_path=default_candidate_pool_path(),
    )
    return bridge, account, watchlist


def run(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    from app.integrations.yitaojin.models import BridgeCommand, YitaojinError

    try:
        bridge, account_service, watchlist_service = _build_services()
        if args.command == "probe":
            return {
                "status": "ok",
                "requested_apply": False,
                "applied": False,
                "reasons": [],
                "probe": dict(bridge.run(BridgeCommand.PROBE)),
            }, 0

        enabled = (
            os.environ.get("CONGXI_YITAOJIN_ENABLED", "").strip().lower() == "true"
        )
        if not enabled:
            return _blocked_payload("integration_disabled"), 2
        write_enabled = (
            os.environ.get("CONGXI_YITAOJIN_WRITE_ENABLED", "").strip().lower()
            == "true"
        )
        if args.apply and not write_enabled:
            return _blocked_payload("write_disabled"), 2

        if args.command == "account":
            result = account_service.sync_account(
                apply=args.apply,
                bootstrap=args.bootstrap,
            )
            payload = account_result_payload(result)
            return payload, 0 if result.status in {"validated", "applied"} else 2

        account_result = account_service.sync_account(
            apply=False,
            bootstrap=args.bootstrap,
        )
        if account_result.status != "validated":
            payload = account_result_payload(account_result)
            payload["watchlist_status"] = "not_started"
            return payload, 2
        result = watchlist_service.sync_watchlist(
            apply=args.apply,
            bootstrap=args.bootstrap,
        )
        payload = watchlist_result_payload(result)
        return payload, 0 if result.status in {
            "planned",
            "bootstrapped",
            "applied",
        } else 2
    except (YitaojinError, OSError, ValueError) as exc:
        return _blocked_payload(exc.__class__.__name__), 2


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload, exit_code = run(args)
    _emit(payload, as_json=args.json)
    return exit_code
