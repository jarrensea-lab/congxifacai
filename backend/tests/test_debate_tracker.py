from __future__ import annotations

import inspect
from datetime import datetime, timedelta

import pytest

from app.database import SessionLocal
from app.engine.debate_tracker import DebateTracker
from app.models import DebateResult


@pytest.fixture
def db_session():
    db = SessionLocal()
    db.query(DebateResult).delete()
    db.commit()
    try:
        yield db
    finally:
        db.rollback()
        db.close()


async def _fill_pending(db_session) -> int:
    """Exercise both the current sync bug and the intended async contract."""
    outcome = DebateTracker.fill_pending(db_session)
    if inspect.isawaitable(outcome):
        return await outcome
    return outcome


def _record(*, age_days: int, short_codes=None, mid_codes=None) -> DebateResult:
    return DebateResult(
        strategy_type="review",
        debated_at=datetime.now() - timedelta(days=age_days),
        judge_decision="buy",
        short_term_codes=short_codes or [],
        mid_term_codes=mid_codes or [],
    )


@pytest.mark.asyncio
async def test_fill_pending_keeps_failed_market_returns_unknown(monkeypatch, db_session):
    """Catches quote failures being persisted as false 0% outcomes."""

    async def fail_fetch_batch(self, codes):
        raise RuntimeError("quote unavailable")

    monkeypatch.setattr(
        "app.data_sources.tencent_client.TencentDataSource.fetch_batch",
        fail_fetch_batch,
    )
    record = _record(
        age_days=21,
        short_codes=["000001"],
        mid_codes=["600000"],
    )
    db_session.add(record)
    db_session.commit()
    record_id = record.id

    filled = await _fill_pending(db_session)
    db_session.expire_all()
    persisted = db_session.get(DebateResult, record_id)

    assert filled == 0
    assert persisted.short_term_return_5d is None
    assert persisted.mid_term_return_20d is None
    assert persisted.judge_direction_correct is None
    assert persisted.result_filled_at is None


@pytest.mark.asyncio
async def test_fill_pending_persists_due_short_return_before_mid_term_is_due(
    monkeypatch,
    db_session,
):
    """Catches a valid 5-day result being lost because the 20-day leg is pending."""

    async def fetch_batch(self, codes):
        return {
            "000001": {"change_pct": 2.0},
            "000002": {"change_pct": -1.0},
        }

    monkeypatch.setattr(
        "app.data_sources.tencent_client.TencentDataSource.fetch_batch",
        fetch_batch,
    )
    record = _record(
        age_days=6,
        short_codes=["000001", "000002"],
        mid_codes=["600000"],
    )
    db_session.add(record)
    db_session.commit()
    record_id = record.id

    filled = await _fill_pending(db_session)
    db_session.close()

    verify_db = SessionLocal()
    try:
        persisted = verify_db.get(DebateResult, record_id)
        assert filled == 0
        assert persisted.short_term_return_5d == pytest.approx(0.5)
        assert persisted.judge_direction_correct is True
        assert persisted.mid_term_return_20d is None
        assert persisted.result_filled_at is None
    finally:
        verify_db.close()


@pytest.mark.asyncio
async def test_fill_pending_completes_only_after_all_due_returns_succeed(
    monkeypatch,
    db_session,
):
    """Catches records being marked complete when one required return is missing."""

    async def fetch_batch(self, codes):
        values = {
            "000001": {"change_pct": 3.0},
            "000002": {"change_pct": 1.0},
            "600000": {"change_pct": -2.0},
        }
        return {code: values[code] for code in codes}

    monkeypatch.setattr(
        "app.data_sources.tencent_client.TencentDataSource.fetch_batch",
        fetch_batch,
    )
    record = _record(
        age_days=21,
        short_codes=["000001", "000002"],
        mid_codes=["600000"],
    )
    db_session.add(record)
    db_session.commit()
    record_id = record.id

    filled = await _fill_pending(db_session)
    db_session.expire_all()
    persisted = db_session.get(DebateResult, record_id)

    assert filled == 1
    assert persisted.short_term_return_5d == pytest.approx(2.0)
    assert persisted.mid_term_return_20d == pytest.approx(-2.0)
    assert persisted.judge_direction_correct is True
    assert persisted.result_filled_at is not None
