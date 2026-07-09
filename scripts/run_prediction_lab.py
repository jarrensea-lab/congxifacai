#!/usr/bin/env python3
"""Collect and evaluate all-market prediction ledger records."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "backend"))
sys.path.insert(0, str(PROJECT_ROOT))

from app.data_sources.realtime_market_data import FastRealtimeMarketDataSource
from app.data_sources.tushare_client import TushareDataSource
from app.services.prediction_lab import (
    PredictionLedger,
    build_prediction_records,
    evaluate_prediction_record,
    suggest_strategy_adjustments,
    summarize_prediction_outcomes,
)
from app.services.quant_lifecycle import CandidatePoolStore


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


async def collect_predictions(args: argparse.Namespace) -> dict:
    day = args.date or _today()
    universe = _load_universe(args.universe, args.limit)
    quote_source = FastRealtimeMarketDataSource()
    ledger = PredictionLedger(args.output_root)
    codes = [item["code"] for item in universe if item.get("code")]
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
    outcomes: list[dict] = []
    for prediction in predictions[: args.limit if args.limit else None]:
        code = prediction.get("code")
        kline = await quote_source.fetch_kline(code, "day", count=args.kline_count)
        outcomes.append(evaluate_prediction_record(prediction, bars=kline.get("bars") or [], as_of=args.as_of or _today()))
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


async def _main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("collect", "evaluate"))
    parser.add_argument("--date", default=None)
    parser.add_argument("--as-of", default=None)
    parser.add_argument("--universe", choices=("target_pool", "tushare_all"), default="target_pool")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--kline-count", type=int, default=40)
    parser.add_argument("--output-root", default=None)
    args = parser.parse_args()

    if args.mode == "collect":
        result = await collect_predictions(args)
    else:
        result = await evaluate_predictions(args)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
