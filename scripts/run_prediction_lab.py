#!/usr/bin/env python3
"""Collect and evaluate all-market prediction ledger records."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "backend"))
sys.path.insert(0, str(PROJECT_ROOT))

from app.data_sources.realtime_market_data import FastRealtimeMarketDataSource
from app.data_sources.offline_market_data import OfflineMinuteDataSource
from app.data_sources.tushare_client import TushareDataSource
from app.services.prediction_lab import (
    PredictionLedger,
    build_prediction_records,
    evaluate_prediction_record,
    suggest_strategy_adjustments,
    summarize_prediction_outcomes,
)
from app.services.quant_lifecycle import CandidatePoolStore

BACKFILL_KLINE_PROVIDER_LIMIT = 2000
BACKFILL_KLINE_SAFETY_DAYS = 10


def _today() -> str:
    return date.today().isoformat()


def _target_pool_universe(limit: int | None) -> list[dict]:
    payload = CandidatePoolStore().load()
    rows = [
        {"code": item.get("code"), "name": item.get("name") or item.get("code"), "source": "target_pool"}
        for item in (payload.get("items") or {}).values()
        if isinstance(item, dict) and item.get("code") and item.get("status") not in {"removed", "expired"}
    ]
    return rows[:limit] if limit else rows


def _tushare_universe(limit: int | None) -> list[dict]:
    source = TushareDataSource()
    if not source.is_available():
        return []
    return source.fetch_stock_basic(limit=limit)


def _load_universe(kind: str, limit: int | None) -> list[dict]:
    if kind == "tushare_all":
        return _tushare_universe(limit)
    return _target_pool_universe(limit)


def _horizon_return(
    bars: list[dict],
    prediction_date: str,
    horizon_days: int,
) -> float | None:
    normalized = [{**item, "date": str(item.get("date") or "")[:10]} for item in bars]
    start_index = next(
        (index for index, item in enumerate(normalized) if item["date"] == prediction_date[:10]),
        None,
    )
    if start_index is None or start_index + horizon_days >= len(normalized):
        return None
    try:
        start = float(normalized[start_index].get("close") or 0)
        end = float(normalized[start_index + horizon_days].get("close") or 0)
    except (TypeError, ValueError):
        return None
    if start <= 0 or end <= 0:
        return None
    return round((end - start) / start * 100, 2)


async def _evaluate_with_benchmarks(
    predictions: list[dict],
    *,
    bars_by_code: dict[str, list[dict]],
    quote_source,
    as_of: str,
    kline_count: int,
) -> list[dict]:
    preliminary = [
        evaluate_prediction_record(
            prediction,
            bars=bars_by_code.get(str(prediction.get("code") or ""), []),
            as_of=as_of,
        )
        for prediction in predictions
    ]
    universe_values: dict[tuple[str, int], list[float]] = {}
    for prediction, outcome in zip(predictions, preliminary, strict=True):
        if outcome.get("status") != "verified":
            continue
        key = (
            str(prediction.get("prediction_date") or "")[:10],
            int(prediction.get("horizon_days") or 0),
        )
        universe_values.setdefault(key, []).append(float(outcome["actual_return_pct"]))
    universe_returns = {
        key: round(sum(values) / len(values), 2)
        for key, values in universe_values.items()
        if values
    }

    benchmark_bars: dict[str, list[dict]] = {}
    benchmark_ids = {
        str(prediction.get("broad_benchmark_id") or "")
        for prediction in predictions
        if str(prediction.get("broad_benchmark_id") or "") not in {"", "unavailable"}
    }
    for benchmark_id in benchmark_ids:
        try:
            response = await quote_source.fetch_kline(
                benchmark_id,
                "day",
                count=kline_count,
            )
            benchmark_bars[benchmark_id] = (response or {}).get("bars") or []
        except Exception:
            benchmark_bars[benchmark_id] = []

    evaluated: list[dict] = []
    for prediction in predictions:
        key = (
            str(prediction.get("prediction_date") or "")[:10],
            int(prediction.get("horizon_days") or 0),
        )
        benchmark_id = str(prediction.get("broad_benchmark_id") or "")
        broad_return = _horizon_return(
            benchmark_bars.get(benchmark_id, []),
            key[0],
            key[1],
        )
        evaluated.append(
            evaluate_prediction_record(
                prediction,
                bars=bars_by_code.get(str(prediction.get("code") or ""), []),
                broad_benchmark_return_pct=broad_return,
                tradable_universe_return_pct=universe_returns.get(key),
                as_of=as_of,
            )
        )
    return evaluated


async def collect_predictions(args: argparse.Namespace) -> dict:
    day = args.date or _today()
    universe = _load_universe(args.universe, args.limit)
    quote_source = FastRealtimeMarketDataSource()
    ledger = PredictionLedger(args.output_root)
    codes = [item["code"] for item in universe if item.get("code")]
    universe_digest = hashlib.sha256(
        json.dumps(sorted(codes), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:20]
    tradable_universe_snapshot_id = f"{args.universe}:sha256:{universe_digest}"
    budget_yuan = float(os.getenv("CONGXI_PREDICTION_BUDGET_YUAN", "2000"))
    tradable_budget_fen = int(round(budget_yuan * 100)) if budget_yuan > 0 else None
    commission_text = os.getenv("CONGXI_COMMISSION_RATE")
    commission_rate = float(commission_text) if commission_text not in (None, "") else None
    quotes = await quote_source.fetch_batch(codes)
    records: list[dict] = []
    skipped: list[dict] = []
    for item in universe:
        code = item.get("code")
        quote = quotes.get(code) or {}
        price = float(quote.get("price") or 0)
        if price <= 0:
            skipped.append({"code": code, "reason": "quote_missing"})
            continue
        kline = await quote_source.fetch_kline(code, "day", count=args.kline_count)
        bars = kline.get("bars") or []
        if len(bars) < 10:
            skipped.append({"code": code, "reason": "kline_insufficient"})
            continue
        records.extend(
            build_prediction_records(
                code=code,
                name=quote.get("name") or item.get("name") or code,
                quote=quote,
                bars=bars,
                prediction_date=day,
                source=f"prediction_lab:{args.universe}",
                broad_benchmark_id="sh000001",
                tradable_universe_snapshot_id=tradable_universe_snapshot_id,
                data_cutoff=quote.get("quote_timestamp") or day,
                tradable_budget_fen=tradable_budget_fen,
                entry_policy="prediction_close",
                exit_policy="horizon_close",
                commission_rate=commission_rate,
            )
        )
    written = ledger.append_predictions(records)
    return {
        "mode": "collect",
        "date": day,
        "universe": args.universe,
        "universe_count": len(universe),
        "record_count": len(records),
        "written": written,
        "skipped_count": len(skipped),
        "prediction_path": str(ledger.prediction_path(day)),
    }


async def evaluate_predictions(args: argparse.Namespace) -> dict:
    prediction_date = args.date
    if not prediction_date:
        raise SystemExit("--date is required for evaluate")
    ledger = PredictionLedger(args.output_root)
    predictions = ledger.read_jsonl(ledger.prediction_path(prediction_date))
    quote_source = FastRealtimeMarketDataSource()
    selected = predictions[: args.limit if args.limit else None]
    bars_by_code: dict[str, list[dict]] = {}
    for prediction in selected:
        code = prediction.get("code")
        kline = await quote_source.fetch_kline(code, "day", count=args.kline_count)
        bars_by_code[str(code)] = kline.get("bars") or []
    outcomes = await _evaluate_with_benchmarks(
        selected,
        bars_by_code=bars_by_code,
        quote_source=quote_source,
        as_of=args.as_of or _today(),
        kline_count=args.kline_count,
    )
    written = ledger.append_outcomes(outcomes, outcome_date=args.as_of or _today())
    return {
        "mode": "evaluate",
        "prediction_date": prediction_date,
        "as_of": args.as_of or _today(),
        "input_count": len(predictions),
        "evaluated_count": len(outcomes),
        "written": written,
        "summary": summarize_prediction_outcomes(outcomes),
        "adjustment_suggestions": suggest_strategy_adjustments(outcomes),
        "outcome_path": str(ledger.outcome_path(args.as_of or _today())),
    }


async def backfill_due_predictions(
    args: argparse.Namespace,
    *,
    quote_source: FastRealtimeMarketDataSource | None = None,
    offline_source=None,
) -> dict:
    """Evaluate every unfinished prediction while reusing one K-line fetch per code."""
    as_of = args.as_of or _today()
    ledger = PredictionLedger(args.output_root)
    predictions = ledger.due_predictions(as_of=as_of, limit=args.limit)
    source = quote_source or FastRealtimeMarketDataSource()
    if offline_source is None and quote_source is None:
        try:
            offline_source = OfflineMinuteDataSource.from_default_registry()
        except (OSError, ValueError):
            offline_source = None
    by_code: dict[str, list[dict]] = {}
    for prediction in predictions:
        by_code.setdefault(str(prediction.get("code") or ""), []).append(prediction)

    outcomes: list[dict] = []
    bars_by_code: dict[str, list[dict]] = {}
    code_errors: list[dict] = []
    history_overflow_count = 0
    offline_history_code_count = 0
    for code, code_predictions in by_code.items():
        prediction_dates = [
            date.fromisoformat(str(item.get("prediction_date"))[:10])
            for item in code_predictions
            if item.get("prediction_date")
        ]
        as_of_date = date.fromisoformat(as_of[:10])
        earliest_date = min(prediction_dates, default=as_of_date)
        max_horizon = max(
            (int(item.get("horizon_days") or 0) for item in code_predictions),
            default=0,
        )
        required_count = max(
            args.kline_count,
            (as_of_date - earliest_date).days + max_horizon + BACKFILL_KLINE_SAFETY_DAYS,
        )
        if required_count > BACKFILL_KLINE_PROVIDER_LIMIT:
            history_overflow_count += 1
            code_errors.append(
                {
                    "code": code,
                    "reason": "kline_history_overflow",
                    "required_count": required_count,
                    "provider_limit": BACKFILL_KLINE_PROVIDER_LIMIT,
                }
            )
            outcomes.extend(
                {
                    **evaluate_prediction_record(prediction, bars=[], as_of=as_of),
                    "status": "unavailable",
                    "reason": "kline_history_overflow",
                }
                for prediction in code_predictions
            )
            continue
        try:
            kline = {}
            if offline_source is not None:
                try:
                    kline = await offline_source.fetch_kline(
                        code,
                        "day",
                        count=required_count,
                        adjustment="qfq",
                        as_of=as_of,
                    )
                except (OSError, ValueError):
                    kline = {"status": "error", "reason": "source_error"}
                if kline.get("status") == "ok":
                    if not isinstance(kline.get("bars"), list) or not kline["bars"]:
                        code_errors.append(
                            {
                                "code": code,
                                "reason": "offline_history_invalid",
                            }
                        )
                        outcomes.extend(
                            {
                                **evaluate_prediction_record(
                                    prediction,
                                    bars=[],
                                    as_of=as_of,
                                ),
                                "status": "unavailable",
                                "reason": "offline_history_invalid",
                            }
                            for prediction in code_predictions
                        )
                        continue
                    offline_history_code_count += 1
                elif kline.get("status") == "error":
                    kline = {}
                else:
                    code_errors.append(
                        {"code": code, "reason": "offline_history_invalid"}
                    )
                    outcomes.extend(
                        {
                            **evaluate_prediction_record(
                                prediction,
                                bars=[],
                                as_of=as_of,
                            ),
                            "status": "unavailable",
                            "reason": "offline_history_invalid",
                        }
                        for prediction in code_predictions
                    )
                    continue
            if not kline:
                kline = await source.fetch_kline(
                    code,
                    "day",
                    count=required_count,
                )
            bars = kline.get("bars") or []
            bars_by_code[code] = bars
            outcomes.extend(
                evaluate_prediction_record(prediction, bars=bars, as_of=as_of)
                for prediction in code_predictions
            )
        except Exception as exc:
            code_errors.append({"code": code, "reason": f"kline_fetch_failed: {type(exc).__name__}"})
            outcomes.extend(
                {
                    **evaluate_prediction_record(prediction, bars=[], as_of=as_of),
                    "status": "unavailable",
                    "reason": "kline_fetch_failed",
                }
                for prediction in code_predictions
            )

    verified_ids = {
        item.get("prediction_id")
        for item in outcomes
        if item.get("status") == "verified"
    }
    benchmark_predictions = [
        prediction
        for prediction in predictions
        if prediction.get("prediction_id") in verified_ids
    ]
    benchmark_outcomes = await _evaluate_with_benchmarks(
        benchmark_predictions,
        bars_by_code=bars_by_code,
        quote_source=source,
        as_of=as_of,
        kline_count=max(args.kline_count, 250),
    )
    benchmark_by_id = {
        item.get("prediction_id"): item for item in benchmark_outcomes
    }
    outcomes = [benchmark_by_id.get(item.get("prediction_id"), item) for item in outcomes]

    written = ledger.append_outcomes(outcomes, outcome_date=as_of)
    return {
        "mode": "backfill",
        "as_of": as_of,
        "due_count": len(predictions),
        "code_count": len(by_code),
        "code_error_count": len(code_errors),
        "history_overflow_count": history_overflow_count,
        "offline_history_code_count": offline_history_code_count,
        "code_errors": code_errors,
        "evaluated_count": len(outcomes),
        "verified_count": sum(item.get("status") == "verified" for item in outcomes),
        "written": written,
        "summary": summarize_prediction_outcomes(outcomes),
        "adjustment_suggestions": suggest_strategy_adjustments(outcomes),
        "outcome_path": str(ledger.outcome_path(as_of)),
    }


async def _main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("collect", "evaluate", "backfill"))
    parser.add_argument("--date", default=None)
    parser.add_argument("--as-of", default=None)
    parser.add_argument("--universe", choices=("target_pool", "tushare_all"), default="target_pool")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--kline-count", type=int, default=40)
    parser.add_argument("--output-root", default=None)
    args = parser.parse_args()

    if args.mode == "collect":
        result = await collect_predictions(args)
    elif args.mode == "evaluate":
        result = await evaluate_predictions(args)
    else:
        result = await backfill_due_predictions(args)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
