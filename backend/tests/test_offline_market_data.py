import csv
import json
import math
import zipfile
from pathlib import Path

import pytest

from app.data_sources.offline_market_data import (
    ExternalMarketDataRegistry,
    OfflineMinuteDataSource,
)


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
