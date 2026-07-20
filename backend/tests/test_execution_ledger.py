import json
from concurrent.futures import ThreadPoolExecutor


def _event(event_id, event_type, **overrides):
    event = {
        "event_id": event_id,
        "event_type": event_type,
        "occurred_at": "2026-07-20T10:00:00+08:00",
        "code": "002131",
        "signal_id": "signal-1",
        "recommendation_id": "rec-1",
        "fill_id": "fill-1",
        "source": "test",
        "payload": {},
    }
    event.update(overrides)
    return event


def _complete_chain_events():
    return [
        _event(
            "evt-signal",
            "signal",
            occurred_at="2026-07-20T10:00:00+08:00",
            recommendation_id=None,
            fill_id=None,
        ),
        _event(
            "evt-auth",
            "authorization",
            occurred_at="2026-07-20T10:01:00+08:00",
            recommendation_id=None,
            fill_id=None,
            payload={"approved": True},
        ),
        _event(
            "evt-rec",
            "recommendation",
            occurred_at="2026-07-20T10:02:00+08:00",
            fill_id=None,
        ),
        _event("evt-fill", "fill", occurred_at="2026-07-20T10:03:00+08:00"),
        _event(
            "evt-outcome",
            "outcome",
            occurred_at="2026-07-20T10:04:00+08:00",
            payload={"state": "closed", "pnl": 12.5, "return_pct": 1.5},
        ),
    ]


def test_execution_ledger_appends_full_chain_and_is_idempotent(tmp_path):
    from app.services.execution_ledger import ExecutionLedger

    ledger = ExecutionLedger(tmp_path / "execution.jsonl")
    for event in _complete_chain_events():
        result = ledger.append_event(event)
        assert result["ok"] is True
        assert result["written"] is True

    duplicate = ledger.append_event(_complete_chain_events()[0])
    conflict = ledger.append_event(
        _event("evt-signal", "signal", code="000100", recommendation_id=None, fill_id=None)
    )

    assert duplicate["ok"] is True
    assert duplicate["duplicate"] is True
    assert duplicate["written"] is False
    assert conflict["ok"] is False
    assert conflict["conflict"] is True
    assert len(ledger.read_with_diagnostics()["events"]) == 5


def test_execution_ledger_dirty_history_fails_closed_without_append(tmp_path):
    from app.services.execution_ledger import ExecutionLedger

    path = tmp_path / "execution.jsonl"
    original = '{"event_id":"incomplete"}\n{not-json}\n'
    path.write_text(original, encoding="utf-8")
    ledger = ExecutionLedger(path)

    result = ledger.append_event(_event("evt-new", "signal"))

    assert result["ok"] is False
    assert result["error"] == "execution_ledger_history_invalid"
    assert path.read_text(encoding="utf-8") == original


def test_execution_ledger_rejects_invalid_type_and_nan_before_writing(tmp_path):
    from app.services.execution_ledger import ExecutionLedger

    ledger = ExecutionLedger(tmp_path / "execution.jsonl")

    invalid_type = ledger.append_event(_event("evt-invalid-type", "unknown"))
    invalid_nan = ledger.append_event(
        _event("evt-invalid-nan", "outcome", payload={"pnl": float("nan")})
    )

    assert invalid_type["ok"] is False
    assert invalid_type["error"] == "event_type_invalid"
    assert invalid_nan["ok"] is False
    assert invalid_nan["error"] == "event_not_json_serializable"
    assert not ledger.path.exists()


def test_execution_ledger_requires_type_specific_ids_and_aware_iso_timestamp(tmp_path):
    from app.services.execution_ledger import ExecutionLedger

    ledger = ExecutionLedger(tmp_path / "execution.jsonl")
    probes = [
        (_event("missing-signal", "signal", signal_id=None), "signal_id_required"),
        (_event("missing-auth-signal", "authorization", signal_id=""), "signal_id_required"),
        (_event("missing-rec-id", "recommendation", recommendation_id=None), "recommendation_id_required"),
        (_event("missing-fill-id", "fill", fill_id=None), "fill_id_required"),
        (_event("missing-outcome-rec", "outcome", recommendation_id=None), "recommendation_id_required"),
        (_event("bad-time", "fill", occurred_at="not-a-timestamp"), "occurred_at_invalid"),
        (_event("naive-time", "fill", occurred_at="2026-07-20T10:00:00"), "occurred_at_timezone_required"),
    ]

    for event, expected_error in probes:
        result = ledger.append_event(event)
        assert result["ok"] is False
        assert result["error"] == expected_error
    assert not ledger.path.exists()


def test_execution_ledger_concurrent_duplicate_writes_one_jsonl_row(tmp_path):
    from app.services.execution_ledger import ExecutionLedger

    ledger = ExecutionLedger(tmp_path / "execution.jsonl")
    event = _event("evt-concurrent", "fill")

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: ledger.append_event(event), range(2)))

    assert sorted(result["written"] for result in results) == [False, True]
    lines = ledger.path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["event_id"] == "evt-concurrent"


def test_execution_attribution_joins_complete_chain_and_excludes_unlinked_fill(tmp_path):
    from app.services.execution_ledger import ExecutionLedger

    ledger = ExecutionLedger(tmp_path / "execution.jsonl")
    for event in _complete_chain_events():
        assert ledger.append_event(event)["ok"] is True

    attribution = ledger.join_attribution(
        portfolio_trade_events=[
            {
                "fill_id": "fill-1",
                "code": "002131",
                "signal_id": "signal-1",
                "recommendation_id": "rec-1",
            },
            {
                "fill_id": "fill-lio-unlinked",
                "code": "002131",
                "signal_id": None,
                "recommendation_id": None,
            },
        ]
    )

    assert attribution["ok"] is True
    assert attribution["attributed_count"] == 1
    assert attribution["unattributed_count"] == 1
    by_fill = {item["fill_id"]: item for item in attribution["chains"]}
    assert by_fill["fill-1"]["status"] == "attributed"
    assert [event["event_type"] for event in by_fill["fill-1"]["events"]] == [
        "signal",
        "authorization",
        "recommendation",
        "fill",
        "outcome",
    ]
    assert by_fill["fill-lio-unlinked"]["status"] == "unattributed"
    assert "signal_id_missing" in by_fill["fill-lio-unlinked"]["reasons"]
    assert attribution["strategy_metrics"]["closed_count"] == 1
    assert attribution["strategy_metrics"]["total_pnl"] == 12.5


def test_execution_attribution_rejects_denied_authorization_without_changing_metrics():
    from app.services.execution_ledger import build_execution_attribution

    events = _complete_chain_events()
    events[1]["payload"] = {"status": "denied"}

    attribution = build_execution_attribution(events)

    assert attribution["chains"][0]["status"] == "unattributed"
    assert "authorization_not_approved" in attribution["chains"][0]["reasons"]
    assert attribution["strategy_metrics"]["closed_count"] == 0
    assert attribution["strategy_metrics"]["total_pnl"] == 0


def test_execution_attribution_rejects_code_id_time_and_portfolio_mismatches():
    from app.services.execution_ledger import build_execution_attribution

    probes = []

    code_mismatch = _complete_chain_events()
    code_mismatch[4]["code"] = "000100"
    probes.append((code_mismatch, None, "event_code_mismatch"))

    id_mismatch = _complete_chain_events()
    id_mismatch[4]["signal_id"] = "signal-other"
    probes.append((id_mismatch, None, "outcome_signal_mismatch"))

    time_reversed = _complete_chain_events()
    time_reversed[3]["occurred_at"] = "2026-07-20T09:59:00+08:00"
    probes.append((time_reversed, None, "lifecycle_time_not_monotonic"))

    portfolio_mismatch = [{
        "fill_id": "fill-1",
        "code": "000100",
        "signal_id": "signal-1",
        "recommendation_id": "rec-1",
    }]
    probes.append((_complete_chain_events(), portfolio_mismatch, "portfolio_fill_code_mismatch"))

    for events, portfolio_events, reason in probes:
        attribution = build_execution_attribution(
            events,
            portfolio_trade_events=portfolio_events,
        )
        chain = attribution["chains"][0]
        assert chain["status"] == "unattributed"
        assert reason in chain["reasons"]
        assert attribution["strategy_metrics"]["closed_count"] == 0
        assert attribution["strategy_metrics"]["total_pnl"] == 0


def test_closed_outcome_requires_finite_numeric_pnl_and_return(tmp_path):
    from app.services.execution_ledger import ExecutionLedger

    ledger = ExecutionLedger(tmp_path / "execution.jsonl")
    invalid_values = [True, float("nan"), float("inf"), "not-a-number"]
    for index, invalid in enumerate(invalid_values):
        event = _complete_chain_events()[-1]
        event["event_id"] = f"invalid-outcome-{index}"
        event["payload"] = {"state": "closed", "pnl": invalid, "return_pct": 1.0}
        result = ledger.append_event(event)
        assert result["ok"] is False
        assert result["error"] == "outcome_pnl_invalid"

    missing_return = _complete_chain_events()[-1]
    missing_return["event_id"] = "missing-return"
    missing_return["payload"] = {"state": "closed", "pnl": 1.0}
    result = ledger.append_event(missing_return)
    assert result["ok"] is False
    assert result["error"] == "outcome_return_pct_invalid"
    assert not ledger.path.exists()


def test_pending_outcome_is_audited_but_not_counted_until_one_terminal_outcome_exists(tmp_path):
    from app.services.execution_ledger import ExecutionLedger

    ledger = ExecutionLedger(tmp_path / "execution.jsonl")
    events = _complete_chain_events()
    pending = {
        **events[-1],
        "event_id": "evt-outcome-pending",
        "occurred_at": "2026-07-20T10:03:30+08:00",
        "payload": {"status": "pending"},
    }
    for event in [*events[:-1], pending]:
        assert ledger.append_event(event)["ok"] is True

    pending_attribution = ledger.join_attribution()
    assert pending_attribution["chains"][0]["status"] == "pending"
    assert pending_attribution["strategy_metrics"]["closed_count"] == 0

    assert ledger.append_event(events[-1])["ok"] is True
    closed_attribution = ledger.join_attribution()
    assert closed_attribution["chains"][0]["status"] == "attributed"
    assert closed_attribution["strategy_metrics"]["closed_count"] == 1


def test_multiple_terminal_outcomes_are_ambiguous_and_excluded():
    from app.services.execution_ledger import build_execution_attribution

    events = _complete_chain_events()
    events.append(
        {
            **events[-1],
            "event_id": "evt-outcome-second-terminal",
            "occurred_at": "2026-07-20T10:05:00+08:00",
            "payload": {"status": "terminal", "pnl": 13.0, "return_pct": 1.6},
        }
    )

    attribution = build_execution_attribution(events)

    assert attribution["chains"][0]["status"] == "unattributed"
    assert "outcome_event_ambiguous" in attribution["chains"][0]["reasons"]
    assert attribution["strategy_metrics"]["closed_count"] == 0


def test_portfolio_event_collection_is_authoritative_even_when_empty():
    from app.services.execution_ledger import build_execution_attribution

    pure_ledger = build_execution_attribution(_complete_chain_events())
    portfolio_authoritative = build_execution_attribution(
        _complete_chain_events(),
        portfolio_trade_events=[],
    )

    assert pure_ledger["strategy_metrics"]["closed_count"] == 1
    assert portfolio_authoritative["chains"][0]["status"] == "unattributed"
    assert "portfolio_fill_missing" in portfolio_authoritative["chains"][0]["reasons"]
    assert portfolio_authoritative["strategy_metrics"]["closed_count"] == 0


def test_portfolio_duplicate_fill_collapses_only_when_payload_matches():
    from app.services.execution_ledger import build_execution_attribution

    portfolio_fill = {
        "fill_id": "fill-1",
        "code": "002131",
        "signal_id": "signal-1",
        "recommendation_id": "rec-1",
        "side": "buy",
        "shares": 100,
        "price": 4.0,
        "payload": {"broker": "manual"},
    }
    identical = build_execution_attribution(
        _complete_chain_events(),
        portfolio_trade_events=[portfolio_fill, dict(portfolio_fill)],
    )
    conflicting = build_execution_attribution(
        _complete_chain_events(),
        portfolio_trade_events=[portfolio_fill, {**portfolio_fill, "code": "000100"}],
    )

    assert len(identical["chains"]) == 1
    assert identical["chains"][0]["status"] == "attributed"
    assert identical["strategy_metrics"]["closed_count"] == 1
    assert conflicting["chains"][0]["status"] == "unattributed"
    assert "portfolio_fill_conflict" in conflicting["chains"][0]["reasons"]
    assert "portfolio_fill_ambiguous" in conflicting["chains"][0]["reasons"]
    assert conflicting["strategy_metrics"]["closed_count"] == 0
