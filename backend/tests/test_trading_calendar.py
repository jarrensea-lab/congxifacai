from datetime import date

import pytest

from app.utils.trading_calendar import is_trading_day, prev_trading_day


@pytest.mark.parametrize(
    "holiday",
    [
        date(2026, 1, 1),
        date(2026, 1, 2),
        date(2026, 2, 16),
        date(2026, 2, 17),
        date(2026, 2, 18),
        date(2026, 2, 19),
        date(2026, 2, 20),
        date(2026, 2, 23),
        date(2026, 4, 6),
        date(2026, 5, 1),
        date(2026, 5, 4),
        date(2026, 5, 5),
        date(2026, 6, 19),
        date(2026, 9, 25),
        date(2026, 10, 1),
        date(2026, 10, 2),
        date(2026, 10, 5),
        date(2026, 10, 6),
        date(2026, 10, 7),
    ],
)
def test_official_sse_2026_weekday_closures_are_not_trading_days(holiday):
    assert is_trading_day(holiday) is False


@pytest.mark.parametrize(
    ("first_session_after_holiday", "expected_previous_session"),
    [
        (date(2026, 4, 7), date(2026, 4, 3)),
        (date(2026, 5, 6), date(2026, 4, 30)),
        (date(2026, 10, 8), date(2026, 9, 30)),
    ],
)
def test_previous_trading_day_skips_official_2026_holiday_blocks(
    first_session_after_holiday,
    expected_previous_session,
):
    assert prev_trading_day(first_session_after_holiday) == expected_previous_session


def test_fresh_close_accepts_pre_holiday_close_for_next_service_session():
    from scripts.daily_report import _apply_position_quote

    position = {
        "shares": 100,
        "total_cost": 800.0,
        "current_price": 8.0,
        "current_value": 800.0,
        "pnl": 0.0,
        "pnl_pct": 0.0,
    }
    applied = _apply_position_quote(position, {
        "price": 7.5,
        "source": "tencent",
        "quote_timestamp": "2026-04-03T15:00:00+08:00",
        "trading_date": "2026-04-03",
        "freshness": "valid_close",
    }, service_date="2026-04-07")

    assert applied is True
    assert position["quote_freshness"] == "fresh_close"
    assert position["current_price"] == 7.5
