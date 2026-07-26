import csv
import json
import math
import stat
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app.data_sources.offline_market_data import (
    ExternalMarketDataRegistry,
    OfflineMinuteDataSource,
    OfflineArchiveDataError,
)
from app.data_sources import offline_market_data


def _write_csv(path: Path, rows: list[list[object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        csv.writer(handle).writerows(rows)


def _write_minute_archive(
    root: Path,
    *,
    period: str,
    year: int,
    member: str,
    rows: list[list[object]],
    market_dir: str = "A股_分时数据_沪深",
) -> None:
    archive = (
        root
        / market_dir
        / f"{period}分钟_按年汇总"
        / f"{year}_{period}min.zip"
    )
    archive.parent.mkdir(parents=True, exist_ok=True)
    payload = root / member
    _write_csv(payload, rows)
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        bundle.write(payload, arcname=member)
    payload.unlink()


def _write_daily_archive(
    root: Path,
    *,
    period: str,
    day: str,
    member: str,
    rows: list[list[object]],
    market_dir: str = "A股_分时数据_沪深",
) -> None:
    archive = (
        root
        / market_dir
        / f"{period}分钟_按月归档"
        / day[:7]
        / f"{day.replace('-', '')}_{period}min.zip"
    )
    archive.parent.mkdir(parents=True, exist_ok=True)
    payload = root / f"daily-{day}-{member}"
    _write_csv(payload, rows)
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        bundle.write(payload, arcname=member)
    payload.unlink()


def _registry(tmp_path: Path) -> ExternalMarketDataRegistry:
    minute_root = tmp_path / "minutes"
    factor_root = tmp_path / "factors"
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(
        json.dumps(
            {
                "version": 1,
                "mode": "shadow",
                "minute_data": {"root": str(minute_root)},
                "adjustment_factors": {
                    "primary": "tushare_csv",
                    "tushare_csv": {"root": str(factor_root)},
                    "vendor_archives": {"root": str(tmp_path / "vendor_factors")},
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return ExternalMarketDataRegistry.load(registry_path)


def test_registry_rejects_live_mode_for_external_historical_data(tmp_path):
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(
        json.dumps(
            {
                "version": 1,
                "mode": "live",
                "minute_data": {"root": str(tmp_path / "minutes")},
                "adjustment_factors": {
                    "primary": "tushare_csv",
                    "tushare_csv": {"root": str(tmp_path / "factors")},
                },
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="shadow"):
        ExternalMarketDataRegistry.load(registry_path)


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        ([], "registry_root_not_object"),
        (
            {
                "version": 1,
                "mode": "shadow",
                "minute_data": [],
                "adjustment_factors": {},
            },
            "registry_section_invalid:minute_data",
        ),
        (
            {
                "version": 1,
                "mode": "shadow",
                "minute_data": {"root": "relative/minutes"},
                "adjustment_factors": {
                    "primary": "tushare_csv",
                    "tushare_csv": {"root": "/absolute/factors"},
                },
            },
            "registry_path_not_absolute:minute_data.root",
        ),
        (
            {
                "version": 1,
                "mode": "shadow",
                "minute_data": {"root": "/absolute/minutes"},
                "adjustment_factors": {
                    "primary": "tushare_csv",
                    "tushare_csv": {"root": "/absolute/factors"},
                    "vendor_archives": [],
                },
            },
            "registry_section_invalid:adjustment_factors.vendor_archives",
        ),
        (
            {
                "version": 1,
                "mode": "shadow",
                "minute_data": {"root": "/absolute/minutes"},
                "adjustment_factors": {
                    "primary": "tushare_csv",
                    "tushare_csv": {"root": "/absolute/factors"},
                    "vendor_archives": {"root": "relative/vendor"},
                },
            },
            "registry_path_not_absolute:adjustment_factors.vendor_archives.root",
        ),
    ],
)
def test_registry_rejects_malformed_shape_and_relative_paths(
    tmp_path,
    payload,
    reason,
):
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match=reason):
        ExternalMarketDataRegistry.load(registry_path)


def test_registry_wraps_malformed_json_with_safe_named_error(tmp_path):
    registry_path = tmp_path / "registry.json"
    registry_path.write_text("{not-json", encoding="utf-8")

    with pytest.raises(ValueError, match="registry_json_invalid") as exc_info:
        ExternalMarketDataRegistry.load(registry_path)

    assert "not-json" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_daily_kline_is_aggregated_and_qfq_adjusted_without_extracting_zip(tmp_path):
    registry = _registry(tmp_path)
    minute_root = registry.minute_root
    factor_root = registry.tushare_factor_root
    header = ["时间", "代码", "名称", "开盘价", "收盘价", "最高价", "最低价", "成交量", "成交额", "涨幅", "振幅"]
    _write_minute_archive(
        minute_root,
        period="1",
        year=2025,
        member="sz000725_2025.csv",
        rows=[
            header,
            ["2025-01-02 09:30:00", "sz000725", "京东方Ａ", 10, 11, 12, 9, 100, 1000, 0, 0],
            ["2025-01-02 09:31:00", "sz000725", "京东方Ａ", 11, 12, 13, 10, 200, 2200, 0, 0],
            ["2025-01-03 09:30:00", "sz000725", "京东方Ａ", 6, 7, 8, 5, 300, 2100, 0, 0],
        ],
    )
    _write_csv(
        factor_root / "000725.SZ.csv",
        [
            ["股票代码", "交易日期", "复权因子"],
            ["000725.SZ", "20250102", "1.0"],
            ["000725.SZ", "20250103", "2.0"],
        ],
    )

    result = await OfflineMinuteDataSource(registry).fetch_kline(
        "000725",
        period="day",
        count=2,
        adjustment="qfq",
        as_of="2025-01-03",
    )

    assert result["status"] == "ok"
    assert result["source"] == "offline_minute_archive"
    assert result["shadow_only"] is True
    assert result["adjustment"] == "qfq"
    assert result["data_cutoff"] == "2025-01-03 09:30:00"
    assert result["bars"] == [
        {
            "date": "2025-01-02",
            "open": 5.0,
            "close": 6.0,
            "high": 6.5,
            "low": 4.5,
            "volume": 300.0,
            "amount": 3200.0,
            "change_pct": 0.0,
        },
        {
            "date": "2025-01-03",
            "open": 6.0,
            "close": 7.0,
            "high": 8.0,
            "low": 5.0,
            "volume": 300.0,
            "amount": 2100.0,
            "change_pct": pytest.approx(16.6667, abs=0.0001),
        },
    ]


@pytest.mark.asyncio
async def test_minute_kline_reads_requested_period_and_returns_tail(tmp_path):
    registry = _registry(tmp_path)
    header = ["时间", "代码", "名称", "开盘价", "收盘价", "最高价", "最低价", "成交量", "成交额", "涨幅", "振幅"]
    _write_minute_archive(
        registry.minute_root,
        period="5",
        year=2025,
        member="sh600000_2025.csv",
        rows=[
            header,
            ["2025-01-02 09:35:00", "sh600000", "浦发银行", 10, 10.1, 10.2, 9.9, 100, 1000, 0, 0],
            ["2025-01-02 09:40:00", "sh600000", "浦发银行", 10.1, 10.2, 10.3, 10, 200, 2000, 0, 0],
            ["2025-01-02 09:45:00", "sh600000", "浦发银行", 10.2, 10.3, 10.4, 10.1, 300, 3000, 0, 0],
        ],
    )
    _write_csv(
        registry.tushare_factor_root / "600000.SH.csv",
        [
            ["股票代码", "交易日期", "复权因子"],
            ["600000.SH", "20250102", "1.0"],
        ],
    )

    result = await OfflineMinuteDataSource(registry).fetch_kline(
        "600000",
        period="5",
        count=2,
        adjustment="none",
    )

    assert [bar["date"] for bar in result["bars"]] == [
        "2025-01-02 09:40:00",
        "2025-01-02 09:45:00",
    ]
    assert result["bars"][-1]["close"] == 10.3


@pytest.mark.asyncio
async def test_minute_kline_normalizes_vendor_slash_timestamps(tmp_path):
    registry = _registry(tmp_path)
    header = ["时间", "代码", "名称", "开盘价", "收盘价", "最高价", "最低价", "成交量", "成交额", "涨幅", "振幅"]
    _write_minute_archive(
        registry.minute_root,
        period="1",
        year=2026,
        member="sz000725_2026.csv",
        rows=[
            header,
            ["2026/07/03 15:00", "sz000725", "京东方Ａ", 8.38, 8.38, 8.38, 8.38, 100, 838, 0, 0],
        ],
    )
    _write_csv(
        registry.tushare_factor_root / "000725.SZ.csv",
        [
            ["股票代码", "交易日期", "复权因子"],
            ["000725.SZ", "20260703", "5.0"],
        ],
    )

    result = await OfflineMinuteDataSource(registry).fetch_kline(
        "000725",
        period="day",
        count=1,
        adjustment="qfq",
    )

    assert result["status"] == "ok"
    assert result["data_cutoff"] == "2026-07-03 15:00:00"
    assert result["bars"][0]["date"] == "2026-07-03"


@pytest.mark.asyncio
async def test_qfq_fails_closed_when_a_requested_trade_date_has_no_factor(tmp_path):
    registry = _registry(tmp_path)
    header = ["时间", "代码", "名称", "开盘价", "收盘价", "最高价", "最低价", "成交量", "成交额", "涨幅", "振幅"]
    _write_minute_archive(
        registry.minute_root,
        period="1",
        year=2025,
        member="sz000725_2025.csv",
        rows=[
            header,
            ["2025-01-02 09:30:00", "sz000725", "京东方Ａ", 10, 11, 12, 9, 100, 1000, 0, 0],
        ],
    )
    _write_csv(
        registry.tushare_factor_root / "000725.SZ.csv",
        [
            ["股票代码", "交易日期", "复权因子"],
            ["000725.SZ", "20250103", "2.0"],
        ],
    )

    result = await OfflineMinuteDataSource(registry).fetch_kline(
        "000725",
        period="day",
        count=1,
        adjustment="qfq",
    )

    assert result["status"] == "error"
    assert result["reason"] == "missing_adjustment_factor"
    assert result["missing_factor_dates"] == ["20250102"]
    assert result["bars"] == []


@pytest.mark.asyncio
async def test_bse_920_code_routes_to_beijing_archive_and_factor(tmp_path):
    registry = _registry(tmp_path)
    header = ["时间", "代码", "名称", "开盘价", "收盘价", "最高价", "最低价", "成交量", "成交额", "涨幅", "振幅"]
    _write_minute_archive(
        registry.minute_root,
        period="1",
        year=2026,
        member="bj920001_2026.csv",
        rows=[
            header,
            ["2026-07-01 15:00:00", "bj920001", "北交测试", 10, 10.2, 10.3, 9.9, 100, 1020, 0, 0],
        ],
        market_dir="A股_分时数据_京市",
    )
    _write_csv(
        registry.tushare_factor_root / "920001.BJ.csv",
        [
            ["股票代码", "交易日期", "复权因子"],
            ["920001.BJ", "20260701", "1.0"],
        ],
    )

    result = await OfflineMinuteDataSource(registry).fetch_kline(
        "920001.BJ",
        period="day",
        count=1,
        adjustment="qfq",
    )

    assert result["status"] == "ok"
    assert result["code"] == "920001"
    assert result["bars"][0]["close"] == 10.2


@pytest.mark.asyncio
async def test_invalid_nine_prefix_does_not_route_to_beijing_archive(tmp_path):
    registry = _registry(tmp_path)

    result = await OfflineMinuteDataSource(registry).fetch_kline(
        "900001",
        period="day",
        count=1,
        adjustment="none",
    )

    assert result["status"] == "error"
    assert result["reason"] == "invalid_stock_code"


@pytest.mark.asyncio
async def test_as_of_timestamp_excludes_later_rows_on_same_day(tmp_path):
    registry = _registry(tmp_path)
    header = ["时间", "代码", "名称", "开盘价", "收盘价", "最高价", "最低价", "成交量", "成交额", "涨幅", "振幅"]
    _write_minute_archive(
        registry.minute_root,
        period="5",
        year=2026,
        member="sz000725_2026.csv",
        rows=[
            header,
            ["2026-07-03 09:35:00", "sz000725", "京东方Ａ", 8, 8.1, 8.2, 7.9, 100, 810, 0, 0],
            ["2026-07-03 09:40:00", "sz000725", "京东方Ａ", 8.1, 8.2, 8.3, 8, 100, 820, 0, 0],
            ["2026-07-03 09:45:00", "sz000725", "京东方Ａ", 8.2, 8.3, 8.4, 8.1, 100, 830, 0, 0],
        ],
    )

    result = await OfflineMinuteDataSource(registry).fetch_kline(
        "000725",
        period="5",
        count=10,
        adjustment="none",
        as_of="2026-07-03T09:40:00",
    )

    assert result["status"] == "ok"
    assert [bar["date"] for bar in result["bars"]] == [
        "2026-07-03 09:35:00",
        "2026-07-03 09:40:00",
    ]
    assert result["data_cutoff"] == "2026-07-03 09:40:00"


@pytest.mark.asyncio
async def test_zip_member_matching_is_case_insensitive_and_never_extracts(tmp_path, monkeypatch):
    registry = _registry(tmp_path)
    header = ["时间", "代码", "名称", "开盘价", "收盘价", "最高价", "最低价", "成交量", "成交额", "涨幅", "振幅"]
    _write_minute_archive(
        registry.minute_root,
        period="1",
        year=2026,
        member="SZ000725_2026.CSV",
        rows=[
            header,
            ["2026-07-03 15:00:00", "sz000725", "京东方Ａ", 8, 8.1, 8.2, 7.9, 100, 810, 0, 0],
        ],
    )
    monkeypatch.setattr(
        zipfile.ZipFile,
        "extract",
        lambda *_args, **_kwargs: pytest.fail("must not extract ZIP members"),
    )
    monkeypatch.setattr(
        zipfile.ZipFile,
        "extractall",
        lambda *_args, **_kwargs: pytest.fail("must not extract ZIP archives"),
    )

    result = await OfflineMinuteDataSource(registry).fetch_kline(
        "000725",
        period="day",
        count=1,
        adjustment="none",
    )

    assert result["status"] == "ok"
    assert result["bars"][0]["close"] == 8.1


@pytest.mark.asyncio
async def test_ambiguous_or_nested_zip_members_fail_closed(tmp_path):
    registry = _registry(tmp_path)
    archive = (
        registry.minute_root
        / "A股_分时数据_沪深"
        / "1分钟_按年汇总"
        / "2026_1min.zip"
    )
    archive.parent.mkdir(parents=True, exist_ok=True)
    payload = "时间,代码,名称,开盘价,收盘价,最高价,最低价,成交量,成交额,涨幅,振幅\n2026-07-03 15:00:00,sz000725,京东方Ａ,8,8.1,8.2,7.9,100,810,0,0\n"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr("SZ000725_2026.CSV", payload)
        bundle.writestr("sz000725_2026.csv", payload)
        bundle.writestr("../sz000725_2026.csv", payload)

    result = await OfflineMinuteDataSource(registry).fetch_kline(
        "000725",
        period="day",
        count=1,
        adjustment="none",
    )

    assert result["status"] == "error"
    assert result["reason"] == "archive_member_ambiguous"
    assert result["bars"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_value", ["nan", "inf", "-inf", ""])
async def test_nonfinite_or_missing_numeric_row_fails_closed(tmp_path, bad_value):
    registry = _registry(tmp_path)
    header = ["时间", "代码", "名称", "开盘价", "收盘价", "最高价", "最低价", "成交量", "成交额", "涨幅", "振幅"]
    _write_minute_archive(
        registry.minute_root,
        period="1",
        year=2026,
        member="sz000725_2026.csv",
        rows=[
            header,
            ["2026-07-03 14:59:00", "sz000725", "京东方Ａ", 8, 8.1, 8.2, 7.9, 100, 810, 0, 0],
            ["2026-07-03 15:00:00", "sz000725", "京东方Ａ", 8, bad_value, 8.2, 7.9, 100, 810, 0, 0],
        ],
    )

    result = await OfflineMinuteDataSource(registry).fetch_kline(
        "000725",
        period="day",
        count=1,
        adjustment="none",
    )

    assert result["status"] == "error"
    assert result["reason"] == "invalid_numeric_row"
    assert result["bars"] == []
    assert all(
        math.isfinite(value)
        for bar in result["bars"]
        for value in bar.values()
        if isinstance(value, float)
    )


@pytest.mark.asyncio
async def test_duplicate_timestamps_fail_closed_instead_of_double_counting(tmp_path):
    registry = _registry(tmp_path)
    header = ["时间", "代码", "名称", "开盘价", "收盘价", "最高价", "最低价", "成交量", "成交额", "涨幅", "振幅"]
    _write_minute_archive(
        registry.minute_root,
        period="1",
        year=2026,
        member="sz000725_2026.csv",
        rows=[
            header,
            ["2026-07-03 15:00:00", "sz000725", "京东方Ａ", 8, 8.1, 8.2, 7.9, 100, 810, 0, 0],
            ["2026-07-03 15:00:00", "sz000725", "京东方Ａ", 8, 8.1, 8.2, 7.9, 100, 810, 0, 0],
        ],
    )

    result = await OfflineMinuteDataSource(registry).fetch_kline(
        "000725",
        period="day",
        count=1,
        adjustment="none",
    )

    assert result["status"] == "error"
    assert result["reason"] == "duplicate_timestamp"
    assert result["bars"] == []


@pytest.mark.asyncio
async def test_qfq_reference_factor_never_uses_factor_after_as_of(tmp_path):
    registry = _registry(tmp_path)
    header = ["时间", "代码", "名称", "开盘价", "收盘价", "最高价", "最低价", "成交量", "成交额", "涨幅", "振幅"]
    _write_minute_archive(
        registry.minute_root,
        period="1",
        year=2025,
        member="sz000725_2025.csv",
        rows=[
            header,
            ["2025-01-02 15:00:00", "sz000725", "京东方Ａ", 10, 10, 10, 10, 100, 1000, 0, 0],
        ],
    )
    _write_csv(
        registry.tushare_factor_root / "000725.SZ.csv",
        [
            ["股票代码", "交易日期", "复权因子"],
            ["000725.SZ", "20250102", "1.0"],
            ["000725.SZ", "20250103", "2.0"],
            ["000725.SZ", "20250104", "100.0"],
        ],
    )

    result = await OfflineMinuteDataSource(registry).fetch_kline(
        "000725",
        period="day",
        count=1,
        adjustment="qfq",
        as_of="2025-01-03",
    )

    assert result["status"] == "ok"
    assert result["bars"][0]["close"] == 5.0


@pytest.mark.asyncio
@pytest.mark.parametrize("factor", ["nan", "inf", "-inf", "0", "-1", ""])
async def test_qfq_rejects_nonfinite_or_nonpositive_factor(tmp_path, factor):
    registry = _registry(tmp_path)
    header = ["时间", "代码", "名称", "开盘价", "收盘价", "最高价", "最低价", "成交量", "成交额", "涨幅", "振幅"]
    _write_minute_archive(
        registry.minute_root,
        period="1",
        year=2025,
        member="sz000725_2025.csv",
        rows=[
            header,
            ["2025-01-02 15:00:00", "sz000725", "京东方Ａ", 10, 10, 10, 10, 100, 1000, 0, 0],
        ],
    )
    _write_csv(
        registry.tushare_factor_root / "000725.SZ.csv",
        [
            ["股票代码", "交易日期", "复权因子"],
            ["000725.SZ", "20250102", factor],
        ],
    )

    result = await OfflineMinuteDataSource(registry).fetch_kline(
        "000725",
        period="day",
        count=1,
        adjustment="qfq",
    )

    assert result["status"] == "error"
    assert result["reason"] == "invalid_adjustment_factor"
    assert result["bars"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("factor_code", "reason"),
    [
        ("000001.SZ", "adjustment_factor_code_mismatch"),
        ("", "adjustment_factor_code_invalid"),
        ("not-a-code", "adjustment_factor_code_invalid"),
    ],
)
async def test_qfq_rejects_invalid_or_mismatched_factor_code(
    tmp_path,
    factor_code,
    reason,
):
    registry = _registry(tmp_path)
    header = ["时间", "代码", "名称", "开盘价", "收盘价", "最高价", "最低价", "成交量", "成交额", "涨幅", "振幅"]
    _write_minute_archive(
        registry.minute_root,
        period="1",
        year=2025,
        member="sz000725_2025.csv",
        rows=[
            header,
            ["2025-01-02 15:00:00", "sz000725", "京东方Ａ", 10, 10, 10, 10, 100, 1000, 0, 0],
        ],
    )
    _write_csv(
        registry.tushare_factor_root / "000725.SZ.csv",
        [
            ["股票代码", "交易日期", "复权因子"],
            [factor_code, "20250102", "1.0"],
        ],
    )

    result = await OfflineMinuteDataSource(registry).fetch_kline(
        "000725",
        period="day",
        count=1,
        adjustment="qfq",
    )

    assert result["status"] == "error"
    assert result["reason"] == reason
    assert result["bars"] == []


@pytest.mark.asyncio
async def test_qfq_rejects_duplicate_factor_trade_date(tmp_path):
    registry = _registry(tmp_path)
    header = ["时间", "代码", "名称", "开盘价", "收盘价", "最高价", "最低价", "成交量", "成交额", "涨幅", "振幅"]
    _write_minute_archive(
        registry.minute_root,
        period="1",
        year=2025,
        member="sz000725_2025.csv",
        rows=[
            header,
            ["2025-01-02 15:00:00", "sz000725", "京东方Ａ", 10, 10, 10, 10, 100, 1000, 0, 0],
        ],
    )
    _write_csv(
        registry.tushare_factor_root / "000725.SZ.csv",
        [
            ["股票代码", "交易日期", "复权因子"],
            ["000725.SZ", "20250102", "1.0"],
            ["000725.SZ", "20250102", "1.0"],
        ],
    )

    result = await OfflineMinuteDataSource(registry).fetch_kline(
        "000725",
        period="day",
        count=1,
        adjustment="qfq",
    )

    assert result["status"] == "error"
    assert result["reason"] == "duplicate_adjustment_factor_date"
    assert result["bars"] == []


@pytest.mark.asyncio
async def test_qfq_rejects_nonfinite_adjusted_output(tmp_path):
    registry = _registry(tmp_path)
    header = ["时间", "代码", "名称", "开盘价", "收盘价", "最高价", "最低价", "成交量", "成交额", "涨幅", "振幅"]
    _write_minute_archive(
        registry.minute_root,
        period="1",
        year=2025,
        member="sz000725_2025.csv",
        rows=[
            header,
            ["2025-01-02 15:00:00", "sz000725", "京东方Ａ", 10, 10, 10, 10, 100, 1000, 0, 0],
            ["2025-01-03 15:00:00", "sz000725", "京东方Ａ", 10, 10, 10, 10, 100, 1000, 0, 0],
        ],
    )
    _write_csv(
        registry.tushare_factor_root / "000725.SZ.csv",
        [
            ["股票代码", "交易日期", "复权因子"],
            ["000725.SZ", "20250102", "1e308"],
            ["000725.SZ", "20250103", "1e-308"],
        ],
    )

    result = await OfflineMinuteDataSource(registry).fetch_kline(
        "000725",
        period="day",
        count=2,
        adjustment="qfq",
    )

    assert result["status"] == "error"
    assert result["reason"] == "adjusted_price_not_finite"
    assert result["bars"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("row_code", "reason"),
    [
        ("000001", "minute_row_code_mismatch"),
        ("", "minute_row_code_invalid"),
        ("not-a-code", "minute_row_code_invalid"),
    ],
)
async def test_minute_row_code_must_match_requested_member(
    tmp_path,
    row_code,
    reason,
):
    registry = _registry(tmp_path)
    header = ["时间", "代码", "名称", "开盘价", "收盘价", "最高价", "最低价", "成交量", "成交额", "涨幅", "振幅"]
    _write_minute_archive(
        registry.minute_root,
        period="1",
        year=2026,
        member="sz000725_2026.csv",
        rows=[
            header,
            ["2026-07-03 15:00:00", row_code, "错误行", 8, 8.1, 8.2, 7.9, 100, 810, 0, 0],
        ],
    )

    result = await OfflineMinuteDataSource(registry).fetch_kline(
        "000725",
        period="day",
        count=1,
        adjustment="none",
    )

    assert result["status"] == "error"
    assert result["reason"] == reason
    assert result["bars"] == []


@pytest.mark.asyncio
async def test_zip_member_size_limit_fails_closed_before_open(tmp_path, monkeypatch):
    registry = _registry(tmp_path)
    header = ["时间", "代码", "名称", "开盘价", "收盘价", "最高价", "最低价", "成交量", "成交额", "涨幅", "振幅"]
    _write_minute_archive(
        registry.minute_root,
        period="1",
        year=2026,
        member="sz000725_2026.csv",
        rows=[
            header,
            ["2026-07-03 15:00:00", "sz000725", "京东方Ａ", 8, 8.1, 8.2, 7.9, 100, 810, 0, 0],
        ],
    )
    monkeypatch.setattr(offline_market_data, "MAX_ZIP_MEMBER_BYTES", 16)

    result = await OfflineMinuteDataSource(registry).fetch_kline(
        "000725",
        period="day",
        count=1,
        adjustment="none",
    )

    assert result["status"] == "error"
    assert result["reason"] == "archive_member_too_large"
    assert result["bars"] == []


@pytest.mark.asyncio
async def test_zip_compression_ratio_limit_fails_closed_before_open(
    tmp_path,
    monkeypatch,
):
    registry = _registry(tmp_path)
    header = ["时间", "代码", "名称", "开盘价", "收盘价", "最高价", "最低价", "成交量", "成交额", "涨幅", "振幅"]
    _write_minute_archive(
        registry.minute_root,
        period="1",
        year=2026,
        member="sz000725_2026.csv",
        rows=[
            header,
            ["2026-07-03 15:00:00", "sz000725", "京东方Ａ", 8, 8.1, 8.2, 7.9, 100, 810, 0, 0],
        ],
    )
    monkeypatch.setattr(offline_market_data, "MAX_ZIP_COMPRESSION_RATIO", 0.5)

    result = await OfflineMinuteDataSource(registry).fetch_kline(
        "000725",
        period="day",
        count=1,
        adjustment="none",
    )

    assert result["status"] == "error"
    assert result["reason"] == "archive_member_compression_ratio_exceeded"
    assert result["bars"] == []


@pytest.mark.parametrize(
    ("flag_bits", "unix_mode", "reason"),
    [
        (0x1, stat.S_IFREG | 0o600, "archive_member_encrypted"),
        (0, stat.S_IFLNK | 0o777, "archive_member_non_regular"),
    ],
)
def test_zip_rejects_encrypted_or_nonregular_member(
    flag_bits,
    unix_mode,
    reason,
):
    info = zipfile.ZipInfo("sz000725_2026.csv")
    info.file_size = 100
    info.compress_size = 50
    info.flag_bits = flag_bits
    info.external_attr = unix_mode << 16

    with pytest.raises(OfflineArchiveDataError, match=reason):
        offline_market_data._validate_zip_member_info(info)


@pytest.mark.asyncio
async def test_zip_row_limit_fails_closed(tmp_path, monkeypatch):
    registry = _registry(tmp_path)
    header = ["时间", "代码", "名称", "开盘价", "收盘价", "最高价", "最低价", "成交量", "成交额", "涨幅", "振幅"]
    _write_minute_archive(
        registry.minute_root,
        period="1",
        year=2026,
        member="sz000725_2026.csv",
        rows=[
            header,
            ["2026-07-03 14:59:00", "sz000725", "京东方Ａ", 8, 8.1, 8.2, 7.9, 100, 810, 0, 0],
            ["2026-07-03 15:00:00", "sz000725", "京东方Ａ", 8.1, 8.2, 8.3, 8, 100, 820, 0, 0],
        ],
    )
    monkeypatch.setattr(offline_market_data, "MAX_CSV_ROWS", 1)

    result = await OfflineMinuteDataSource(registry).fetch_kline(
        "000725",
        period="day",
        count=1,
        adjustment="none",
    )

    assert result["status"] == "error"
    assert result["reason"] == "archive_row_limit_exceeded"
    assert result["bars"] == []


@pytest.mark.asyncio
async def test_aware_row_and_as_of_are_converted_to_shanghai_timezone(tmp_path):
    registry = _registry(tmp_path)
    header = ["时间", "代码", "名称", "开盘价", "收盘价", "最高价", "最低价", "成交量", "成交额", "涨幅", "振幅"]
    _write_minute_archive(
        registry.minute_root,
        period="5",
        year=2026,
        member="sz000725_2026.csv",
        rows=[
            header,
            ["2026-07-03T01:35:00+00:00", "sz000725", "京东方Ａ", 8, 8.1, 8.2, 7.9, 100, 810, 0, 0],
            ["2026-07-03T01:45:00+00:00", "sz000725", "京东方Ａ", 8.1, 8.2, 8.3, 8, 100, 820, 0, 0],
        ],
    )

    result = await OfflineMinuteDataSource(registry).fetch_kline(
        "000725",
        period="5",
        count=10,
        adjustment="none",
        as_of="2026-07-03T01:40:00+00:00",
    )

    assert result["status"] == "ok"
    assert [bar["date"] for bar in result["bars"]] == [
        "2026-07-03 09:35:00"
    ]
    assert result["data_cutoff"] == "2026-07-03 09:35:00"


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_as_of", ["not-a-timestamp", "", 0])
async def test_invalid_as_of_returns_named_safe_error(tmp_path, invalid_as_of):
    registry = _registry(tmp_path)
    registry.minute_root.mkdir(parents=True)

    result = await OfflineMinuteDataSource(registry).fetch_kline(
        "000725",
        period="day",
        count=1,
        adjustment="none",
        as_of=invalid_as_of,
    )

    assert result["status"] == "error"
    assert result["reason"] == "invalid_as_of"
    if invalid_as_of == "not-a-timestamp":
        assert invalid_as_of not in json.dumps(result, ensure_ascii=False)


@pytest.mark.asyncio
async def test_invalid_archive_timestamp_returns_named_safe_error(tmp_path):
    registry = _registry(tmp_path)
    header = ["时间", "代码", "名称", "开盘价", "收盘价", "最高价", "最低价", "成交量", "成交额", "涨幅", "振幅"]
    _write_minute_archive(
        registry.minute_root,
        period="1",
        year=2026,
        member="sz000725_2026.csv",
        rows=[
            header,
            ["not-a-timestamp", "sz000725", "京东方Ａ", 8, 8.1, 8.2, 7.9, 100, 810, 0, 0],
        ],
    )

    result = await OfflineMinuteDataSource(registry).fetch_kline(
        "000725",
        period="day",
        count=1,
        adjustment="none",
    )

    assert result["status"] == "error"
    assert result["reason"] == "invalid_timestamp"
    assert "not-a-timestamp" not in json.dumps(result, ensure_ascii=False)


def _valid_offline_response() -> dict:
    return {
        "status": "ok",
        "data_cutoff": "2026-07-03 15:00:00",
        "bars": [
            {
                "date": "2026-07-02",
                "open": 8.0,
                "close": 8.1,
                "high": 8.2,
                "low": 7.9,
                "volume": 100.0,
                "amount": 810.0,
            },
            {
                "date": "2026-07-03",
                "open": 8.1,
                "close": 8.2,
                "high": 8.3,
                "low": 8.0,
                "volume": 110.0,
                "amount": 902.0,
            },
        ],
    }


def test_offline_response_validator_accepts_complete_chronological_bars():
    response = _valid_offline_response()

    bars, error = offline_market_data.validate_offline_kline_response(
        response,
        as_of="2026-07-03",
        minimum_bars=2,
    )

    assert error is None
    assert bars == response["bars"]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload["bars"][0].pop("amount"),
        lambda payload: payload["bars"][0].update(close=float("nan")),
        lambda payload: payload["bars"][0].update(volume=-1),
        lambda payload: payload["bars"][0].update(high=7.0),
        lambda payload: payload.update(bars="not-a-list"),
    ],
)
def test_offline_response_validator_rejects_malformed_bar_values(mutate):
    response = _valid_offline_response()
    mutate(response)

    bars, error = offline_market_data.validate_offline_kline_response(
        response,
        as_of="2026-07-03",
    )

    assert bars == []
    assert error == "offline_history_invalid"


@pytest.mark.parametrize(
    "dates",
    [
        ["2026-07-02", "2026-07-02"],
        ["2026-07-03", "2026-07-02"],
        ["not-a-date", "2026-07-03"],
    ],
)
def test_offline_response_validator_rejects_duplicate_or_unordered_dates(dates):
    response = _valid_offline_response()
    for bar, bar_date in zip(response["bars"], dates, strict=True):
        bar["date"] = bar_date

    bars, error = offline_market_data.validate_offline_kline_response(
        response,
        as_of="2026-07-03",
    )

    assert bars == []
    assert error == "offline_history_invalid"


@pytest.mark.parametrize(
    ("data_cutoff", "as_of"),
    [
        ("not-a-date", "2026-07-03"),
        ("2026-07-04 09:30:00", "2026-07-03"),
        ("2026-07-02 09:30:00", "2026-07-03"),
    ],
)
def test_offline_response_validator_enforces_data_cutoff(data_cutoff, as_of):
    response = _valid_offline_response()
    response["data_cutoff"] = data_cutoff

    bars, error = offline_market_data.validate_offline_kline_response(
        response,
        as_of=as_of,
    )

    assert bars == []
    assert error == "offline_history_invalid"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stock_code", "member", "row_code", "market_dir"),
    [
        ("600000", "sh600000.csv", "sh600000", "A股_分时数据_沪深"),
        ("000725", "sz000725.csv", "sz000725", "A股_分时数据_沪深"),
        ("920001", "bj920001.csv", "bj920001", "A股_分时数据_京市"),
    ],
)
async def test_current_daily_zip_layout_supports_sh_sz_and_bj(
    tmp_path,
    stock_code,
    member,
    row_code,
    market_dir,
):
    registry = _registry(tmp_path)
    header = ["时间", "代码", "名称", "开盘价", "收盘价", "最高价", "最低价", "成交量", "成交额", "涨幅", "振幅"]
    _write_daily_archive(
        registry.minute_root,
        period="1",
        day="2026-07-06",
        member=member,
        rows=[
            header,
            ["2026-07-06 09:30:00", row_code, "真实日包形态", 10, 10.1, 10.2, 9.9, 100, 1010, 0, 0],
        ],
        market_dir=market_dir,
    )

    result = await OfflineMinuteDataSource(registry).fetch_kline(
        stock_code,
        period="1",
        count=1,
        adjustment="none",
        as_of="2026-07-06",
    )

    assert result["status"] == "ok"
    assert result["bars"][0]["date"] == "2026-07-06 09:30:00"
    assert result["data_cutoff"] == "2026-07-06 09:30:00"


@pytest.mark.asyncio
async def test_annual_and_daily_archives_merge_with_identical_overlap(tmp_path):
    registry = _registry(tmp_path)
    header = ["时间", "代码", "名称", "开盘价", "收盘价", "最高价", "最低价", "成交量", "成交额", "涨幅", "振幅"]
    shared = ["2026-07-06 09:30:00", "sz000725", "京东方Ａ", 8, 8.1, 8.2, 7.9, 100, 810, 0, 0]
    _write_minute_archive(
        registry.minute_root,
        period="1",
        year=2026,
        member="sz000725_2026.csv",
        rows=[
            header,
            ["2026-07-03 15:00:00", "sz000725", "京东方Ａ", 7.9, 8, 8.1, 7.8, 100, 800, 0, 0],
            shared,
        ],
    )
    _write_daily_archive(
        registry.minute_root,
        period="1",
        day="2026-07-06",
        member="sz000725.csv",
        rows=[
            header,
            shared,
            ["2026-07-06 09:31:00", "sz000725", "京东方Ａ", 8.1, 8.2, 8.3, 8, 110, 902, 0, 0],
        ],
    )

    result = await OfflineMinuteDataSource(registry).fetch_kline(
        "000725",
        period="1",
        count=4,
        adjustment="none",
        as_of="2026-07-06",
    )

    assert result["status"] == "ok"
    assert [bar["date"] for bar in result["bars"]] == [
        "2026-07-03 15:00:00",
        "2026-07-06 09:30:00",
        "2026-07-06 09:31:00",
    ]
    assert result["archive_count"] == 2


@pytest.mark.asyncio
async def test_conflicting_annual_and_daily_overlap_fails_closed(tmp_path):
    registry = _registry(tmp_path)
    header = ["时间", "代码", "名称", "开盘价", "收盘价", "最高价", "最低价", "成交量", "成交额", "涨幅", "振幅"]
    _write_minute_archive(
        registry.minute_root,
        period="1",
        year=2026,
        member="sz000725_2026.csv",
        rows=[
            header,
            ["2026-07-06 09:30:00", "sz000725", "京东方Ａ", 8, 8.1, 8.2, 7.9, 100, 810, 0, 0],
        ],
    )
    _write_daily_archive(
        registry.minute_root,
        period="1",
        day="2026-07-06",
        member="sz000725.csv",
        rows=[
            header,
            ["2026-07-06 09:30:00", "sz000725", "京东方Ａ", 8, 8.15, 8.2, 7.9, 100, 815, 0, 0],
        ],
    )

    result = await OfflineMinuteDataSource(registry).fetch_kline(
        "000725",
        period="1",
        count=1,
        adjustment="none",
        as_of="2026-07-06",
    )

    assert result["status"] == "error"
    assert result["reason"] == "conflicting_overlap_timestamp"
    assert result["bars"] == []


@pytest.mark.asyncio
async def test_annual_member_retains_only_bounded_minute_tail(tmp_path):
    registry = _registry(tmp_path)
    header = ["时间", "代码", "名称", "开盘价", "收盘价", "最高价", "最低价", "成交量", "成交额", "涨幅", "振幅"]
    start = datetime(2026, 1, 1, 9, 30)
    rows = [header]
    for index in range(1000):
        observed_at = start + timedelta(minutes=index)
        price = 8 + index / 10000
        rows.append(
            [
                observed_at.isoformat(sep=" "),
                "sz000725",
                "京东方Ａ",
                price,
                price,
                price + 0.01,
                price - 0.01,
                100,
                800,
                0,
                0,
            ]
        )
    _write_minute_archive(
        registry.minute_root,
        period="1",
        year=2026,
        member="sz000725_2026.csv",
        rows=rows,
    )

    result = await OfflineMinuteDataSource(registry).fetch_kline(
        "000725",
        period="1",
        count=5,
        adjustment="none",
        as_of="2026-12-31",
    )

    assert result["status"] == "ok"
    assert len(result["bars"]) == 5
    assert result["retained_row_peak"] <= 10
