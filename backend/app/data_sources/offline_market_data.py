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
import stat
import zipfile
from collections import deque
from dataclasses import dataclass
from datetime import datetime, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from app.data_sources.base import BaseDataSource
from app.utils.a_share_codes import a_share_exchange, normalize_a_share_code


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_REGISTRY_PATH = PROJECT_ROOT / "data" / "external_market_data_sources.json"
SUPPORTED_PERIODS = {"1", "5", "15", "30", "60", "day"}
MARKET_TIMEZONE = ZoneInfo("Asia/Shanghai")
# One annual, one-stock minute CSV should stay far below these fail-closed
# ceilings. They prevent a registry-selected archive from becoming an
# unbounded decompression/read operation while preserving normal vendor files.
MAX_ZIP_MEMBER_BYTES = 256 * 1024 * 1024
MAX_ZIP_COMPRESSION_RATIO = 250.0
MAX_CSV_ROWS = 1_000_000
RETAINED_TAIL_SAFETY = 5
NUMERIC_FIELDS = {
    "open": "开盘价",
    "close": "收盘价",
    "high": "最高价",
    "low": "最低价",
    "volume": "成交量",
    "amount": "成交额",
}
RETURNED_BAR_FIELDS = (
    "date",
    "open",
    "close",
    "high",
    "low",
    "volume",
    "amount",
)


class OfflineArchiveDataError(ValueError):
    """Safe, named failure for invalid rows or ZIP members."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class _ArchiveCandidate:
    path: Path
    member: str
    kind: str
    priority: int
    year: int


@dataclass(frozen=True)
class _ReadResult:
    rows: list[dict[str, Any]]
    data_cutoff: str | None
    retained_peak: int


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
        try:
            payload = json.loads(registry_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            raise ValueError("registry_json_invalid") from None
        if not isinstance(payload, dict):
            raise ValueError("registry_root_not_object")
        mode = str(payload.get("mode") or "").strip().lower()
        if mode != "shadow":
            raise ValueError("external historical market data must stay in shadow mode")

        _required_object(payload, ("minute_data",))
        adjustment_factors = _required_object(
            payload,
            ("adjustment_factors",),
        )
        _required_object(payload, ("adjustment_factors", "tushare_csv"))
        minute_root = _required_root(payload, ("minute_data", "root"))
        factor_root = _required_root(
            payload,
            ("adjustment_factors", "tushare_csv", "root"),
        )
        primary = adjustment_factors.get("primary")
        if primary != "tushare_csv":
            raise ValueError("tushare_csv must be the primary adjustment factor source")

        vendor_root = None
        if "vendor_archives" in adjustment_factors:
            _required_object(
                payload,
                ("adjustment_factors", "vendor_archives"),
            )
            vendor_root = _required_root(
                payload,
                ("adjustment_factors", "vendor_archives", "root"),
            )
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
    expanded = Path(value).expanduser()
    if not expanded.is_absolute():
        raise ValueError(f"registry_path_not_absolute:{'.'.join(keys)}")
    return expanded.resolve()


def _required_object(
    payload: dict[str, Any],
    keys: tuple[str, ...],
) -> dict[str, Any]:
    value: Any = payload
    for key in keys:
        if not isinstance(value, dict):
            value = None
            break
        value = value.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"registry_section_invalid:{'.'.join(keys)}")
    return value


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
        try:
            _parse_as_of(as_of)
        except OfflineArchiveDataError as exc:
            return self._error(stock_code, period, exc.reason)
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
        candidates = self._archive_candidates(
            prefix,
            code,
            archive_period,
            as_of,
            count=count,
            output_period=period,
        )
        if not candidates:
            return self._error(code, period, "archive_not_found")

        retention_limit = count + RETAINED_TAIL_SAFETY
        merged: dict[str, tuple[dict[str, Any], int]] = {}
        used_archives: list[Path] = []
        data_cutoffs: list[str] = []
        retained_peak = 0
        cutoff_year = _parse_as_of(as_of).year
        for candidate in candidates:
            if (
                candidate.kind == "annual"
                and candidate.year < cutoff_year
                and len(merged) >= retention_limit
            ):
                continue
            read_result = _read_member_rows(
                candidate.path,
                candidate.member,
                as_of,
                expected_code=code,
                expected_exchange=exchange,
                aggregate_daily=period == "day",
                retention_limit=retention_limit,
            )
            retained_peak = max(
                retained_peak,
                read_result.retained_peak,
            )
            if not read_result.rows:
                continue
            for row in read_result.rows:
                timestamp = row["date"]
                existing = merged.get(timestamp)
                if existing is not None:
                    existing_row, existing_priority = existing
                    if not _market_rows_equal(existing_row, row):
                        raise OfflineArchiveDataError(
                            "conflicting_overlap_timestamp"
                        )
                    if candidate.priority > existing_priority:
                        merged[timestamp] = (row, candidate.priority)
                    continue
                merged[timestamp] = (row, candidate.priority)
                if len(merged) > retention_limit:
                    del merged[min(merged)]
                retained_peak = max(retained_peak, len(merged))
            used_archives.append(candidate.path)
            if read_result.data_cutoff:
                data_cutoffs.append(read_result.data_cutoff)

        if not merged:
            return self._error(code, period, "stock_data_not_found")
        rows = [merged[key][0] for key in sorted(merged)]

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

        bars = (
            _daily_bars_from_aggregates(rows)
            if period == "day"
            else _minute_bars(rows)
        )
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
                newest_archive_mtime,
                tz=MARKET_TIMEZONE,
            ).isoformat(),
            "captured_at": datetime.now(MARKET_TIMEZONE).isoformat(),
            "data_cutoff": max(data_cutoffs),
            "archive_count": len(used_archives),
            "retained_row_peak": retained_peak,
            "missing_fields": [],
        }

    def _archive_candidates(
        self,
        prefix: str,
        code: str,
        period: str,
        as_of: str | None,
        *,
        count: int,
        output_period: str,
    ) -> list[_ArchiveCandidate]:
        market_dir = (
            "A股_分时数据_京市"
            if prefix == "bj"
            else "A股_分时数据_沪深"
        )
        market_root = self.registry.minute_root / market_dir
        cutoff = _parse_as_of(as_of)
        if output_period == "day":
            daily_limit = count + RETAINED_TAIL_SAFETY
        else:
            bars_per_day = max(1, 240 // int(period))
            daily_limit = (
                math.ceil((count + RETAINED_TAIL_SAFETY) / bars_per_day) + 2
            )

        daily_paths: list[tuple[datetime, Path]] = []
        monthly_root = market_root / f"{period}分钟_按月归档"
        if monthly_root.is_dir():
            for month_dir in sorted(monthly_root.iterdir(), reverse=True):
                if not month_dir.is_dir():
                    continue
                try:
                    month = datetime.strptime(month_dir.name, "%Y-%m")
                except ValueError:
                    continue
                if (month.year, month.month) > (cutoff.year, cutoff.month):
                    continue
                for path in sorted(
                    month_dir.glob(f"*_{period}min.zip"),
                    reverse=True,
                ):
                    date_text = path.name.split("_", 1)[0]
                    try:
                        archive_day = datetime.strptime(date_text, "%Y%m%d")
                    except ValueError:
                        continue
                    archive_day = archive_day.replace(
                        tzinfo=MARKET_TIMEZONE
                    )
                    if archive_day.date() <= cutoff.date():
                        daily_paths.append((archive_day, path))
                if len(daily_paths) >= daily_limit:
                    break
        daily_paths = sorted(
            daily_paths,
            key=lambda item: item[0],
            reverse=True,
        )[:daily_limit]
        daily_candidates = [
            _ArchiveCandidate(
                path=path,
                member=f"{prefix}{code}.csv",
                kind="daily",
                priority=2,
                year=archive_day.year,
            )
            for archive_day, path in daily_paths
        ]

        annual_dir = market_root / f"{period}分钟_按年汇总"
        annual_candidates: list[_ArchiveCandidate] = []
        if annual_dir.is_dir():
            annual_paths: list[tuple[int, Path]] = []
            for path in annual_dir.glob(f"*_{period}min.zip"):
                try:
                    year = int(path.name.split("_", 1)[0])
                except ValueError:
                    continue
                if year <= cutoff.year:
                    annual_paths.append((year, path))
            annual_candidates = [
                _ArchiveCandidate(
                    path=path,
                    member=f"{prefix}{code}_{year}.csv",
                    kind="annual",
                    priority=1,
                    year=year,
                )
                for year, path in sorted(annual_paths, reverse=True)
            ]
        return daily_candidates + annual_candidates

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
        seen_dates: set[str] = set()
        max_date = _parse_as_of(as_of).strftime("%Y%m%d") if as_of else None
        with factor_path.open(encoding="utf-8-sig", newline="") as handle:
            for factor_row in csv.DictReader(handle):
                factor_code = str(factor_row.get("股票代码") or "").strip()
                try:
                    normalized_code, normalized_exchange, _ = _normalize_stock_code(
                        factor_code
                    )
                except ValueError:
                    return {"reason": "adjustment_factor_code_invalid"}
                if (normalized_code, normalized_exchange) != (code, exchange):
                    return {"reason": "adjustment_factor_code_mismatch"}
                trade_date = str(factor_row.get("交易日期") or "").strip()
                try:
                    datetime.strptime(trade_date, "%Y%m%d")
                except ValueError:
                    return {"reason": "invalid_adjustment_factor_date"}
                if trade_date in seen_dates:
                    return {"reason": "duplicate_adjustment_factor_date"}
                seen_dates.add(trade_date)
                raw_factor = factor_row.get("复权因子")
                try:
                    factor = float(raw_factor)
                except (TypeError, ValueError):
                    return {"reason": "invalid_adjustment_factor"}
                if not math.isfinite(factor) or factor <= 0:
                    return {"reason": "invalid_adjustment_factor"}
                if max_date and trade_date > max_date:
                    continue
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
            try:
                ratio = factors[trade_date] / reference_factor
            except OverflowError:
                return {"reason": "adjusted_price_not_finite"}
            prices = {
                field: row[field] * ratio
                for field in ("open", "close", "high", "low")
            }
            if not all(math.isfinite(value) for value in prices.values()):
                return {"reason": "adjusted_price_not_finite"}
            adjusted.append(
                {
                    **row,
                    **prices,
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
    if value is None:
        return datetime.max.replace(tzinfo=MARKET_TIMEZONE)
    if not isinstance(value, str) or not value.strip():
        raise OfflineArchiveDataError("invalid_as_of")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except (TypeError, ValueError):
        raise OfflineArchiveDataError("invalid_as_of") from None
    if len(value.strip()) == 10:
        return datetime.combine(
            parsed.date(),
            time.max,
            tzinfo=MARKET_TIMEZONE,
        )
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return parsed.replace(tzinfo=MARKET_TIMEZONE)
    return parsed.astimezone(MARKET_TIMEZONE)


def _parse_archive_timestamp(value: Any) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise OfflineArchiveDataError("invalid_timestamp")
    try:
        parsed = datetime.fromisoformat(
            value.strip().replace("/", "-").replace("Z", "+00:00")
        )
    except (TypeError, ValueError):
        raise OfflineArchiveDataError("invalid_timestamp") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return parsed.replace(tzinfo=MARKET_TIMEZONE)
    return parsed.astimezone(MARKET_TIMEZONE)


def validate_offline_kline_response(
    response: Any,
    *,
    as_of: str | None = None,
    minimum_bars: int = 1,
) -> tuple[list[dict[str, Any]], str | None]:
    """Validate an offline ``status=ok`` payload at its consumer boundary."""
    invalid = ([], "offline_history_invalid")
    if (
        not isinstance(response, dict)
        or response.get("status") != "ok"
        or not isinstance(minimum_bars, int)
        or isinstance(minimum_bars, bool)
        or minimum_bars < 1
    ):
        return invalid
    bars = response.get("bars")
    if not isinstance(bars, list) or len(bars) < minimum_bars:
        return invalid
    try:
        cutoff = _parse_as_of(response.get("data_cutoff"))
        upper_bound = (
            _parse_as_of(as_of)
            if as_of is not None
            else datetime.now(MARKET_TIMEZONE)
        )
    except OfflineArchiveDataError:
        return invalid
    if cutoff > upper_bound:
        return invalid

    previous: datetime | None = None
    validated: list[dict[str, Any]] = []
    for bar in bars:
        if not isinstance(bar, dict) or any(
            field not in bar for field in RETURNED_BAR_FIELDS
        ):
            return invalid
        raw_date = bar.get("date")
        if not isinstance(raw_date, str) or not raw_date.strip():
            return invalid
        date_only = len(raw_date.strip()) == 10
        try:
            observed_at = (
                _parse_as_of(raw_date)
                if date_only
                else _parse_archive_timestamp(raw_date)
            )
        except OfflineArchiveDataError:
            return invalid
        if previous is not None and observed_at <= previous:
            return invalid
        previous = observed_at
        if date_only:
            if (
                observed_at.date() > cutoff.date()
                or observed_at.date() > upper_bound.date()
            ):
                return invalid
        elif observed_at > cutoff or observed_at > upper_bound:
            return invalid

        numeric: dict[str, float] = {}
        for field in ("open", "close", "high", "low", "volume", "amount"):
            value = bar.get(field)
            if isinstance(value, bool):
                return invalid
            try:
                parsed = float(value)
            except (TypeError, ValueError, OverflowError):
                return invalid
            if not math.isfinite(parsed):
                return invalid
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
            return invalid
        validated.append(bar)
    return validated, None


def _read_member_rows(
    archive: Path,
    member: str,
    as_of: str | None,
    *,
    expected_code: str,
    expected_exchange: str,
    aggregate_daily: bool,
    retention_limit: int,
) -> _ReadResult:
    cutoff = _parse_as_of(as_of)
    retained: deque[dict[str, Any]] = deque(maxlen=retention_limit)
    current_day: dict[str, Any] | None = None
    previous_timestamp: datetime | None = None
    data_cutoff: str | None = None
    retained_peak = 0
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
            return _ReadResult([], None, 0)
        member_name = case_matches[0]
        _validate_zip_member_info(bundle.getinfo(member_name))
        with bundle.open(member_name) as raw:
            text = io.TextIOWrapper(raw, encoding="utf-8-sig", newline="")
            for row_number, item in enumerate(csv.DictReader(text), start=1):
                if row_number > MAX_CSV_ROWS:
                    raise OfflineArchiveDataError(
                        "archive_row_limit_exceeded"
                    )
                row_code = str(item.get("代码") or "").strip()
                try:
                    normalized_code, normalized_exchange, _ = _normalize_stock_code(
                        row_code
                    )
                except ValueError:
                    raise OfflineArchiveDataError(
                        "minute_row_code_invalid"
                    ) from None
                if (normalized_code, normalized_exchange) != (
                    expected_code,
                    expected_exchange,
                ):
                    raise OfflineArchiveDataError("minute_row_code_mismatch")
                timestamp = item.get("时间")
                observed_at = _parse_archive_timestamp(timestamp)
                if previous_timestamp is not None:
                    if observed_at == previous_timestamp:
                        raise OfflineArchiveDataError("duplicate_timestamp")
                    if observed_at < previous_timestamp:
                        raise OfflineArchiveDataError(
                            "non_monotonic_timestamp"
                        )
                previous_timestamp = observed_at
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
                if observed_at > cutoff:
                    continue
                normalized_timestamp = observed_at.replace(
                    tzinfo=None
                ).isoformat(
                    sep=" ",
                    timespec="seconds",
                )
                data_cutoff = normalized_timestamp
                if aggregate_daily:
                    trade_date = normalized_timestamp[:10]
                    if current_day is None:
                        current_day = _new_daily_aggregate(
                            trade_date,
                            numeric,
                        )
                    elif current_day["date"] == trade_date:
                        _update_daily_aggregate(current_day, numeric)
                    else:
                        retained.append(current_day)
                        current_day = _new_daily_aggregate(
                            trade_date,
                            numeric,
                        )
                    retained_peak = max(
                        retained_peak,
                        len(retained) + (1 if current_day else 0),
                    )
                else:
                    retained.append(
                        {
                            "date": normalized_timestamp,
                            **numeric,
                        }
                    )
                    retained_peak = max(retained_peak, len(retained))
    if current_day is not None:
        retained.append(current_day)
        retained_peak = max(retained_peak, len(retained))
    return _ReadResult(list(retained), data_cutoff, retained_peak)


def _new_daily_aggregate(
    trade_date: str,
    numeric: dict[str, float],
) -> dict[str, Any]:
    return {
        "date": trade_date,
        "open": numeric["open"],
        "close": numeric["close"],
        "high": numeric["high"],
        "low": numeric["low"],
        "volume": numeric["volume"],
        "amount": numeric["amount"],
    }


def _update_daily_aggregate(
    aggregate: dict[str, Any],
    numeric: dict[str, float],
) -> None:
    aggregate["close"] = numeric["close"]
    aggregate["high"] = max(aggregate["high"], numeric["high"])
    aggregate["low"] = min(aggregate["low"], numeric["low"])
    aggregate["volume"] += numeric["volume"]
    aggregate["amount"] += numeric["amount"]


def _market_rows_equal(
    left: dict[str, Any],
    right: dict[str, Any],
) -> bool:
    return all(
        left.get(field) == right.get(field)
        for field in (
            "date",
            "open",
            "close",
            "high",
            "low",
            "volume",
            "amount",
        )
    )


def _validate_zip_member_info(info: zipfile.ZipInfo) -> None:
    if info.flag_bits & 0x1:
        raise OfflineArchiveDataError("archive_member_encrypted")
    unix_mode = info.external_attr >> 16
    file_type = stat.S_IFMT(unix_mode)
    if file_type not in {0, stat.S_IFREG}:
        raise OfflineArchiveDataError("archive_member_non_regular")
    if info.file_size < 0 or info.file_size > MAX_ZIP_MEMBER_BYTES:
        raise OfflineArchiveDataError("archive_member_too_large")
    if info.file_size:
        if info.compress_size <= 0:
            raise OfflineArchiveDataError(
                "archive_member_compression_ratio_exceeded"
            )
        if info.file_size / info.compress_size > MAX_ZIP_COMPRESSION_RATIO:
            raise OfflineArchiveDataError(
                "archive_member_compression_ratio_exceeded"
            )


def _round_price(value: float) -> float:
    return round(value, 4)


def _daily_bars_from_aggregates(
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    bars = []
    previous_close = None
    for day in rows:
        change_pct = (
            (day["close"] - previous_close) / previous_close * 100
            if previous_close
            else 0.0
        )
        bars.append(
            {
                "date": day["date"],
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
