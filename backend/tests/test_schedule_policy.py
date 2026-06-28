"""Schedule policy regressions for report automation."""
from datetime import date, time

from app.services.schedule_policy import (
    main_report_target_date,
    schedule_reason,
    should_run_main_report,
    should_run_premarket_calibration,
)


def test_sunday_evening_main_report_targets_monday():
    assert should_run_main_report(date(2026, 6, 28), time(20, 30)) is True
    assert main_report_target_date(date(2026, 6, 28)).isoformat() == "2026-06-29"


def test_saturday_evening_does_not_run_main_report():
    assert should_run_main_report(date(2026, 6, 27), time(20, 30)) is False


def test_trading_day_evening_targets_next_trading_day():
    assert should_run_main_report(date(2026, 6, 29), time(20, 30)) is True
    assert main_report_target_date(date(2026, 6, 29)).isoformat() == "2026-06-30"


def test_premarket_calibration_only_runs_on_trading_day():
    assert should_run_premarket_calibration(date(2026, 6, 29), time(8, 50)) is True
    assert should_run_premarket_calibration(date(2026, 6, 28), time(8, 50)) is False


def test_schedule_reason_explains_sunday_main_report():
    reason = schedule_reason("main_report", date(2026, 6, 28))

    assert "周日晚" in reason
    assert "2026-06-29" in reason
