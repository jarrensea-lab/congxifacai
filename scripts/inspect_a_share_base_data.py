#!/usr/bin/env python3
"""Inspect the local A-share base data package without importing it."""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

from app.services.market_state_store import DEFAULT_BASE_DATA_DIR, MarketStateStore


def _count_csv_rows(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        next(reader, None)
        return sum(1 for _ in reader)


def _count_files(path: Path, pattern: str = "*.csv") -> int:
    if not path.exists():
        return 0
    return sum(1 for item in path.rglob(pattern) if item.is_file())


def build_inspection_report(
    base_dir: str | Path,
    samples: list[tuple[str, str, float | None]] | None = None,
) -> dict[str, Any]:
    root = Path(base_dir)
    store = MarketStateStore(root)
    sample_inputs = samples or [
        ("000001", "2026-06-30", None),
        ("000005", "2024-04-26", None),
    ]
    return {
        "base_dir": str(root),
        "exists": root.exists(),
        "files": {
            "stock_master": {
                "path": str(root / "股票列表.csv"),
                "rows": _count_csv_rows(root / "股票列表.csv"),
            },
            "delisted_master": {
                "path": str(root / "退市股票列表.csv"),
                "rows": _count_csv_rows(root / "退市股票列表.csv"),
            },
            "name_history": {
                "path": str(root / "股票曾用名汇总.csv"),
                "rows": _count_csv_rows(root / "股票曾用名汇总.csv"),
            },
            "trading_calendar": {
                "path": str(root / "交易日历.csv"),
                "rows": _count_csv_rows(root / "交易日历.csv"),
            },
        },
        "daily_dirs": {
            "st_daily_files": _count_files(root / "ST股票列表_每日更新"),
            "halt_daily_files": _count_files(root / "停复牌_每日更新"),
            "limit_daily_files": _count_files(root / "涨跌停价格_每日更新"),
        },
        "archives": {
            "halt_zip_exists": (root / "每日停复牌.zip").exists(),
            "limit_zip_exists": (root / "每日涨跌停价格.zip").exists(),
        },
        "samples": [
            store.get_trade_state(code, trade_date, price=price)
            for code, trade_date, price in sample_inputs
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect A-share base data package coverage.")
    parser.add_argument("--base-dir", default=DEFAULT_BASE_DATA_DIR)
    parser.add_argument(
        "--sample",
        action="append",
        default=[],
        help="Sample query as code,date[,price], e.g. 000001,2026-06-30,10.24",
    )
    args = parser.parse_args()
    samples: list[tuple[str, str, float | None]] = []
    for raw in args.sample:
        parts = [part.strip() for part in raw.split(",")]
        if len(parts) not in {2, 3}:
            raise SystemExit(f"Invalid --sample {raw!r}; expected code,date[,price]")
        price = float(parts[2]) if len(parts) == 3 and parts[2] else None
        samples.append((parts[0], parts[1], price))
    report = build_inspection_report(args.base_dir, samples=samples or None)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
