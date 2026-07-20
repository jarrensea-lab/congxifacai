import argparse
import ast
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace

import pytest

from app.services.prediction_lab import (
    PredictionLedger,
    build_prediction_records,
    evaluate_prediction_record,
    summarize_prediction_outcomes,
)
from scripts import run_prediction_lab


def _records(*, code: str, prediction_date: str, horizons: tuple[int, ...] = (1, 3)):
    bars = [
        {"date": f"2026-06-{day:02d}", "close": 10 + day / 10, "high": 11 + day / 10, "low": 9 + day / 10}
        for day in range(1, 21)
    ]
    return build_prediction_records(
        code=code,
        name=code,
        quote={"price": 12, "change_pct": 1, "vol_ratio": 2, "amount_wan": 30000},
        bars=bars,
        prediction_date=prediction_date,
        horizons=horizons,
    )


def test_due_queue_includes_old_pending_prediction_and_excludes_verified(tmp_path):
    ledger = PredictionLedger(tmp_path)
    pending, verified = _records(code="000001", prediction_date="2026-06-01", horizons=(1, 3))
    ledger.append_predictions([pending, verified])
    ledger.append_outcomes(
        [{"prediction_id": verified["prediction_id"], "status": "verified"}],
        outcome_date="2026-06-20",
    )

    due = ledger.due_predictions(as_of="2026-07-15")

    assert [item["prediction_id"] for item in due] == [pending["prediction_id"]]


def test_append_outcomes_is_idempotent_across_outcome_dates(tmp_path):
    ledger = PredictionLedger(tmp_path)
    record = {"prediction_id": "pred_terminal", "status": "verified", "actual_return_pct": 1.0}

    assert ledger.append_outcomes([record], outcome_date="2026-07-14") == 1
    assert ledger.append_outcomes([record], outcome_date="2026-07-15") == 0
    rows = [
        item
        for path in (tmp_path / "outcomes").glob("*.jsonl")
        for item in ledger.read_jsonl(path)
    ]
    assert rows == [record]


def test_append_predictions_is_idempotent_under_concurrency(tmp_path):
    record = _records(code="000001", prediction_date="2026-07-21", horizons=(1,))[0]
    worker_count = 16
    start = Barrier(worker_count)

    def append_once():
        start.wait()
        return PredictionLedger(tmp_path).append_predictions([record])

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        written = list(executor.map(lambda _: append_once(), range(worker_count)))

    assert sum(written) == 1
    assert PredictionLedger.read_jsonl(tmp_path / "predictions" / "2026-07-21.jsonl") == [record]


def test_append_outcomes_is_idempotent_under_concurrency(tmp_path):
    record = {"prediction_id": "pred_concurrent", "status": "verified"}
    worker_count = 16
    start = Barrier(worker_count)

    def append_once():
        start.wait()
        return PredictionLedger(tmp_path).append_outcomes(
            [record], outcome_date="2026-07-21"
        )

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        written = list(executor.map(lambda _: append_once(), range(worker_count)))

    assert sum(written) == 1
    assert PredictionLedger.read_jsonl(tmp_path / "outcomes" / "2026-07-21.jsonl") == [record]


@pytest.mark.asyncio
async def test_backfill_fetches_each_code_once_and_continues_after_one_code_failure(tmp_path):
    ledger = PredictionLedger(tmp_path)
    ledger.append_predictions(_records(code="000001", prediction_date="2026-06-01"))
    ledger.append_predictions(_records(code="000002", prediction_date="2026-06-01"))

    class FakeQuoteSource:
        def __init__(self):
            self.calls = []

        async def fetch_kline(self, code, period, count):
            self.calls.append(code)
            if code == "000001":
                raise RuntimeError("fixture failure")
            return {
                "bars": [
                    {"date": f"2026-06-{day:02d}", "close": 10 + day / 10}
                    for day in range(1, 10)
                ]
            }

    source = FakeQuoteSource()
    args = argparse.Namespace(
        output_root=str(tmp_path),
        as_of="2026-07-15",
        limit=None,
        kline_count=40,
    )

    result = await run_prediction_lab.backfill_due_predictions(args, quote_source=source)

    assert source.calls == ["000001", "000002"]
    assert result["code_error_count"] == 1
    assert result["verified_count"] == 2
    assert result["evaluated_count"] == 4


@pytest.mark.asyncio
async def test_backfill_requests_enough_history_for_prediction_older_than_forty_days(tmp_path):
    ledger = PredictionLedger(tmp_path)
    prediction = build_prediction_records(
        code="000001",
        name="000001",
        quote={"price": 10, "change_pct": 1, "vol_ratio": 2, "amount_wan": 30000},
        bars=[{"date": "2026-05-01", "close": 10}] * 10,
        prediction_date="2026-05-01",
        horizons=(5,),
    )[0]
    ledger.append_predictions([prediction])

    class FakeQuoteSource:
        def __init__(self):
            self.requested_counts = []

        async def fetch_kline(self, code, period, count):
            self.requested_counts.append(count)
            start = date(2026, 5, 1)
            return {
                "bars": [
                    {"date": str(start + timedelta(days=offset)), "close": 10 + offset / 100}
                    for offset in range((date(2026, 7, 15) - start).days + 1)
                ]
            }

    source = FakeQuoteSource()
    args = argparse.Namespace(
        output_root=str(tmp_path),
        as_of="2026-07-15",
        limit=None,
        kline_count=40,
    )

    result = await run_prediction_lab.backfill_due_predictions(args, quote_source=source)

    assert source.requested_counts == [90]
    assert result["verified_count"] == 1


@pytest.mark.asyncio
async def test_backfill_caps_provider_history_and_emits_named_overflow(tmp_path):
    ledger = PredictionLedger(tmp_path)
    ledger.append_predictions(
        build_prediction_records(
            code="000001",
            name="000001",
            quote={"price": 10, "change_pct": 1, "vol_ratio": 2, "amount_wan": 30000},
            bars=[{"date": "2010-01-01", "close": 10}] * 10,
            prediction_date="2010-01-01",
            horizons=(5,),
        )
    )

    class FailIfCalledSource:
        async def fetch_kline(self, code, period, count):
            raise AssertionError("overflow must not issue a misleading partial fetch")

    args = argparse.Namespace(
        output_root=str(tmp_path),
        as_of="2026-07-15",
        limit=None,
        kline_count=40,
    )

    result = await run_prediction_lab.backfill_due_predictions(args, quote_source=FailIfCalledSource())
    outcomes = ledger.read_jsonl(ledger.outcome_path("2026-07-15"))

    assert result["history_overflow_count"] == 1
    assert result["code_errors"] == [
        {
            "code": "000001",
            "reason": "kline_history_overflow",
            "required_count": 6054,
            "provider_limit": 2000,
        }
    ]
    assert outcomes[0]["reason"] == "kline_history_overflow"


def test_scheduler_runs_one_backfill_subprocess_instead_of_ten_evaluate_processes():
    source = Path("backend/app/main.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_run_prediction_lab_with_status"
    )
    function_source = ast.get_source_segment(source, function) or ""

    assert '"backfill"' in function_source
    assert '"evaluate"' not in function_source
    assert "for offset in range" not in function_source


def test_prediction_records_freeze_dual_benchmark_identity_and_data_cutoff():
    records = build_prediction_records(
        code="000001",
        name="平安银行",
        quote={"price": 12, "change_pct": 1, "vol_ratio": 2, "amount_wan": 30000},
        bars=[{"date": "2026-07-14", "close": 12}] * 10,
        prediction_date="2026-07-15",
        horizons=(1,),
        broad_benchmark_id="000300.SH",
        tradable_universe_snapshot_id="target_pool:sha256:fixture",
        data_cutoff="2026-07-15T15:00:00+08:00",
        tradable_budget_fen=200_000,
        entry_policy="prediction_close",
        exit_policy="horizon_close",
        commission_rate=0.00015,
    )

    assert records[0]["broad_benchmark_id"] == "000300.SH"
    assert records[0]["tradable_universe_snapshot_id"] == "target_pool:sha256:fixture"
    assert records[0]["data_cutoff"] == "2026-07-15T15:00:00+08:00"
    assert records[0]["tradable_budget_fen"] == 200_000
    assert records[0]["entry_policy"] == "prediction_close"
    assert records[0]["exit_policy"] == "horizon_close"
    assert records[0]["commission_rate"] == 0.00015


def test_prediction_records_mark_unconfigured_benchmark_identity_unavailable():
    record = _records(code="000001", prediction_date="2026-06-01", horizons=(1,))[0]

    assert record["broad_benchmark_id"] == "unavailable"
    assert record["tradable_universe_snapshot_id"] == "unavailable"


@pytest.mark.asyncio
async def test_collect_predictions_freezes_reachable_benchmark_and_cost_contracts(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("CONGXI_COMMISSION_RATE", "0.00015")
    monkeypatch.setenv("CONGXI_PREDICTION_BUDGET_YUAN", "2000")
    monkeypatch.setattr(
        run_prediction_lab,
        "_load_universe",
        lambda kind, limit: [{"code": "000001", "name": "平安银行"}],
    )

    class QuoteSource:
        async def fetch_batch(self, codes):
            return {
                "000001": {
                    "price": 12.0,
                    "name": "平安银行",
                    "quote_timestamp": "2026-07-21T15:00:00+08:00",
                }
            }

        async def fetch_kline(self, code, period, count):
            return {
                "bars": [
                    {"date": f"2026-07-{day:02d}", "close": 10 + day / 10}
                    for day in range(1, 21)
                ]
            }

    monkeypatch.setattr(run_prediction_lab, "FastRealtimeMarketDataSource", QuoteSource)
    args = SimpleNamespace(
        date="2026-07-21",
        universe="target_pool",
        limit=1,
        output_root=str(tmp_path),
        kline_count=40,
    )

    result = await run_prediction_lab.collect_predictions(args)

    assert result["written"] == 3
    records = PredictionLedger(tmp_path).read_jsonl(
        tmp_path / "predictions" / "2026-07-21.jsonl"
    )
    assert {record["broad_benchmark_id"] for record in records} == {"sh000001"}
    assert all(record["tradable_universe_snapshot_id"].startswith("target_pool:sha256:") for record in records)
    assert {record["tradable_budget_fen"] for record in records} == {200_000}
    assert {record["entry_policy"] for record in records} == {"prediction_close"}
    assert {record["exit_policy"] for record in records} == {"horizon_close"}
    assert {record["commission_rate"] for record in records} == {0.00015}


@pytest.mark.asyncio
async def test_backfill_populates_dual_benchmark_returns_for_collected_contract(tmp_path):
    prediction = build_prediction_records(
        code="000001",
        name="平安银行",
        quote={"price": 10, "change_pct": 1, "vol_ratio": 2, "amount_wan": 30000},
        bars=[{"date": "2026-07-01", "close": 10}] * 10,
        prediction_date="2026-07-01",
        horizons=(1,),
        broad_benchmark_id="sh000001",
        tradable_universe_snapshot_id="target_pool:sha256:fixture",
        data_cutoff="2026-07-01T15:00:00+08:00",
        tradable_budget_fen=200_000,
        entry_policy="prediction_close",
        exit_policy="horizon_close",
        commission_rate=0.00015,
    )[0]
    PredictionLedger(tmp_path).append_predictions([prediction])

    class QuoteSource:
        async def fetch_kline(self, code, period, count):
            if code == "sh000001":
                return {
                    "bars": [
                        {"date": "2026-07-01", "close": 3000},
                        {"date": "2026-07-02", "close": 3030},
                    ]
                }
            return {
                "bars": [
                    {"date": "2026-07-01", "close": 10},
                    {"date": "2026-07-02", "close": 10.2},
                ]
            }

    args = SimpleNamespace(
        output_root=str(tmp_path),
        as_of="2026-07-15",
        limit=None,
        kline_count=40,
    )

    result = await run_prediction_lab.backfill_due_predictions(
        args, quote_source=QuoteSource()
    )

    assert result["verified_count"] == 1
    outcome = PredictionLedger(tmp_path).read_jsonl(
        tmp_path / "outcomes" / "2026-07-15.jsonl"
    )[0]
    assert outcome["broad_benchmark_return_pct"] == 1.0
    assert outcome["tradable_universe_return_pct"] == 2.0
    assert outcome["benchmark_coverage_complete"] is True
    assert outcome["tradable"] is True
    assert outcome["cost_estimated"] is False


def test_missing_benchmark_stays_unavailable_and_blocks_promotion():
    prediction = _records(code="000001", prediction_date="2026-06-01", horizons=(1,))[0]
    bars = [
        {"date": f"2026-06-{day:02d}", "close": 10 + day / 10}
        for day in range(1, 5)
    ]

    outcome = evaluate_prediction_record(
        prediction,
        bars=bars,
        broad_benchmark_return_pct=0.5,
        tradable_universe_return_pct=None,
        as_of="2026-07-15",
    )

    assert outcome["broad_benchmark_return_pct"] == 0.5
    assert outcome["tradable_universe_return_pct"] is None
    assert outcome["tradable_universe_excess_return_pct"] is None
    assert outcome["benchmark_coverage_complete"] is False
    assert outcome["promotion_eligible"] is False
    assert summarize_prediction_outcomes([outcome])["promotion_eligible"] is False


def test_unavailable_outcome_keeps_fail_closed_benchmark_and_cost_contract():
    prediction = _records(code="000001", prediction_date="2026-06-01", horizons=(1,))[0]

    outcome = evaluate_prediction_record(prediction, bars=[], as_of="2026-07-15")

    assert outcome["status"] == "unavailable"
    assert outcome["broad_benchmark_return_pct"] is None
    assert outcome["tradable_universe_return_pct"] is None
    assert outcome["benchmark_coverage_complete"] is False
    assert outcome["net_tradable_return_pct"] is None
    assert outcome["tradable"] is False
    assert outcome["promotion_eligible"] is False


def test_dual_benchmark_excess_returns_are_computed_independently():
    prediction = _records(code="000001", prediction_date="2026-06-01", horizons=(1,))[0]
    prediction.update(
        {
            "broad_benchmark_id": "000300.SH",
            "tradable_universe_snapshot_id": "target_pool:sha256:fixture",
            "data_cutoff": "2026-06-01T15:00:00+08:00",
        }
    )
    bars = [
        {"date": f"2026-06-{day:02d}", "close": 10 + day / 10}
        for day in range(1, 5)
    ]

    outcome = evaluate_prediction_record(
        prediction,
        bars=bars,
        broad_benchmark_return_pct=0.25,
        tradable_universe_return_pct=-0.5,
        as_of="2026-07-15",
    )

    assert outcome["actual_return_pct"] == pytest.approx(0.99, abs=0.01)
    assert outcome["broad_benchmark_excess_return_pct"] == pytest.approx(0.74, abs=0.01)
    assert outcome["tradable_universe_excess_return_pct"] == pytest.approx(1.49, abs=0.01)
    assert outcome["benchmark_coverage_complete"] is True


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("broad_benchmark_id", "unavailable"),
        ("broad_benchmark_id", ""),
        ("tradable_universe_snapshot_id", None),
        ("data_cutoff", "unavailable"),
    ],
)
def test_benchmark_returns_do_not_count_as_covered_without_frozen_identity(field, invalid_value):
    prediction = _records(code="000001", prediction_date="2026-06-01", horizons=(1,))[0]
    prediction.update(
        {
            "broad_benchmark_id": "000300.SH",
            "tradable_universe_snapshot_id": "target_pool:sha256:fixture",
            "data_cutoff": "2026-06-01T15:00:00+08:00",
            field: invalid_value,
        }
    )
    bars = [
        {"date": f"2026-06-{day:02d}", "close": 10 + day / 10}
        for day in range(1, 5)
    ]

    outcome = evaluate_prediction_record(
        prediction,
        bars=bars,
        broad_benchmark_return_pct=0.25,
        tradable_universe_return_pct=-0.5,
        as_of="2026-07-15",
    )

    assert outcome["benchmark_coverage_complete"] is False


def test_outcome_fails_closed_for_cost_return_without_explicit_execution_policy():
    prediction = _records(code="000001", prediction_date="2026-06-01", horizons=(1,))[0]
    prediction["tradable_budget_fen"] = 200_000
    bars = [
        {"date": f"2026-06-{day:02d}", "close": 10 + day / 10}
        for day in range(1, 5)
    ]

    outcome = evaluate_prediction_record(
        prediction,
        bars=bars,
        broad_benchmark_return_pct=0.25,
        tradable_universe_return_pct=-0.5,
        as_of="2026-07-15",
    )

    assert outcome["gross_return_pct"] == outcome["actual_return_pct"]
    assert outcome["cost_model_version"]
    assert outcome["tradable"] is False
    assert outcome["untradable_reason"] == "entry_policy_missing"
    assert outcome["net_tradable_return_pct"] is None
    assert outcome["promotion_eligible"] is False


def test_estimated_cost_keeps_net_return_visible_but_blocks_outcome_promotion(monkeypatch):
    monkeypatch.delenv("CONGXI_COMMISSION_RATE", raising=False)
    prediction = _records(code="000001", prediction_date="2026-06-01", horizons=(1,))[0]
    prediction.update(
        {
            "broad_benchmark_id": "000300.SH",
            "tradable_universe_snapshot_id": "target_pool:sha256:fixture",
            "data_cutoff": "2026-06-01T15:00:00+08:00",
            "tradable_budget_fen": 200_000,
            "entry_policy": "prediction_close",
            "exit_policy": "horizon_close",
        }
    )
    bars = [
        {"date": f"2026-06-{day:02d}", "close": 10 + day / 10}
        for day in range(1, 5)
    ]

    outcome = evaluate_prediction_record(
        prediction,
        bars=bars,
        broad_benchmark_return_pct=0.25,
        tradable_universe_return_pct=-0.5,
        as_of="2026-07-15",
    )

    assert outcome["cost_estimated"] is True
    assert outcome["net_tradable_return_pct"] is not None
    assert outcome["promotion_eligible"] is False
