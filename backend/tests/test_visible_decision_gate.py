import json
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import pytest


def _gate(**overrides):
    gate = {
        "report_date": "2026-07-21",
        "target_date": "2026-07-22",
        "generated_at": "2026-07-21T20:00:00+08:00",
        "entry_allowed": False,
        "reasons": ["main_report_entry_not_triggered"],
        "state": "blocked",
    }
    gate.update(overrides)
    return gate


def test_visible_decision_gate_degrades_entry_only_and_preserves_risk_actions():
    from app.services.visible_decision_gate import apply_visible_decision_gate

    decision = {
        "target_scores": [
            {"code": "000001", "action": "buy", "position_amount": 1000},
            {"code": "000002", "action": "sell", "position_amount": 500},
            {"code": "000003", "action": "stop_loss"},
        ],
        "outside_pool_scan": [
            {
                "code": "000004",
                "action": "actionable",
                "suggested_amount": 900,
                "lot_value": 900,
            },
            {"code": "000005", "action": "entry_cancelled"},
        ],
    }

    visible = apply_visible_decision_gate(decision, _gate())

    assert visible["target_scores"][0]["action"] == "watching"
    assert visible["target_scores"][0]["position_amount"] == 0
    assert visible["target_scores"][1]["action"] == "sell"
    assert visible["target_scores"][2]["action"] == "stop_loss"
    assert visible["outside_pool_scan"][0]["action"] == "watching"
    assert visible["outside_pool_scan"][0]["suggested_amount"] == 0
    assert visible["outside_pool_scan"][0]["entry_allowed"] is False
    assert visible["outside_pool_scan"][1]["action"] == "entry_cancelled"

    from scripts.daily_report import _new_entry_action_lines

    entry_lines = "\n".join(_new_entry_action_lines(visible))
    assert "建议试仓金额：一手试错" not in entry_lines
    assert "今天不主动买入" in entry_lines


def test_quote_validation_blocks_entry_but_preserves_risk_actions():
    """Catches quote failure hiding a stop-loss or leaving a buy executable."""
    from app.services.visible_decision_gate import (
        apply_visible_decision_gate,
        build_visible_decision_gate,
    )

    decision = {
        "target_scores": [
            {"code": "600000", "action": "buy", "position_amount": 1000},
            {"code": "000001", "action": "stop_loss"},
        ]
    }
    quote_validation = {
        "enabled": True,
        "status": "blocked",
        "as_of": "2026-07-21T19:59:00+08:00",
        "validations": {
            "600000": {
                "code": "600000",
                "status": "stale",
                "market_time": "2026-07-21T19:58:00+08:00",
                "blocks_new_entry": True,
                "requires_manual_price_check": True,
                "reasons": ["quote_stale"],
            },
            "000001": {
                "code": "000001",
                "status": "conflict",
                "market_time": "2026-07-21T19:59:00+08:00",
                "blocks_new_entry": True,
                "requires_manual_price_check": True,
                "reasons": ["price_divergence"],
            },
        },
    }

    gate = build_visible_decision_gate(
        report_date="2026-07-21",
        target_date="2026-07-22",
        decision=decision,
        quote_validation=quote_validation,
    )
    visible = apply_visible_decision_gate(decision, gate)

    assert gate["entry_allowed"] is False
    assert "quote_validation_blocked" in gate["reasons"]
    assert visible["target_scores"][0]["action"] == "watching"
    assert visible["target_scores"][0]["quote_status"] == "stale"
    assert visible["target_scores"][0]["execution_blocked_reason"] == (
        "quote_validation_blocked"
    )
    assert visible["target_scores"][1]["action"] == "stop_loss"
    assert visible["target_scores"][1]["quote_status"] == "conflict"
    assert visible["target_scores"][1]["requires_manual_price_check"] is True


def test_quote_not_enabled_is_visible_but_keeps_existing_entry_path():
    """Catches disabled integration pretending quotes were checked."""
    from app.services.visible_decision_gate import (
        apply_visible_decision_gate,
        build_visible_decision_gate,
    )

    decision = {
        "target_scores": [
            {"code": "600000", "action": "buy", "position_amount": 1000},
        ]
    }
    gate = build_visible_decision_gate(
        report_date="2026-07-21",
        target_date="2026-07-22",
        decision=decision,
        quote_validation={
            "enabled": False,
            "status": "not_enabled",
            "validations": {},
        },
    )
    visible = apply_visible_decision_gate(decision, gate)

    assert gate["entry_allowed"] is True
    assert visible["target_scores"][0]["action"] == "buy"
    assert visible["target_scores"][0]["quote_status"] == "not_enabled"
    assert visible["target_scores"][0]["requires_manual_price_check"] is False


def test_quote_reader_unavailable_blocks_buy_without_hiding_stop_loss():
    """Catches a broker read failure suppressing the holding risk path."""
    from app.services.visible_decision_gate import (
        apply_visible_decision_gate,
        build_visible_decision_gate,
    )

    decision = {
        "target_scores": [
            {"code": "600000", "action": "buy", "position_amount": 1000},
            {"code": "000001", "action": "stop_loss"},
        ]
    }
    gate = build_visible_decision_gate(
        report_date="2026-07-21",
        target_date="2026-07-22",
        decision=decision,
        quote_validation={
            "enabled": True,
            "status": "unavailable",
            "validations": {},
            "reasons": ["quote_read_failed"],
        },
    )
    visible = apply_visible_decision_gate(decision, gate)

    assert visible["target_scores"][0]["action"] == "watching"
    assert visible["target_scores"][0]["quote_status"] == "unavailable"
    assert visible["target_scores"][1]["action"] == "stop_loss"
    assert visible["target_scores"][1]["requires_manual_price_check"] is True


def test_visible_decision_gate_persists_atomically_and_has_target_date_validity(
    tmp_path,
):
    from app.services.visible_decision_gate import (
        load_effective_visible_decision_gate,
        write_visible_decision_gate,
    )

    path = tmp_path / "visible-decision.json"
    write_visible_decision_gate(_gate(), path=path)

    assert (
        load_effective_visible_decision_gate(path=path, today=date(2026, 7, 22))
        == _gate()
    )
    assert (
        load_effective_visible_decision_gate(path=path, today=date(2026, 7, 23)) is None
    )
    assert path.exists()
    assert not list(tmp_path.glob("*.tmp"))


def test_gate_schema_rejects_contradictions_unknown_reasons_and_invalid_time_order(
    tmp_path,
):
    from app.services.visible_decision_gate import (
        load_effective_visible_decision_gate,
        load_runtime_visible_decision_gate,
        write_visible_decision_gate,
    )

    path = tmp_path / "visible-decision.json"
    invalid_gates = [
        _gate(entry_allowed=True, state="blocked", reasons=[]),
        _gate(entry_allowed=True, state="allowed", reasons=["hard_risk_veto"]),
        _gate(entry_allowed=False, state="blocked", reasons=[]),
        _gate(entry_allowed=False, state="allowed", reasons=["hard_risk_veto"]),
        _gate(reasons=["unregistered_reason"]),
        _gate(report_date="2026-07-23", target_date="2026-07-22"),
        _gate(generated_at="2026-07-21T20:00:00"),
        _gate(
            generated_at="2099-07-21T20:00:00+08:00",
            entry_allowed=True,
            reasons=[],
            state="allowed",
        ),
        _gate(generated_at="2026-07-20T23:59:59+08:00"),
    ]
    for invalid in invalid_gates:
        path.write_text(json.dumps(invalid), encoding="utf-8")
        assert (
            load_effective_visible_decision_gate(path=path, today=date(2026, 7, 22))
            is None
        )
        runtime = load_runtime_visible_decision_gate(path=path, today=date(2026, 7, 22))
        assert runtime["entry_allowed"] is False
        assert runtime["reasons"] == ["visible_decision_gate_missing"]

    with pytest.raises(ValueError, match="visible_decision_gate_invalid"):
        write_visible_decision_gate(invalid_gates[0], path=path)


def test_gate_schema_accepts_generated_time_between_friday_report_and_monday_target(
    tmp_path,
):
    from app.services.visible_decision_gate import (
        load_effective_visible_decision_gate,
        write_visible_decision_gate,
    )

    path = tmp_path / "visible-decision.json"
    weekend_gate = _gate(
        report_date="2026-07-17",
        target_date="2026-07-20",
        generated_at="2026-07-18T01:30:00+08:00",
    )

    write_visible_decision_gate(weekend_gate, path=path)

    assert (
        load_effective_visible_decision_gate(path=path, today=date(2026, 7, 20))
        == weekend_gate
    )


def test_invalid_future_existing_gate_cannot_block_current_valid_replacement(tmp_path):
    from app.services.visible_decision_gate import write_visible_decision_gate

    path = tmp_path / "visible-decision.json"
    future = _gate(
        generated_at="2099-07-21T20:00:00+08:00",
        entry_allowed=True,
        reasons=[],
        state="allowed",
    )
    current = _gate(
        generated_at="2026-07-21T20:30:00+08:00", reasons=["hard_risk_veto"]
    )
    path.write_text(json.dumps(future), encoding="utf-8")

    write_visible_decision_gate(current, path=path)

    assert json.loads(path.read_text(encoding="utf-8")) == current


def test_gate_writer_keeps_newest_version_even_when_older_write_finishes_last(tmp_path):
    from app.services.visible_decision_gate import write_visible_decision_gate

    path = tmp_path / "visible-decision.json"
    older = _gate(generated_at="2026-07-21T19:00:00+08:00")
    newer = _gate(generated_at="2026-07-21T21:00:00+08:00", reasons=["hard_risk_veto"])

    write_visible_decision_gate(newer, path=path)
    write_visible_decision_gate(older, path=path)

    assert json.loads(path.read_text(encoding="utf-8")) == newer


def test_gate_writer_serializes_concurrent_writers_and_replaces_invalid_existing(
    tmp_path,
):
    from app.services.visible_decision_gate import write_visible_decision_gate

    path = tmp_path / "visible-decision.json"
    path.write_text("{invalid-json}\n", encoding="utf-8")
    gates = [
        _gate(generated_at=f"2026-07-21T{hour:02d}:00:00+08:00")
        for hour in range(16, 22)
    ]
    with ThreadPoolExecutor(max_workers=len(gates)) as executor:
        list(
            executor.map(
                lambda gate: write_visible_decision_gate(gate, path=path), gates
            )
        )

    assert json.loads(path.read_text(encoding="utf-8")) == gates[-1]


@pytest.mark.parametrize(
    "portfolio_truth",
    [
        {"pending_user_confirmed_fills": {}},
        {"pending_user_confirmed_fills": ["not-a-fill"]},
        {"trade_events": {}},
        {"trade_events": ["not-an-event"]},
        {"portfolio_truth": []},
        {"portfolio_writeback": "pending"},
        {"user_confirmed_fill": []},
        [],
    ],
)
def test_declared_portfolio_truth_with_invalid_shape_blocks_entry(portfolio_truth):
    from app.services.visible_decision_gate import build_visible_decision_gate

    gate = build_visible_decision_gate(
        report_date="2026-07-21",
        target_date="2026-07-22",
        decision={},
        portfolio_truth=portfolio_truth,
    )

    assert gate["entry_allowed"] is False
    assert "portfolio_truth_invalid" in gate["reasons"]


@pytest.mark.parametrize(
    ("decision", "portfolio_truth"),
    [
        ({"portfolio_sync_failed": True}, {}),
        ({}, {"portfolio_sync_failed": True, "portfolio_sync_status": "failed"}),
        ({}, {"portfolio_truth": {"sync_status": "error"}}),
    ],
)
def test_structured_portfolio_sync_failure_blocks_entry_but_preserves_risk_actions(
    decision,
    portfolio_truth,
):
    from app.services.visible_decision_gate import (
        apply_visible_decision_gate,
        build_visible_decision_gate,
    )

    report_decision = {
        **decision,
        "target_scores": [
            {"code": "000001", "action": "buy", "position_amount": 1000},
            {"code": "000002", "action": "sell", "position_amount": 500},
            {"code": "000003", "action": "stop_loss"},
            {"code": "000004", "action": "entry_cancelled"},
        ],
    }
    gate = build_visible_decision_gate(
        report_date="2026-07-21",
        target_date="2026-07-22",
        decision=report_decision,
        portfolio_truth=portfolio_truth,
    )
    visible = apply_visible_decision_gate(report_decision, gate)

    assert gate["entry_allowed"] is False
    assert "portfolio_sync_failed" in gate["reasons"]
    assert visible["target_scores"][0]["action"] == "watching"
    assert visible["target_scores"][0]["position_amount"] == 0
    assert [row["action"] for row in visible["target_scores"][1:]] == [
        "sell",
        "stop_loss",
        "entry_cancelled",
    ]


def test_stale_explicit_unsynced_gate_remains_blocking(tmp_path):
    from app.services.visible_decision_gate import (
        load_effective_visible_decision_gate,
        write_visible_decision_gate,
    )

    path = tmp_path / "visible-decision.json"
    unresolved = _gate(reasons=["portfolio_truth_unresolved"])
    write_visible_decision_gate(unresolved, path=path)

    assert (
        load_effective_visible_decision_gate(
            path=path,
            today=date(2026, 7, 23),
        )
        == unresolved
    )


def test_backend_main_gate_filters_entries_but_keeps_cancel_and_stop(tmp_path):
    from app.main import _filter_candidate_alerts_by_visible_gate
    from app.services.visible_decision_gate import write_visible_decision_gate

    path = tmp_path / "visible-decision.json"
    write_visible_decision_gate(_gate(), path=path)
    alerts = [
        {"code": "000001", "action": "actionable"},
        {"code": "000002", "action": "add_position"},
        {"code": "000003", "action": "buy"},
        {"code": "000004", "action": "add"},
        {"code": "000005", "action": "entry_cancelled"},
        {"code": "000006", "action": "stop_loss"},
        {"code": "000007", "action": "sell"},
        {"code": "000008", "action": "open_position"},
        {"code": "000009", "action": "watching"},
        {"code": "000010", "action": "unknown"},
    ]

    filtered = _filter_candidate_alerts_by_visible_gate(
        alerts,
        today=date(2026, 7, 22),
        gate_path=path,
    )

    assert [item["action"] for item in filtered] == [
        "entry_cancelled",
        "stop_loss",
        "sell",
    ]


def test_report_gate_downgrades_unknown_entry_action_but_preserves_explicit_non_entry_states():
    from app.services.visible_decision_gate import apply_visible_decision_gate

    decision = {
        "target_scores": [
            {"code": "000001", "action": "open_position", "position_amount": 1000},
            {"code": "000002", "action": "watching", "position_amount": 1000},
            {"code": "000003", "action": "hold", "position_amount": 1000},
            {"code": "000004", "action": "research_reference", "position_amount": 1000},
            {"code": "000005", "action": "removed", "position_amount": 1000},
            {"code": "000006", "status": "open_position", "position_amount": 1000},
        ],
    }

    visible = apply_visible_decision_gate(decision, _gate())

    assert visible["target_scores"][0]["action"] == "watching"
    assert visible["target_scores"][0]["position_amount"] == 0
    assert visible["target_scores"][-1]["action"] == "watching"
    assert visible["target_scores"][-1]["status"] == "watching"
    assert visible["target_scores"][-1]["position_amount"] == 0
    assert [row["action"] for row in visible["target_scores"][1:-1]] == [
        "watching",
        "hold",
        "research_reference",
        "removed",
    ]
    assert [row["position_amount"] for row in visible["target_scores"][1:-1]] == [
        1000
    ] * 4


def test_backend_main_missing_invalid_or_expired_gate_fails_closed_for_entry(tmp_path):
    from app.main import _filter_candidate_alerts_by_visible_gate
    from app.services.visible_decision_gate import write_visible_decision_gate

    path = tmp_path / "visible-decision.json"
    alerts = [
        {"code": "000001", "action": "buy"},
        {"code": "000002", "action": "stop_loss"},
        {"code": "000003", "action": "entry_cancelled"},
    ]
    assert (
        _filter_candidate_alerts_by_visible_gate(
            alerts,
            today=date(2026, 7, 22),
            gate_path=path,
        )
        == alerts[1:]
    )

    path.write_text("{not-json}\n", encoding="utf-8")
    assert (
        _filter_candidate_alerts_by_visible_gate(
            alerts,
            today=date(2026, 7, 22),
            gate_path=path,
        )
        == alerts[1:]
    )

    write_visible_decision_gate(_gate(), path=path)
    assert (
        _filter_candidate_alerts_by_visible_gate(
            alerts,
            today=date(2026, 7, 23),
            gate_path=path,
        )
        == alerts[1:]
    )


def test_blocked_gate_keeps_non_entry_safety_alerts():
    from app.services.visible_decision_gate import (
        filter_alerts_by_visible_decision_gate,
    )

    alerts = [
        {"code": "000001", "action": "actionable"},
        {"code": "000002", "action": "add_position"},
        {"code": "000003", "action": "blocked_chasing"},
        {"code": "000004", "action": "position_limit_reached"},
        {"code": "000005", "action": "risk_budget_too_small"},
        {"code": "000006", "action": "stop_loss"},
    ]

    filtered = filter_alerts_by_visible_decision_gate(
        alerts,
        {"entry_allowed": False},
    )

    assert [item["action"] for item in filtered] == [
        "blocked_chasing",
        "position_limit_reached",
        "risk_budget_too_small",
        "stop_loss",
    ]


@pytest.mark.asyncio
async def test_main_scan_passes_runtime_fallback_gate_before_candidate_evaluation(
    tmp_path,
    monkeypatch,
):
    import app.main as main_module

    gate_path = tmp_path / "missing-gate.json"
    monkeypatch.setenv("CONGXI_VISIBLE_DECISION_GATE_PATH", str(gate_path))
    captured = {}

    async def fake_evaluate(store, quote_source, **kwargs):
        captured["entry_gate"] = kwargs.get("entry_gate")
        return {
            "scanned": 1,
            "alerts": [
                {"stock_code": "000001", "action": "actionable"},
                {"stock_code": "000002", "action": "entry_cancelled"},
            ],
        }

    monkeypatch.setattr(main_module, "evaluate_candidate_pool", fake_evaluate)
    monkeypatch.setattr(
        main_module.notification_gate,
        "filter_alerts",
        lambda alerts, stage: alerts,
    )
    monkeypatch.setattr(main_module, "_feishu_webhook_push", lambda *args: None)

    result = await main_module._scan_candidate_pool_and_push("test", 3000, 3000)

    assert captured["entry_gate"]["entry_allowed"] is False
    assert captured["entry_gate"]["reasons"] == ["visible_decision_gate_missing"]
    assert [item["action"] for item in result["alerts"]] == ["entry_cancelled"]


@pytest.mark.asyncio
async def test_main_scan_uses_portfolio_sync_failure_override_for_evaluation_and_output(
    monkeypatch,
):
    import app.main as main_module
    from app.services.visible_decision_gate import build_runtime_blocked_gate

    captured = {}
    sync_failure_gate = build_runtime_blocked_gate(
        "portfolio_sync_failed", today=date(2026, 7, 22)
    )

    async def fake_evaluate(store, quote_source, **kwargs):
        captured["entry_gate"] = kwargs.get("entry_gate")
        return {
            "scanned": 2,
            "alerts": [
                {"stock_code": "000001", "action": "open_position"},
                {"stock_code": "000002", "action": "stop_loss"},
            ],
        }

    monkeypatch.setattr(main_module, "evaluate_candidate_pool", fake_evaluate)
    monkeypatch.setattr(
        main_module.notification_gate, "filter_alerts", lambda alerts, stage: alerts
    )
    monkeypatch.setattr(main_module, "_feishu_webhook_push", lambda *args: None)

    result = await main_module._scan_candidate_pool_and_push(
        "test",
        3000,
        3000,
        entry_gate=sync_failure_gate,
    )

    assert captured["entry_gate"] == sync_failure_gate
    assert [item["action"] for item in result["alerts"]] == ["stop_loss"]


@pytest.mark.asyncio
async def test_intraday_sync_failure_blocks_candidates_but_still_runs_position_watch(
    monkeypatch,
):
    import app.main as main_module

    class FakeQuery:
        def filter(self, *args, **kwargs):
            return self

        def all(self):
            return []

        def first(self):
            return None

    class FakeDb:
        def query(self, model):
            return FakeQuery()

        def commit(self):
            return None

        def close(self):
            return None

    watch_calls = []
    scan_calls = []
    pushed = []

    def fail_sync(db):
        raise RuntimeError("sync unavailable")

    def fake_watch(store, quotes):
        watch_calls.append((store, quotes))
        return [{"stock_code": "000001", "action": "stop_loss"}]

    async def fake_scan(stage, cash, total_assets, positions=None, entry_gate=None):
        scan_calls.append(entry_gate)
        return {"scanned": 0, "alerts": []}

    monkeypatch.setattr(main_module, "is_trading_day", lambda: True)
    monkeypatch.setattr(main_module, "_in_intraday_alert_window", lambda: True)
    monkeypatch.setattr(main_module, "SessionLocal", FakeDb)
    monkeypatch.setattr(main_module, "sync_db_from_user_portfolio", fail_sync)
    monkeypatch.setattr(main_module, "PositionWatchStore", lambda: object())
    monkeypatch.setattr(main_module, "evaluate_position_watch", fake_watch)
    monkeypatch.setattr(
        main_module.notification_gate, "filter_alerts", lambda alerts, stage: alerts
    )
    monkeypatch.setattr(
        main_module, "_feishu_webhook_push", lambda *args: pushed.append(args)
    )
    monkeypatch.setattr(main_module, "_scan_candidate_pool_and_push", fake_scan)
    main_module.generation_status["event_scan"]["running"] = False

    await main_module._run_intraday_alert_scan_with_status()

    assert len(watch_calls) == 1
    assert pushed and pushed[0][0] == "恭喜发财 v9.0.0-dev 盘中持仓触发"
    assert len(scan_calls) == 1
    assert scan_calls[0]["entry_allowed"] is False
    assert scan_calls[0]["reasons"] == ["portfolio_sync_failed"]


def test_holdings_data_exposes_portfolio_sync_failure(monkeypatch):
    import app.main as main_module

    class FakeQuery:
        def filter(self, *args, **kwargs):
            return self

        def all(self):
            return []

        def first(self):
            return None

    class FakeDb:
        def query(self, model):
            return FakeQuery()

    monkeypatch.setattr(
        main_module,
        "sync_db_from_user_portfolio",
        lambda db: (_ for _ in ()).throw(RuntimeError("sync unavailable")),
    )

    holdings = main_module._get_holdings_data(FakeDb())

    assert holdings["portfolio_sync_failed"] is True
