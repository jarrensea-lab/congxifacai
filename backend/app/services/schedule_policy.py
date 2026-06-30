"""Scheduling policy for report automation.

This module decides whether a scheduled wakeup should actually produce a report.
It keeps date logic separate from APScheduler cron expressions so Sunday evening
and holiday behavior can be tested without running the app.
"""
from __future__ import annotations

from datetime import date, time

from app.utils.trading_calendar import is_trading_day, next_trading_day

MAIN_REPORT_TIME = time(20, 30)
PREMARKET_CALIBRATION_TIME = time(8, 50)


def _today() -> date:
    return date.today()


def main_report_target_date(run_date: date | None = None) -> date:
    """Return the trading day served by a main report generated on run_date."""
    day = run_date or _today()
    return next_trading_day(day)


def should_run_main_report(
    run_date: date | None = None,
    run_time: time | None = None,
) -> bool:
    """Return whether the main next-day report should run for this wakeup."""
    day = run_date or _today()
    if run_time and (run_time.hour != MAIN_REPORT_TIME.hour or run_time.minute != MAIN_REPORT_TIME.minute):
        return False
    if is_trading_day(day):
        return True
    if day.weekday() == 6 and main_report_target_date(day).weekday() == 0:
        return True
    return False


def should_run_premarket_calibration(
    run_date: date | None = None,
    run_time: time | None = None,
) -> bool:
    """Return whether a premarket short calibration should run."""
    day = run_date or _today()
    if run_time and (
        run_time.hour != PREMARKET_CALIBRATION_TIME.hour
        or run_time.minute != PREMARKET_CALIBRATION_TIME.minute
    ):
        return False
    return is_trading_day(day)


def schedule_reason(job: str, run_date: date | None = None) -> str:
    """Explain why a job should run or skip on a given date."""
    day = run_date or _today()
    if job == "main_report":
        target = main_report_target_date(day)
        if is_trading_day(day):
            return f"交易日盘后主报告，服务下一交易日 {target.isoformat()}。"
        if day.weekday() == 6 and target.weekday() == 0:
            return f"周日晚主报告，服务周一交易日 {target.isoformat()}。"
        return f"非交易日前夜，跳过主报告；下一交易日为 {target.isoformat()}。"
    if job == "premarket_calibration":
        if is_trading_day(day):
            return f"交易日盘前短策略校准，服务 {day.isoformat()}。"
        return f"非交易日，跳过盘前短策略校准；下一交易日为 {main_report_target_date(day).isoformat()}。"
    return f"未知调度任务 {job}，无运行原因。"
