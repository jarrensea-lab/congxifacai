from __future__ import annotations

from argparse import Namespace
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest


def test_cli_defaults_to_dry_run_and_exposes_no_arbitrary_code_argument():
    """Catches a convenient but unsafe direct-code write escape hatch."""
    from app.integrations.yitaojin.cli import build_parser

    args = build_parser().parse_args(["watchlist"])

    assert args.apply is False
    assert args.bootstrap is False
    assert not hasattr(args, "code")


def test_cli_rejects_conflicting_apply_and_dry_run_flags():
    """Catches ambiguous mutation intent at the command line."""
    from app.integrations.yitaojin.cli import build_parser

    with pytest.raises(SystemExit):
        build_parser().parse_args(["account", "--apply", "--dry-run"])


def test_account_cli_payload_omits_account_fingerprint_and_raw_identity():
    """Catches a durable account identifier leaking into shell output."""
    from app.integrations.yitaojin.cli import account_result_payload
    from app.integrations.yitaojin.models import AccountSnapshot

    snapshot = AccountSnapshot(
        captured_at=datetime.fromisoformat("2026-07-27T20:45:00+08:00"),
        account_fingerprint="sha256:" + "a" * 64,
        total_assets=Decimal("1000"),
        available_cash=Decimal("1000"),
        frozen_cash=Decimal("0"),
        positions=(),
        empty_positions_confirmed=True,
    )
    result = SimpleNamespace(
        status="validated",
        requested_apply=False,
        applied=False,
        requires_manual_review=False,
        reasons=(),
        snapshot=snapshot,
        diff=None,
    )

    payload = account_result_payload(result)

    assert payload["status"] == "validated"
    assert "account_fingerprint" not in str(payload)
    assert "sha256:" not in str(payload)


def test_parser_probe_has_no_mutation_flags():
    """Catches write semantics accidentally being attached to a read-only probe."""
    from app.integrations.yitaojin.cli import build_parser

    args: Namespace = build_parser().parse_args(["probe", "--json"])

    assert args.command == "probe"
    assert args.json is True
    assert not hasattr(args, "apply")


def test_cli_apply_requires_both_enable_flags_before_account_service(
    monkeypatch,
):
    """Catches account apply bypassing the shared two-switch mutation gate."""
    from app.integrations.yitaojin import cli

    calls = []

    class FakeAccountService:
        def sync_account(self, **kwargs):
            calls.append(kwargs)
            raise AssertionError("account service must not run")

    monkeypatch.setattr(
        cli,
        "_build_services",
        lambda: (object(), FakeAccountService(), object()),
    )
    monkeypatch.setenv("CONGXI_YITAOJIN_ENABLED", "true")
    monkeypatch.delenv("CONGXI_YITAOJIN_WRITE_ENABLED", raising=False)
    args = cli.build_parser().parse_args(["account", "--apply"])

    payload, exit_code = cli.run(args)

    assert exit_code == 2
    assert payload["reasons"] == ["write_disabled"]
    assert calls == []
