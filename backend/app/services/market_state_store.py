"""Local A-share base-data reader for tradeability checks."""
from __future__ import annotations

import csv
import os
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any


BASE_DATA_ENV = "CONGXI_A_SHARE_BASE_DATA_DIR"
DEFAULT_BASE_DATA_DIR = "/Volumes/豪鬼/学习/A股交易基础数据"


def _parse_date(value: str | date) -> date:
    if isinstance(value, date):
        return value
    raw = str(value or "").strip()
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"date must be YYYY-MM-DD or YYYYMMDD, got {value!r}")


def _date_key(value: str | date) -> str:
    return _parse_date(value).strftime("%Y%m%d")


def _date_iso(value: str | date) -> str:
    return _parse_date(value).isoformat()


def _to_float(value: Any, default: float | None = None) -> float | None:
    try:
        if value is None or value == "":
            return default
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return default


def normalize_ts_code(code: str) -> str:
    """Return a Tushare-style A-share code such as 000001.SZ."""
    raw = str(code or "").strip().upper()
    if not raw:
        return ""
    if "." in raw:
        prefix, suffix = raw.split(".", 1)
        return f"{prefix.zfill(6)}.{suffix}"
    raw = raw.replace("SH", "").replace("SZ", "").replace("BJ", "")
    suffix = "SH" if raw.startswith(("6", "9")) else "BJ" if raw.startswith(("8", "4")) else "SZ"
    return f"{raw.zfill(6)}.{suffix}"


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


class MarketStateStore:
    """Read base package files and answer daily tradeability questions."""

    def __init__(self, base_dir: str | Path | None = None):
        self.base_dir = Path(base_dir or os.environ.get(BASE_DATA_ENV, DEFAULT_BASE_DATA_DIR))

    @lru_cache(maxsize=1)
    def stock_master(self) -> dict[str, dict[str, str]]:
        rows = _read_csv(self.base_dir / "股票列表.csv")
        return {normalize_ts_code(row.get("TS代码") or row.get("股票代码") or ""): row for row in rows}

    @lru_cache(maxsize=1)
    def delisted_master(self) -> dict[str, dict[str, str]]:
        rows = _read_csv(self.base_dir / "退市股票列表.csv")
        return {normalize_ts_code(row.get("TS代码") or row.get("股票代码") or ""): row for row in rows}

    @lru_cache(maxsize=1)
    def trading_calendar(self) -> dict[str, dict[str, str]]:
        rows = _read_csv(self.base_dir / "交易日历.csv")
        return {str(row.get("日期", "")).strip(): row for row in rows}

    @lru_cache(maxsize=4096)
    def st_rows_for_date(self, day_key: str) -> dict[str, dict[str, str]]:
        path = self.base_dir / "ST股票列表_每日更新" / f"{day_key[:4]}-{day_key[4:6]}" / f"ST股票列表_{day_key}.csv"
        return {normalize_ts_code(row.get("股票代码", "")): row for row in _read_csv(path)}

    @lru_cache(maxsize=4096)
    def halt_rows_for_date(self, day_key: str) -> dict[str, list[dict[str, str]]]:
        path = self.base_dir / "停复牌_每日更新" / f"{day_key[:4]}-{day_key[4:6]}" / f"每日停复牌_{day_key}.csv"
        grouped: dict[str, list[dict[str, str]]] = {}
        for row in _read_csv(path):
            grouped.setdefault(normalize_ts_code(row.get("股票代码", "")), []).append(row)
        return grouped

    @lru_cache(maxsize=4096)
    def limit_rows_for_date(self, day_key: str) -> dict[str, dict[str, str]]:
        path = self.base_dir / "涨跌停价格_每日更新" / f"{day_key[:4]}-{day_key[4:6]}" / f"每日涨跌停价格_{day_key}.csv"
        return {normalize_ts_code(row.get("股票代码", "")): row for row in _read_csv(path)}

    def _stock_row(self, code: str) -> dict[str, str]:
        ts_code = normalize_ts_code(code)
        return self.stock_master().get(ts_code) or self.delisted_master().get(ts_code) or {}

    def get_trade_state(self, code: str, trade_date: str | date, price: float | None = None) -> dict[str, Any]:
        ts_code = normalize_ts_code(code)
        day_key = _date_key(trade_date)
        day_iso = _date_iso(trade_date)
        day = _parse_date(trade_date)
        stock = self._stock_row(ts_code)
        delisted = self.delisted_master().get(ts_code)
        st_row = self.st_rows_for_date(day_key).get(ts_code)
        halt_rows = self.halt_rows_for_date(day_key).get(ts_code, [])
        limit_row = self.limit_rows_for_date(day_key).get(ts_code, {})
        calendar_row = self.trading_calendar().get(day_iso, {})

        list_date = _parse_optional_yyyymmdd(stock.get("上市日期"))
        delist_date = _parse_optional_yyyymmdd((delisted or stock).get("退市日期"))
        listed = bool(stock) and (list_date is None or day >= list_date) and (delist_date is None or day < delist_date)
        is_delisted = delist_date is not None and day >= delist_date
        halt_types = [str(row.get("停复牌类型", "")).strip() for row in halt_rows]
        suspended = "停牌" in halt_types
        resumed = "复牌" in halt_types
        limit_up = _to_float(limit_row.get("涨停价"))
        limit_down = _to_float(limit_row.get("跌停价"))
        current_price = _to_float(price)
        at_limit_up = bool(current_price is not None and limit_up is not None and current_price >= limit_up)
        at_limit_down = bool(current_price is not None and limit_down is not None and current_price <= limit_down)
        near_limit_up = bool(current_price is not None and limit_up is not None and current_price >= limit_up * 0.995)
        near_limit_down = bool(current_price is not None and limit_down is not None and current_price <= limit_down * 1.005)

        block_reasons: list[str] = []
        if not calendar_row:
            block_reasons.append("calendar_missing")
        elif calendar_row.get("是否交易") != "交易":
            block_reasons.append("not_trading_day")
        if not stock:
            block_reasons.append("stock_missing")
        if is_delisted:
            block_reasons.append("delisted")
        elif not listed:
            block_reasons.append("not_listed")
        if st_row:
            block_reasons.append("st_flag")
        if suspended:
            block_reasons.append("suspended")
        if at_limit_up:
            block_reasons.append("at_limit_up")
        if at_limit_down:
            block_reasons.append("at_limit_down")

        return {
            "code": ts_code,
            "name": stock.get("股票名称", ts_code),
            "date": day_iso,
            "is_trading_day": calendar_row.get("是否交易") == "交易",
            "listed": listed,
            "delisted": is_delisted,
            "st": bool(st_row),
            "st_type": st_row.get("ST类型", "") if st_row else "",
            "st_name": st_row.get("ST类型名称", "") if st_row else "",
            "suspended": suspended,
            "resumed": resumed,
            "halt_types": halt_types,
            "previous_close": _to_float(limit_row.get("昨日收盘价")),
            "limit_up": limit_up,
            "limit_down": limit_down,
            "at_limit_up": at_limit_up,
            "at_limit_down": at_limit_down,
            "near_limit_up": near_limit_up,
            "near_limit_down": near_limit_down,
            "tradable": not block_reasons,
            "block_reasons": block_reasons,
            "source": "a_share_base_data",
        }


def _parse_optional_yyyymmdd(value: str | None) -> date | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    return datetime.strptime(raw, "%Y%m%d").date()


def evaluate_trade_eligibility(
    code: str,
    trade_date: str | date,
    price: float | None = None,
    store: MarketStateStore | None = None,
) -> dict[str, Any]:
    """Convenience wrapper for consumers that only need a tradeability result."""
    return (store or MarketStateStore()).get_trade_state(code, trade_date, price=price)
