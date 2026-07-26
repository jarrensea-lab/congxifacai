import pytest
import json
import math

from app.services.recommendation_review import (
    build_recommendation_review,
    render_recommendation_review_markdown,
)


class _NoMarketData:
    async def fetch_batch(self, codes):
        return {}

    async def fetch_kline(self, code, period, count):
        return {"bars": []}


@pytest.mark.asyncio
async def test_recommendation_review_handles_missing_portfolio(tmp_path):
    review = await build_recommendation_review(portfolio_path=str(tmp_path / "missing.json"))

    assert review["executed"]["count"] == 0
    assert review["items"] == []
    assert review["system_gap"] == "portfolio_missing"

    markdown = render_recommendation_review_markdown(review)
    assert "未找到本地持仓文件" in markdown


@pytest.mark.asyncio
async def test_recommendation_review_names_no_executed_samples_truthfully(tmp_path):
    portfolio_path = tmp_path / "portfolio.json"
    portfolio_path.write_text(
        json.dumps(
            {
                "positions": [],
                "closed_positions": [],
                "trade_events": [],
                "available_cash": 2000.0,
            }
        ),
        encoding="utf-8",
    )

    review = await build_recommendation_review(
        portfolio_path=str(portfolio_path),
        quote_source=_NoMarketData(),
    )

    assert review["executed"]["count"] == 0
    assert review["system_gap"] == "no_executed_samples"
    assert review["strategy_attribution"]["closed_count"] == 0


@pytest.mark.asyncio
async def test_recommendation_review_names_missing_chain_when_execution_exists(tmp_path):
    portfolio_path = tmp_path / "portfolio.json"
    portfolio_path.write_text(
        json.dumps(
            {
                "positions": [
                    {
                        "code": "002131",
                        "name": "利欧股份",
                        "shares": 100,
                        "avg_cost": 4.0,
                        "current_price": 4.1,
                        "trade_history": [
                            {
                                "date": "2026-07-20",
                                "type": "buy",
                                "fill_id": "fill-unlinked",
                            }
                        ],
                    }
                ],
                "closed_positions": [],
                "trade_events": [],
                "available_cash": 1000.0,
            }
        ),
        encoding="utf-8",
    )

    review = await build_recommendation_review(
        portfolio_path=str(portfolio_path),
        quote_source=_NoMarketData(),
    )

    assert review["executed"]["count"] == 1
    assert review["executed"]["attributed_count"] == 0
    assert review["system_gap"] == "execution_chain_missing"
    assert review["executed"]["metric_scope"] == (
        "portfolio_behavior_not_strategy_attribution"
    )


@pytest.mark.asyncio
async def test_recommendation_review_detects_fill_without_position_as_execution(
    tmp_path,
):
    portfolio_path = tmp_path / "portfolio.json"
    portfolio_path.write_text(
        json.dumps(
            {
                "positions": [],
                "closed_positions": [],
                "trade_events": [
                    {
                        "fill_id": "fill-without-position",
                        "code": "002131",
                        "side": "buy",
                    }
                ],
                "available_cash": 1000.0,
            }
        ),
        encoding="utf-8",
    )

    review = await build_recommendation_review(
        portfolio_path=str(portfolio_path),
        quote_source=_NoMarketData(),
    )

    assert review["executed"]["count"] == 0
    assert review["executed"]["execution_evidence_count"] == 1
    assert review["system_gap"] == "execution_chain_missing"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failed_event_type", "expected_diagnostics"),
    [
        ("fill", {"fill_sync_failed": 1}),
        ("outcome", {"outcome_sync_failed": 1}),
    ],
)
async def test_recommendation_review_sanitizes_sync_failure_diagnostics(
    tmp_path,
    monkeypatch,
    failed_event_type,
    expected_diagnostics,
):
    from app.services.execution_ledger import ExecutionLedger

    portfolio_path = tmp_path / "portfolio.json"
    portfolio_path.write_text(
        json.dumps(
            {
                "positions": [
                    {
                        "code": "002131",
                        "shares": 100,
                        "avg_cost": 4.0,
                        "current_price": 4.1,
                        "trade_history": [
                            {
                                "date": "2026-07-20",
                                "type": "buy",
                                "fill_id": "private-fill-id",
                                "signal_id": "signal-1",
                                "recommendation_id": "rec-1",
                            }
                        ],
                    }
                ],
                "closed_positions": [],
                "trade_events": [
                    {
                        "fill_id": "private-fill-id",
                        "code": "002131",
                        "side": "buy",
                        "signal_id": "signal-1",
                        "recommendation_id": "rec-1",
                    }
                ],
                "available_cash": 1000.0,
            }
        ),
        encoding="utf-8",
    )
    original_append = ExecutionLedger.append_event

    def append_with_failure(self, event):
        if event.get("event_type") == failed_event_type:
            return {"ok": False, "written": False, "error": "private-detail"}
        return original_append(self, event)

    monkeypatch.setattr(ExecutionLedger, "append_event", append_with_failure)

    review = await build_recommendation_review(
        portfolio_path=str(portfolio_path),
        execution_ledger_path=str(tmp_path / "execution.jsonl"),
        quote_source=_NoMarketData(),
    )

    assert review["sync_diagnostics"] == expected_diagnostics
    assert "private-fill-id" not in json.dumps(
        review["sync_diagnostics"],
        ensure_ascii=False,
    )
    assert "private-detail" not in json.dumps(
        review["sync_diagnostics"],
        ensure_ascii=False,
    )


@pytest.mark.asyncio
async def test_recommendation_review_fails_closed_on_invalid_execution_history(tmp_path):
    portfolio_path = tmp_path / "portfolio.json"
    portfolio_path.write_text(
        json.dumps(
            {
                "positions": [
                    {
                        "code": "002131",
                        "name": "利欧股份",
                        "shares": 100,
                        "avg_cost": 4.0,
                        "current_price": 4.1,
                        "trade_history": [
                            {
                                "date": "2026-07-20",
                                "type": "buy",
                                "fill_id": "fill-1",
                                "signal_id": "signal-1",
                                "recommendation_id": "rec-1",
                            }
                        ],
                    }
                ],
                "closed_positions": [],
                "trade_events": [
                    {
                        "fill_id": "fill-1",
                        "code": "002131",
                        "signal_id": "signal-1",
                        "recommendation_id": "rec-1",
                    }
                ],
                "available_cash": 1000.0,
            }
        ),
        encoding="utf-8",
    )
    ledger_path = tmp_path / "execution.jsonl"
    ledger_path.write_text("{broken-json\n", encoding="utf-8")

    review = await build_recommendation_review(
        portfolio_path=str(portfolio_path),
        execution_ledger_path=str(ledger_path),
        quote_source=_NoMarketData(),
    )

    assert review["executed"]["count"] == 1
    assert review["system_gap"] == "execution_ledger_history_invalid"
    assert review["strategy_attribution"]["closed_count"] == 0
    assert review["diagnostics"] == ["execution_ledger_history_invalid"]
    assert "broken-json" not in json.dumps(review, ensure_ascii=False)


@pytest.mark.asyncio
async def test_recommendation_review_separates_portfolio_behavior_from_strategy_attribution(tmp_path):
    from app.services.execution_ledger import ExecutionLedger

    portfolio_path = tmp_path / "portfolio.json"
    portfolio_path.write_text(json.dumps({
        "positions": [{
            "code": "002131",
            "name": "利欧股份",
            "shares": 200,
            "avg_cost": 3.995,
            "current_price": 3.9,
            "trade_history": [{
                "date": "2026-07-20",
                "fill_id": "fill-lio-unlinked",
                "signal_id": None,
                "recommendation_id": None,
            }],
        }],
        "closed_positions": [{
            "code": "000100",
            "name": "TCL科技",
            "shares": 100,
            "avg_cost": 4.0,
            "close_price": 4.2,
            "close_date": "2026-07-20",
            "realized_pnl": 20.0,
            "realized_pnl_pct": 5.0,
            "trade_history": [{
                "date": "2026-07-20",
                "fill_id": "fill-linked",
                "signal_id": "signal-linked",
                "recommendation_id": "rec-linked",
            }],
        }],
        "trade_events": [
            {
                "fill_id": "fill-lio-unlinked",
                "code": "002131",
                "signal_id": None,
                "recommendation_id": None,
            },
            {
                "fill_id": "fill-linked",
                "code": "000100",
                "signal_id": "signal-linked",
                "recommendation_id": "rec-linked",
            },
        ],
        "available_cash": 1000.0,
        "realized_pnl": 20.0,
    }), encoding="utf-8")
    ledger = ExecutionLedger(tmp_path / "execution.jsonl")
    base = {
        "occurred_at": "2026-07-20T10:00:00+08:00",
        "code": "000100",
        "signal_id": "signal-linked",
        "recommendation_id": "rec-linked",
        "fill_id": "fill-linked",
        "source": "test",
        "payload": {},
    }
    for index, (event_id, event_type) in enumerate([
        ("e-signal", "signal"),
        ("e-auth", "authorization"),
        ("e-rec", "recommendation"),
        ("e-fill", "fill"),
        ("e-outcome", "outcome"),
    ]):
        event = {
            **base,
            "event_id": event_id,
            "event_type": event_type,
            "occurred_at": f"2026-07-20T10:0{index}:00+08:00",
        }
        if event_type == "signal":
            event.update({"recommendation_id": None, "fill_id": None})
        elif event_type == "authorization":
            event.update({
                "recommendation_id": None,
                "fill_id": None,
                "payload": {"approved": True},
            })
        elif event_type == "recommendation":
            event["fill_id"] = None
        elif event_type == "outcome":
            event["payload"] = {"state": "closed", "pnl": 20.0, "return_pct": 5.0}
        assert ledger.append_event(event)["ok"] is True

    class QuoteSource:
        async def fetch_batch(self, codes):
            return {}

        async def fetch_kline(self, code, period, count):
            return {"bars": []}

    review = await build_recommendation_review(
        portfolio_path=str(portfolio_path),
        execution_ledger_path=str(ledger.path),
        quote_source=QuoteSource(),
    )

    assert review["executed"]["count"] == 2
    assert review["executed"]["attributed_count"] == 1
    assert review["executed"]["unattributed_count"] == 1
    assert review["executed"]["metric_scope"] == "portfolio_behavior_not_strategy_attribution"
    by_code = {item["code"]: item for item in review["items"]}
    assert by_code["002131"]["attribution"]["status"] == "unattributed"
    assert by_code["000100"]["attribution"]["status"] == "attributed"
    assert review["strategy_attribution"]["closed_count"] == 1
    assert review["strategy_attribution"]["total_pnl"] == 20.0
    assert review["system_gap"] == ""


@pytest.mark.asyncio
async def test_recommendation_review_syncs_portfolio_fill_to_default_execution_ledger(tmp_path):
    from app.services.execution_ledger import ExecutionLedger

    portfolio_path = tmp_path / "portfolio.json"
    portfolio_path.write_text(
        json.dumps(
            {
                "positions": [
                    {
                        "code": "002123",
                        "name": "低价突破",
                        "shares": 100,
                        "avg_cost": 3.2,
                        "current_price": 3.3,
                        "trade_history": [
                            {
                                "date": "2026-07-20",
                                "type": "buy",
                                "fill_id": "fill-linked",
                                "signal_id": "signal-linked",
                                "recommendation_id": "rec-linked",
                            }
                        ],
                    }
                ],
                "closed_positions": [],
                "trade_events": [
                    {
                        "fill_id": "fill-linked",
                        "code": "002123",
                        "side": "buy",
                        "signal_id": "signal-linked",
                        "recommendation_id": "rec-linked",
                        "occurred_at": "2026-07-20T10:03:00+08:00",
                    }
                ],
                "available_cash": 1000.0,
            }
        ),
        encoding="utf-8",
    )
    ledger = ExecutionLedger(tmp_path / "execution_ledger.jsonl")
    for event in [
        {
            "event_id": "signal-event",
            "event_type": "signal",
            "occurred_at": "2026-07-20T10:00:00+08:00",
            "code": "002123",
            "signal_id": "signal-linked",
            "source": "test",
            "payload": {},
        },
        {
            "event_id": "authorization-event",
            "event_type": "authorization",
            "occurred_at": "2026-07-20T10:01:00+08:00",
            "code": "002123",
            "signal_id": "signal-linked",
            "source": "test",
            "payload": {"approved": True},
        },
        {
            "event_id": "recommendation-event",
            "event_type": "recommendation",
            "occurred_at": "2026-07-20T10:02:00+08:00",
            "code": "002123",
            "signal_id": "signal-linked",
            "recommendation_id": "rec-linked",
            "source": "test",
            "payload": {},
        },
    ]:
        assert ledger.append_event(event)["ok"] is True

    class QuoteSource:
        async def fetch_batch(self, codes):
            return {}

        async def fetch_kline(self, code, period, count):
            return {"bars": []}

    await build_recommendation_review(
        portfolio_path=str(portfolio_path),
        quote_source=QuoteSource(),
    )

    event_types = [
        event["event_type"] for event in ledger.read_with_diagnostics()["events"]
    ]
    assert event_types == ["signal", "authorization", "recommendation", "fill", "outcome"]


@pytest.mark.asyncio
async def test_recommendation_review_reports_invalid_portfolio_structures_without_crashing(tmp_path):
    invalid_portfolios = [
        ([], "portfolio_not_object"),
        ({"positions": {}}, "positions_not_list"),
        ({"positions": [None]}, "positions_entry_not_object"),
        ({"positions": [], "closed_positions": {}}, "closed_positions_not_list"),
        ({"positions": [], "trade_events": {}}, "trade_events_not_list"),
        ({"positions": [{"code": "002131", "trade_history": {}}]}, "trade_history_not_list"),
        ({"positions": [{"code": "002131", "trade_history": [None]}]}, "trade_history_entry_not_object"),
    ]

    for index, (portfolio, expected_reason) in enumerate(invalid_portfolios):
        path = tmp_path / f"invalid-{index}.json"
        path.write_text(json.dumps(portfolio), encoding="utf-8")

        review = await build_recommendation_review(portfolio_path=str(path))

        assert review["system_gap"] == "portfolio_invalid"
        assert expected_reason in review["diagnostics"]
        assert review["executed"]["count"] == 0
        assert "持仓文件结构无效" in render_recommendation_review_markdown(review)


@pytest.mark.asyncio
async def test_recommendation_review_rejects_nonfinite_and_invalid_position_numbers(tmp_path):
    invalid_portfolios = [
        (
            {"positions": [{"code": "002131", "shares": 200, "avg_cost": float("nan")}]},
            "positions[0].avg_cost_not_finite",
        ),
        (
            {"positions": [{"code": "002131", "shares": 1.5, "avg_cost": 4.0}]},
            "positions[0].shares_not_positive_integer",
        ),
        (
            {"closed_positions": [{"code": "000100", "shares": -1, "realized_pnl": 1.0}]},
            "closed_positions[0].shares_not_nonnegative_integer",
        ),
        (
            {"positions": [], "available_cash": float("inf")},
            "portfolio.available_cash_not_finite",
        ),
        (
            {"positions": [], "realized_pnl": "not-a-number"},
            "portfolio.realized_pnl_not_finite",
        ),
    ]

    for index, (portfolio, expected_reason) in enumerate(invalid_portfolios):
        path = tmp_path / f"invalid-number-{index}.json"
        path.write_text(json.dumps(portfolio), encoding="utf-8")

        review = await build_recommendation_review(portfolio_path=str(path))

        assert review["system_gap"] == "portfolio_invalid"
        assert expected_reason in review["diagnostics"]
        assert all(
            math.isfinite(value)
            for section in (review["executed"], review["portfolio"], review["strategy_attribution"])
            for value in section.values()
            if isinstance(value, (int, float)) and not isinstance(value, bool)
        )


@pytest.mark.asyncio
async def test_recommendation_review_rejects_malformed_trade_events(tmp_path):
    invalid_events = [
        ({"code": "002131"}, "trade_events[0].fill_id_invalid"),
        ({"fill_id": "fill-1", "code": 2131}, "trade_events[0].code_invalid"),
        ({"fill_id": "fill-1", "code": "002131", "side": 1}, "trade_events[0].side_invalid"),
        (
            {"fill_id": "fill-1", "code": "002131", "signal_id": []},
            "trade_events[0].signal_id_invalid",
        ),
        (
            {"fill_id": "fill-1", "code": "002131", "recommendation_id": ""},
            "trade_events[0].recommendation_id_invalid",
        ),
    ]

    for index, (trade_event, expected_reason) in enumerate(invalid_events):
        path = tmp_path / f"invalid-event-{index}.json"
        path.write_text(
            json.dumps({"positions": [], "trade_events": [trade_event]}),
            encoding="utf-8",
        )

        review = await build_recommendation_review(portfolio_path=str(path))

        assert review["system_gap"] == "portfolio_invalid"
        assert expected_reason in review["diagnostics"]
