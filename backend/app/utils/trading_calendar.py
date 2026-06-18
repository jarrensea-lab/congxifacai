"""交易日历工具 — A股交易日判断 (基础版)"""
from datetime import date, timedelta


# 2025-2026 中国 A 股主要节假日休市日期 (基于公开交易日历)
# 格式: YYYY-MM-DD
CN_HOLIDAYS: set[str] = {
    # 2025 元旦
    "2025-01-01",
    # 2025 春节 (1.28-2.4)
    "2025-01-28", "2025-01-29", "2025-01-30", "2025-01-31",
    "2025-02-01", "2025-02-02", "2025-02-03", "2025-02-04",
    # 2025 清明节
    "2025-04-04", "2025-04-05", "2025-04-06",
    # 2025 劳动节 (5.1-5.5)
    "2025-05-01", "2025-05-02", "2025-05-03", "2025-05-04", "2025-05-05",
    # 2025 端午节
    "2025-05-31", "2025-06-01", "2025-06-02",
    # 2025 中秋+国庆 (10.1-10.8)
    "2025-10-01", "2025-10-02", "2025-10-03", "2025-10-04",
    "2025-10-05", "2025-10-06", "2025-10-07", "2025-10-08",
    # 2026 元旦
    "2026-01-01", "2026-01-02", "2026-01-03",
    # 2026 春节 (约2.17-2.24)
    "2026-02-17", "2026-02-18", "2026-02-19", "2026-02-20",
    "2026-02-21", "2026-02-22", "2026-02-23",
}


def is_trading_day(d: date | None = None) -> bool:
    """判断是否为 A 股交易日。

    规则:
    1. 周六、周日为非交易日
    2. 在 CN_HOLIDAYS 中的日期为非交易日

    Args:
        d: 要检查的日期，默认为今天

    Returns:
        True 如果当天是交易日
    """
    if d is None:
        d = date.today()

    # 周末必休
    if d.weekday() >= 5:  # Saturday=5, Sunday=6
        return False

    # 节假日休市
    if d.isoformat() in CN_HOLIDAYS:
        return False

    return True


def next_trading_day(d: date | None = None) -> date:
    """获取下一个交易日（不包含当日）"""
    if d is None:
        d = date.today()
    next_day = d + timedelta(days=1)
    while not is_trading_day(next_day):
        next_day += timedelta(days=1)
    return next_day


def prev_trading_day(d: date | None = None) -> date:
    """获取上一个交易日（不包含当日）"""
    if d is None:
        d = date.today()
    prev_day = d - timedelta(days=1)
    while not is_trading_day(prev_day):
        prev_day -= timedelta(days=1)
    return prev_day
