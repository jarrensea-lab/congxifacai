"""只读离线 A 股分钟数据源。

外部数据保持原位并按需读取 ZIP 成员。该数据源只允许 ``shadow`` 模式，
用于回测、预测补样和研究验证，不能替代盘中实时行情。
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import math
import os
import zipfile
from dataclasses import dataclass
from datetime import datetime, time
from pathlib import Path
from typing import Any

from app.data_sources.base import BaseDataSource
from app.utils.a_share_codes import a_share_exchange, normalize_a_share_code


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_REGISTRY_PATH = PROJECT_ROOT / "data" / "external_market_data_sources.json"
SUPPORTED_PERIODS = {"1", "5", "15", "30", "60", "day"}
NUMERIC_FIELDS = {
    "open": "开盘价",
    "close": "收盘价",
    "high": "最高价",
    "low": "最低价",
    "volume": "成交量",
    "amount": "成交额",
}


class OfflineArchiveDataError(ValueError):
    """Safe, named failure for invalid rows or ZIP members."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class ExternalMarketDataRegistry:
    """外部历史行情数据的位置与安全模式。"""

    path: Path
    mode: str
    minute_root: Path
    tushare_factor_root: Path
    vendor_factor_root: Path | None = None

    @classmethod
    def load(cls, path: str | Path) -> "ExternalMarketDataRegistry":
        registry_path = Path(path).expanduser().resolve()
        payload = json.loads(registry_path.read_text(encoding="utf-8"))
        mode = str(payload.get("mode") or "").strip().lower()
        if mode != "shadow":
            raise ValueError("external historical market data must stay in shadow mode")

        minute_root = _required_root(payload, ("minute_data", "root"))
        factor_root = _required_root(
            payload,
            ("adjustment_factors", "tushare_csv", "root"),
        )
        primary = (
            payload.get("adjustment_factors", {}).get("primary")
            if isinstance(payload.get("adjustment_factors"), dict)
            else None
        )
        if primary != "tushare_csv":
            raise ValueError("tushare_csv must be the primary adjustment factor source")

        vendor_value = (
            payload.get("adjustment_factors", {})
            .get("vendor_archives", {})
            .get("root")
        )
        vendor_root = Path(vendor_value).expanduser().resolve() if vendor_value else None
        return cls(
            path=registry_path,
            mode=mode,
            minute_root=minute_root,
            tushare_factor_root=factor_root,
            vendor_factor_root=vendor_root,
        )

    @classmethod
    def load_default(cls) -> "ExternalMarketDataRegistry":
        configured = os.getenv("CONGXI_EXTERNAL_MARKET_DATA_REGISTRY")
        return cls.load(configured or DEFAULT_REGISTRY_PATH)


def _required_root(payload: dict[str, Any], keys: tuple[str, ...]) -> Path:
    value: Any = payload
    for key in keys:
        if not isinstance(value, dict):
            value = None
            break
        value = value.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"missing registry path: {'.'.join(keys)}")
    return Path(value).expanduser().resolve()


class OfflineMinuteDataSource(BaseDataSource):
    """从年度分钟 ZIP 懒读取单只股票，并可按日应用前复权。"""

    def __init__(self, registry: ExternalMarketDataRegistry):
        super().__init__("offline_minute_archive")
        self.registry = registry

    @classmethod
    def from_default_registry(cls) -> "OfflineMinuteDataSource":
        return cls(ExternalMarketDataRegistry.load_default())

    def is_available(self) -> bool:
        return (
            self.registry.mode == "shadow"
            and self.registry.minute_root.is_dir()
            and self.registry.tushare_factor_root.is_dir()
        )

    async def fetch(self, stock_code: str) -> dict[str, Any]:
        return await self.fetch_kline(
            stock_code,
            period="day",
            count=1,
            adjustment="none",
        )

    async def fetch_kline(
        self,
        stock_code: str,
        period: str = "day",
        count: int = 120,
        *,
        adjustment: str = "qfq",
        as_of: str | None = None,
    ) -> dict[str, Any]:
        if period not in SUPPORTED_PERIODS:
            return self._error(stock_code, period, "unsupported_period")
        if adjustment not in {"none", "qfq"}:
            return self._error(stock_code, period, "unsupported_adjustment")
        if count < 1:
            return self._error(stock_code, period, "invalid_count")
        try:
            _normalize_stock_code(stock_code)
        except ValueError:
            return self._error(stock_code, period, "invalid_stock_code")
        source_available = self.registry.minute_root.is_dir() and (
            adjustment == "none" or self.registry.tushare_factor_root.is_dir()
        )
        if self.registry.mode != "shadow" or not source_available:
            return self._error(stock_code, period, "source_unavailable")

        try:
            return await asyncio.to_thread(
                self._fetch_kline_sync,
                stock_code,
                period,
                count,
                adjustment,
                as_of,
            )
        except OfflineArchiveDataError as exc:
            return self._error(stock_code, period, exc.reason)
        except (OSError, ValueError, zipfile.BadZipFile, csv.Error) as exc:
            result = self._error(stock_code, period, "archive_read_failed")
            result["error_type"] = type(exc).__name__
            return result

    def _fetch_kline_sync(
        self,
        stock_code: str,
        period: str,
        count: int,
        adjustment: str,
        as_of: str | None,
    ) -> dict[str, Any]:
        code, exchange, prefix = _normalize_stock_code(stock_code)
        archive_period = "1" if period == "day" else period
        archives = self._archives(prefix, archive_period, as_of)
        if not archives:
            return self._error(code, period, "archive_not_found")

        rows: list[dict[str, Any]] = []
        used_archives: list[Path] = []
        for archive in archives:
            year = archive.name.split("_", 1)[0]
            member = f"{prefix}{code}_{year}.csv"
            archive_rows = _read_member_rows(archive, member, as_of)
            if not archive_rows:
                continue
            rows.extend(archive_rows)
            used_archives.append(archive)
            if period == "day":
                if len({row["date"][:10] for row in rows}) >= count:
                    break
            elif len(rows) >= count:
                break

        if not rows:
            return self._error(code, period, "stock_data_not_found")
        rows.sort(key=lambda item: item["date"])
        timestamps = [row["date"] for row in rows]
        if len(timestamps) != len(set(timestamps)):
            raise OfflineArchiveDataError("duplicate_timestamp")

        if adjustment == "qfq":
            adjusted = self._apply_qfq(rows, code, exchange, as_of)
            if isinstance(adjusted, dict):
                result = self._error(code, period, adjusted["reason"])
                result["missing_factor_dates"] = adjusted.get(
                    "missing_factor_dates",
                    [],
                )
                return result
            rows = adjusted

        bars = _aggregate_daily(rows) if period == "day" else _minute_bars(rows)
        bars = bars[-count:]
        newest_archive_mtime = max(path.stat().st_mtime for path in used_archives)
        return {
            "status": "ok",
            "code": code,
            "period": period,
            "bars": bars,
            "source": self.name,
            "vendor": "local_external_archive",
            "adjustment": adjustment,
            "shadow_only": True,
            "freshness": "historical_only",
            "provider_timestamp": datetime.fromtimestamp(
                newest_archive_mtime
            ).astimezone().isoformat(),
            "captured_at": datetime.now().astimezone().isoformat(),
            "data_cutoff": rows[-1]["date"],
            "archive_count": len(used_archives),
            "missing_fields": [],
        }

    def _archives(
        self,
        prefix: str,
        period: str,
        as_of: str | None,
    ) -> list[Path]:
        market_dir = (
            "A股_分时数据_京市"
            if prefix == "bj"
            else "A股_分时数据_沪深"
        )
        archive_dir = (
            self.registry.minute_root
            / market_dir
            / f"{period}分钟_按年汇总"
        )
        if not archive_dir.is_dir():
            return []
        max_year = _parse_as_of(as_of).year if as_of else None
        archives = []
        for path in archive_dir.glob(f"*_{period}min.zip"):
            try:
                year = int(path.name.split("_", 1)[0])
            except ValueError:
                continue
            if max_year is None or year <= max_year:
                archives.append(path)
        return sorted(archives, key=lambda path: path.name, reverse=True)

    def _apply_qfq(
        self,
        rows: list[dict[str, Any]],
        code: str,
        exchange: str,
        as_of: str | None,
    ) -> list[dict[str, Any]] | dict[str, Any]:
        factor_path = self.registry.tushare_factor_root / f"{code}.{exchange}.csv"
        if not factor_path.is_file():
            return {"reason": "adjustment_factor_file_not_found"}

        factors: dict[str, float] = {}
        max_date = _parse_as_of(as_of).strftime("%Y%m%d") if as_of else None
        with factor_path.open(encoding="utf-8-sig", newline="") as handle:
            for factor_row in csv.DictReader(handle):
                trade_date = str(factor_row.get("交易日期") or "").strip()
                if not trade_date or (max_date and trade_date > max_date):
                    continue
                factor = _number(factor_row.get("复权因子"))
                if factor > 0:
                    factors[trade_date] = factor

        if not factors:
            return {"reason": "adjustment_factor_not_found"}
        reference_factor = factors[max(factors)]
        missing_dates = sorted(
            {
                row["date"][:10].replace("-", "")
                for row in rows
                if row["date"][:10].replace("-", "") not in factors
            }
        )
        if missing_dates:
            return {
                "reason": "missing_adjustment_factor",
                "missing_factor_dates": missing_dates,
            }

        adjusted = []
        for row in rows:
            trade_date = row["date"][:10].replace("-", "")
            ratio = factors[trade_date] / reference_factor
            adjusted.append(
                {
                    **row,
                    "open": row["open"] * ratio,
                    "close": row["close"] * ratio,
                    "high": row["high"] * ratio,
                    "low": row["low"] * ratio,
                }
            )
        return adjusted

    def _error(self, code: str, period: str, reason: str) -> dict[str, Any]:
        return {
            "status": "error",
            "code": code,
            "period": period,
            "bars": [],
            "source": self.name,
            "shadow_only": True,
            "reason": reason,
            "missing_fields": ["kline"],
        }


def _normalize_stock_code(stock_code: str) -> tuple[str, str, str]:
    code = normalize_a_share_code(stock_code)
    exchange = a_share_exchange(code)
    return code, exchange, exchange.lower()


def _parse_as_of(value: str | None) -> datetime:
    if not value:
        return datetime.max
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if len(value.strip()) == 10:
        parsed = datetime.combine(parsed.date(), time.max)
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return parsed


def _read_member_rows(
    archive: Path,
    member: str,
    as_of: str | None,
) -> list[dict[str, Any]]:
    cutoff = _parse_as_of(as_of)
    rows = []
    with zipfile.ZipFile(archive) as bundle:
        case_matches = []
        unsafe_matches = []
        for info in bundle.infolist():
            normalized = info.filename.replace("\\", "/")
            basename = normalized.rsplit("/", 1)[-1]
            if basename.lower() != member.lower():
                continue
            if (
                info.is_dir()
                or normalized != info.filename
                or "/" in normalized
                or normalized in {"", ".", ".."}
            ):
                unsafe_matches.append(info.filename)
            elif info.filename.lower() == member.lower():
                case_matches.append(info.filename)
        if len(case_matches) > 1:
            raise OfflineArchiveDataError("archive_member_ambiguous")
        if unsafe_matches:
            raise OfflineArchiveDataError("archive_member_unsafe")
        if not case_matches:
            return []
        member_name = case_matches[0]
        with bundle.open(member_name) as raw:
            text = io.TextIOWrapper(raw, encoding="utf-8-sig", newline="")
            for item in csv.DictReader(text):
                timestamp = str(item.get("时间") or "").strip()
                if not timestamp:
                    continue
                observed_at = datetime.fromisoformat(timestamp.replace("/", "-"))
                if observed_at > cutoff:
                    continue
                numeric: dict[str, float] = {}
                for field, column in NUMERIC_FIELDS.items():
                    raw_value = item.get(column)
                    if raw_value in (None, ""):
                        raise OfflineArchiveDataError("invalid_numeric_row")
                    try:
                        parsed = float(raw_value)
                    except (TypeError, ValueError):
                        raise OfflineArchiveDataError(
                            "invalid_numeric_row"
                        ) from None
                    if not math.isfinite(parsed):
                        raise OfflineArchiveDataError("invalid_numeric_row")
                    numeric[field] = parsed
                if (
                    min(
                        numeric["open"],
                        numeric["close"],
                        numeric["high"],
                        numeric["low"],
                    )
                    <= 0
                    or numeric["volume"] < 0
                    or numeric["amount"] < 0
                    or numeric["high"] < max(numeric["open"], numeric["close"])
                    or numeric["low"] > min(numeric["open"], numeric["close"])
                ):
                    raise OfflineArchiveDataError("invalid_numeric_row")
                rows.append(
                    {
                        "date": observed_at.isoformat(
                            sep=" ",
                            timespec="seconds",
                        ),
                        **numeric,
                    }
                )
    return rows


def _number(value: Any) -> float:
    if value in (None, ""):
        return 0.0
    return float(value)


def _round_price(value: float) -> float:
    return round(value, 4)


def _aggregate_daily(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    days: dict[str, dict[str, Any]] = {}
    for row in rows:
        trade_date = row["date"][:10]
        if trade_date not in days:
            days[trade_date] = {
                "date": trade_date,
                "open": row["open"],
                "close": row["close"],
                "high": row["high"],
                "low": row["low"],
                "volume": row["volume"],
                "amount": row["amount"],
            }
            continue
        day = days[trade_date]
        day["close"] = row["close"]
        day["high"] = max(day["high"], row["high"])
        day["low"] = min(day["low"], row["low"])
        day["volume"] += row["volume"]
        day["amount"] += row["amount"]

    bars = []
    previous_close = None
    for trade_date in sorted(days):
        day = days[trade_date]
        change_pct = (
            (day["close"] - previous_close) / previous_close * 100
            if previous_close
            else 0.0
        )
        bars.append(
            {
                "date": trade_date,
                "open": _round_price(day["open"]),
                "close": _round_price(day["close"]),
                "high": _round_price(day["high"]),
                "low": _round_price(day["low"]),
                "volume": round(day["volume"], 4),
                "amount": round(day["amount"], 4),
                "change_pct": round(change_pct, 4),
            }
        )
        previous_close = day["close"]
    return bars


def _minute_bars(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "date": row["date"],
            "open": _round_price(row["open"]),
            "close": _round_price(row["close"]),
            "high": _round_price(row["high"]),
            "low": _round_price(row["low"]),
            "volume": round(row["volume"], 4),
            "amount": round(row["amount"], 4),
        }
        for row in rows
    ]
