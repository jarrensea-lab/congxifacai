"""Portfolio state synchronization and affordability constraints."""
import json
from concurrent.futures import ThreadPoolExecutor

import pytest


@pytest.mark.parametrize(
    ("overrides", "error_fragment"),
    [
        ({"side": "hold"}, "side"),
        ({"shares": 0}, "shares"),
        ({"shares": -1}, "shares"),
        ({"shares": 1.5}, "shares"),
        ({"shares": True}, "shares"),
        ({"price": 0}, "price"),
        ({"price": -1}, "price"),
        ({"price": float("nan")}, "price"),
        ({"price": float("inf")}, "price"),
        ({"fees": -1}, "fees"),
        ({"fees": float("nan")}, "fees"),
        ({"fees": float("inf")}, "fees"),
        ({"fees": True}, "fees"),
    ],
)
def test_apply_trade_rejects_invalid_input_without_any_file_mutation(
    tmp_path,
    overrides,
    error_fragment,
):
    from app.services.portfolio_store import apply_trade_to_user_portfolio

    portfolio_path = tmp_path / "user_portfolio.json"
    original = json.dumps({"positions": [], "available_cash": 1000.0})
    portfolio_path.write_text(original, encoding="utf-8")
    params = {
        "side": "buy",
        "code": "002131",
        "name": "利欧股份",
        "shares": 100,
        "price": 4.0,
        "fees": None,
    }
    params.update(overrides)

    result = apply_trade_to_user_portfolio(str(portfolio_path), **params)

    assert result["ok"] is False
    assert error_fragment in result["error"]
    assert portfolio_path.read_text(encoding="utf-8") == original


def test_atomic_portfolio_write_interruption_preserves_original_file(tmp_path, monkeypatch):
    from app.services import portfolio_store

    portfolio_path = tmp_path / "user_portfolio.json"
    original = json.dumps({"positions": [], "available_cash": 1000.0})
    portfolio_path.write_text(original, encoding="utf-8")

    def fail_replace(*args, **kwargs):
        raise OSError("simulated replace interruption")

    monkeypatch.setattr(portfolio_store.os, "replace", fail_replace)
    with pytest.raises(OSError, match="replace interruption"):
        portfolio_store.save_user_portfolio(
            {"positions": [], "available_cash": 900.0},
            str(portfolio_path),
        )

    assert portfolio_path.read_text(encoding="utf-8") == original
    assert list(tmp_path.glob("*.tmp")) == []


def test_merge_report_market_snapshot_preserves_concurrent_trade_truth(tmp_path):
    from app.services.portfolio_store import merge_report_market_snapshot

    portfolio_path = tmp_path / "user_portfolio.json"
    portfolio_path.write_text(
        json.dumps(
            {
                "available_cash": 600.0,
                "cash": 600.0,
                "positions": [
                    {
                        "code": "002131",
                        "name": "利欧股份",
                        "shares": 300,
                        "avg_cost": 4.1,
                        "current_price": 4.0,
                    }
                ],
                "trade_events": [{"fill_id": "concurrent-fill"}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    stale_report_snapshot = {
        "available_cash": 1000.0,
        "cash": 1000.0,
        "positions": [
            {
                "code": "002131",
                "name": "利欧股份",
                "shares": 200,
                "avg_cost": 3.995,
                "current_price": 4.2,
                "quote_status": "valid",
                "quote_freshness": "fresh",
            }
        ],
        "trade_events": [],
    }

    merged = merge_report_market_snapshot(stale_report_snapshot, str(portfolio_path))

    persisted = json.loads(portfolio_path.read_text(encoding="utf-8"))
    assert merged == persisted
    assert persisted["available_cash"] == 600.0
    assert persisted["trade_events"] == [{"fill_id": "concurrent-fill"}]
    assert persisted["positions"][0]["shares"] == 300
    assert persisted["positions"][0]["avg_cost"] == 4.1
    assert persisted["positions"][0]["current_price"] == 4.2
    assert persisted["positions"][0]["quote_status"] == "valid"


def test_portfolio_writer_rejects_nan_without_replacing_original(tmp_path):
    from app.services.portfolio_store import save_user_portfolio

    portfolio_path = tmp_path / "user_portfolio.json"
    original = json.dumps({"positions": [], "available_cash": 1000.0})
    portfolio_path.write_text(original, encoding="utf-8")

    with pytest.raises(ValueError):
        save_user_portfolio(
            {"positions": [], "available_cash": float("nan")},
            str(portfolio_path),
        )

    assert portfolio_path.read_text(encoding="utf-8") == original


def test_cas_restore_refuses_to_overwrite_newer_portfolio(tmp_path):
    from app.services.portfolio_store import (
        portfolio_fingerprint,
        restore_user_portfolio_if_unchanged,
    )

    portfolio_path = tmp_path / "user_portfolio.json"
    before = {"positions": [], "available_cash": 1000.0}
    our_after = {"positions": [], "available_cash": 600.0}
    newer = {"positions": [], "available_cash": 800.0}
    portfolio_path.write_text(json.dumps(newer), encoding="utf-8")

    result = restore_user_portfolio_if_unchanged(
        str(portfolio_path),
        expected_fingerprint=portfolio_fingerprint(our_after),
        replacement=before,
    )

    assert result["ok"] is False
    assert result["conflict"] is True
    assert json.loads(portfolio_path.read_text(encoding="utf-8")) == newer


def test_sync_db_from_user_portfolio_replaces_stale_position(tmp_path):
    from app.database import SessionLocal
    from app.models import Position
    from app.services.portfolio_store import sync_db_from_user_portfolio

    portfolio_path = tmp_path / "user_portfolio.json"
    portfolio_path.write_text(json.dumps({
        "positions": [{
            "code": "000100",
            "name": "TCL科技",
            "shares": 100,
            "avg_cost": 4.984,
            "total_cost": 498.4,
            "current_price": 4.8,
            "current_value": 480.0,
            "pnl": -18.4,
        }],
        "available_cash": 1544.89,
        "total_value": 480.0,
    }), encoding="utf-8")

    db = SessionLocal()
    try:
        db.add(Position(
            stock_code="000100",
            stock_name="TCL科技",
            quantity=300,
            avg_cost=502,
            market_price=486,
            market_value=145800,
        ))
        db.commit()

        result = sync_db_from_user_portfolio(db, str(portfolio_path))

        pos = db.query(Position).filter(Position.stock_code == "000100").first()
        assert result["positions_synced"] == 1
        assert result["total_assets"] == 2024.89
        assert pos.quantity == 100
        assert pos.avg_cost == 498
        assert pos.market_price == 480
        assert pos.market_value == 48000
    finally:
        db.query(Position).delete()
        db.commit()
        db.close()


def test_sync_db_from_user_portfolio_counts_new_position_in_assets(tmp_path):
    from app.database import SessionLocal
    from app.models import Position
    from app.services.portfolio_store import sync_db_from_user_portfolio

    portfolio_path = tmp_path / "user_portfolio.json"
    portfolio_path.write_text(json.dumps({
        "positions": [{
            "code": "600839",
            "name": "四川长虹",
            "shares": 100,
            "avg_cost": 6.701,
            "current_price": 6.54,
        }],
        "available_cash": 4704.51,
    }), encoding="utf-8")

    db = SessionLocal()
    try:
        db.query(Position).delete()
        db.commit()

        result = sync_db_from_user_portfolio(db, str(portfolio_path))

        assert result["positions_synced"] == 1
        assert result["total_assets"] == 5358.51
    finally:
        db.query(Position).delete()
        db.commit()
        db.close()


def test_recalculate_portfolio_updates_cash_and_total_assets():
    from app.services.portfolio_store import recalculate_portfolio

    portfolio = recalculate_portfolio({
        "available_cash": 4704.51,
        "cash": 6085.61,
        "positions": [{
            "code": "600839",
            "shares": 100,
            "avg_cost": 6.701,
            "current_price": 6.54,
        }],
    })

    assert portfolio["cash"] == 4704.51
    assert portfolio["total_value"] == 654.0
    assert portfolio["total_assets"] == 5358.51


def test_recalculate_portfolio_includes_frozen_cash_in_total_assets():
    """Catches frozen broker cash disappearing from account equity."""
    from app.services.portfolio_store import recalculate_portfolio

    portfolio = recalculate_portfolio(
        {
            "available_cash": 1000.0,
            "frozen_cash": 100.0,
            "positions": [
                {
                    "code": "000001",
                    "shares": 100,
                    "avg_cost": 9.0,
                    "current_price": 10.0,
                }
            ],
        }
    )

    assert portfolio["total_value"] == 1000.0
    assert portfolio["total_assets"] == 2100.0


def test_sync_db_from_empty_user_portfolio_clears_positions_and_reports_assets(tmp_path):
    from app.database import SessionLocal
    from app.models import Position
    from app.services.portfolio_store import sync_db_from_user_portfolio

    portfolio_path = tmp_path / "user_portfolio.json"
    portfolio_path.write_text(json.dumps({
        "positions": [],
        "available_cash": 3085.61,
        "total_value": 0,
    }), encoding="utf-8")

    db = SessionLocal()
    try:
        db.add(Position(
            stock_code="000100",
            stock_name="TCL科技",
            quantity=100,
            avg_cost=498,
            market_price=534,
            market_value=53400,
        ))
        db.commit()

        result = sync_db_from_user_portfolio(db, str(portfolio_path))

        stale = db.query(Position).filter(Position.stock_code == "000100").first()
        assert result["positions_synced"] == 0
        assert result["available_cash"] == 3085.61
        assert result["total_assets"] == 3085.61
        assert stale.quantity == 0
        assert stale.market_value == 0
    finally:
        db.query(Position).delete()
        db.commit()
        db.close()


def test_apply_sell_to_user_portfolio_updates_json_position(tmp_path):
    from app.services.portfolio_store import apply_trade_to_user_portfolio

    portfolio_path = tmp_path / "user_portfolio.json"
    portfolio_path.write_text(json.dumps({
        "positions": [{
            "code": "000100",
            "name": "TCL科技",
            "shares": 300,
            "avg_cost": 4.984,
            "total_cost": 1495.2,
            "current_price": 4.85,
            "current_value": 1455.0,
            "pnl": -40.2,
        }],
        "available_cash": 1544.89,
        "realized_pnl": 0,
    }), encoding="utf-8")

    result = apply_trade_to_user_portfolio(
        str(portfolio_path),
        side="sell",
        code="000100",
        name="TCL科技",
        shares=200,
        price=4.8,
        trade_date="2026-06-26",
    )

    updated = json.loads(portfolio_path.read_text(encoding="utf-8"))
    pos = updated["positions"][0]
    assert result["ok"] is True
    assert pos["shares"] == 100
    assert round(pos["total_cost"], 2) == 498.4
    assert round(updated["available_cash"], 2) == 2504.89


def test_apply_trade_persists_fill_metadata_in_event_and_position_history(tmp_path):
    from app.services.portfolio_store import apply_trade_to_user_portfolio

    portfolio_path = tmp_path / "user_portfolio.json"
    portfolio_path.write_text(json.dumps({
        "positions": [],
        "available_cash": 2103.25,
        "realized_pnl": 0,
    }), encoding="utf-8")

    result = apply_trade_to_user_portfolio(
        str(portfolio_path),
        "buy",
        "002131",
        "利欧股份",
        200,
        3.995,
        trade_date="2026-07-20",
        fill_id="fill_user_lio_20260720",
        source="user_confirmed_chat",
        occurred_at="2026-07-20T10:15:00+08:00",
        recommendation_id="rec_lio_20260720",
        signal_id="signal_lio_breakout",
        fees=None,
    )

    updated = json.loads(portfolio_path.read_text(encoding="utf-8"))
    history = updated["positions"][0]["trade_history"][0]
    event = updated["trade_events"][0]

    assert result["ok"] is True
    assert result["duplicate"] is False
    assert result["fill"] == event
    assert event["fill_id"] == "fill_user_lio_20260720"
    assert event["source"] == "user_confirmed_chat"
    assert event["occurred_at"] == "2026-07-20T10:15:00+08:00"
    assert event["recommendation_id"] == "rec_lio_20260720"
    assert event["signal_id"] == "signal_lio_breakout"
    assert event["fees"] is None
    assert event["fee_status"] == "pending"
    for key in ("fill_id", "source", "occurred_at", "recommendation_id", "signal_id", "fees", "fee_status"):
        assert history[key] == event[key]


def test_apply_trade_with_duplicate_fill_id_is_idempotent(tmp_path):
    from app.services.portfolio_store import apply_trade_to_user_portfolio

    portfolio_path = tmp_path / "user_portfolio.json"
    portfolio_path.write_text(json.dumps({
        "positions": [],
        "available_cash": 1000.0,
        "realized_pnl": 0,
    }), encoding="utf-8")
    kwargs = {
        "trade_date": "2026-07-20",
        "fill_id": "fill-idempotent-1",
        "source": "test",
        "occurred_at": "2026-07-20T10:15:00+08:00",
    }

    first = apply_trade_to_user_portfolio(
        str(portfolio_path), "buy", "002131", "利欧股份", 100, 4.0, **kwargs,
    )
    before_duplicate = portfolio_path.read_text(encoding="utf-8")
    duplicate = apply_trade_to_user_portfolio(
        str(portfolio_path), "buy", "002131", "利欧股份", 100, 4.0, **kwargs,
    )
    after_duplicate = portfolio_path.read_text(encoding="utf-8")

    assert first["duplicate"] is False
    assert duplicate["ok"] is True
    assert duplicate["duplicate"] is True
    assert duplicate["fill"] == first["fill"]
    assert after_duplicate == before_duplicate
    persisted = json.loads(after_duplicate)
    assert persisted["positions"][0]["shares"] == 100
    assert persisted["available_cash"] == 600.0
    assert len(persisted["positions"][0]["trade_history"]) == 1
    assert len(persisted["trade_events"]) == 1


def test_same_fill_id_with_conflicting_payload_is_rejected_without_mutation(tmp_path):
    from app.services.portfolio_store import apply_trade_to_user_portfolio

    portfolio_path = tmp_path / "user_portfolio.json"
    portfolio_path.write_text(json.dumps({
        "positions": [],
        "available_cash": 1000.0,
    }), encoding="utf-8")
    first = apply_trade_to_user_portfolio(
        str(portfolio_path), "buy", "002131", "利欧股份", 100, 4.0,
        fill_id="fill-conflict", source="feishu_bridge", source_event_id="msg-1",
    )
    before_conflict = portfolio_path.read_text(encoding="utf-8")

    same = apply_trade_to_user_portfolio(
        str(portfolio_path), "buy", "002131", "利欧股份", 100, 4.0,
        fill_id="fill-conflict", source="feishu_bridge", source_event_id="msg-1",
    )
    conflict = apply_trade_to_user_portfolio(
        str(portfolio_path), "sell", "002131", "利欧股份", 50, 4.2,
        fill_id="fill-conflict", source="feishu_bridge", source_event_id="msg-1",
    )

    assert first["duplicate"] is False
    assert same["duplicate"] is True
    assert conflict["ok"] is False
    assert conflict["conflict"] is True
    assert portfolio_path.read_text(encoding="utf-8") == before_conflict
    assert first["fill"]["payload_fingerprint"]


def test_dirty_trade_event_fails_closed_without_crashing_or_mutating(tmp_path):
    from app.services.portfolio_store import apply_trade_to_user_portfolio

    portfolio_path = tmp_path / "user_portfolio.json"
    original = json.dumps({
        "positions": [],
        "available_cash": 1000.0,
        "trade_events": [None],
    })
    portfolio_path.write_text(original, encoding="utf-8")

    result = apply_trade_to_user_portfolio(
        str(portfolio_path), "buy", "002131", "利欧股份", 100, 4.0,
        fill_id="fill-new",
    )

    assert result["ok"] is False
    assert "trade_events" in result["error"]
    assert portfolio_path.read_text(encoding="utf-8") == original


def test_apply_trade_rejects_oversell_without_any_mutation(tmp_path):
    from app.services.portfolio_store import apply_trade_to_user_portfolio

    portfolio_path = tmp_path / "user_portfolio.json"
    original = json.dumps({
        "positions": [{
            "code": "002131",
            "name": "利欧股份",
            "shares": 100,
            "avg_cost": 4.0,
            "current_price": 4.0,
            "trade_history": [],
        }],
        "available_cash": 600.0,
        "realized_pnl": 0,
    })
    portfolio_path.write_text(original, encoding="utf-8")

    result = apply_trade_to_user_portfolio(
        str(portfolio_path), "sell", "002131", "利欧股份", 101, 4.2,
        fill_id="fill-oversell",
    )

    assert result["ok"] is False
    assert "exceeds held shares" in result["error"]
    assert portfolio_path.read_text(encoding="utf-8") == original


def test_apply_trade_numeric_fees_adjust_buy_cash_sell_proceeds_and_realized_pnl(tmp_path):
    from app.services.portfolio_store import apply_trade_to_user_portfolio

    portfolio_path = tmp_path / "user_portfolio.json"
    portfolio_path.write_text(json.dumps({
        "positions": [],
        "available_cash": 2000.0,
        "realized_pnl": 0,
    }), encoding="utf-8")

    buy = apply_trade_to_user_portfolio(
        str(portfolio_path), "buy", "002131", "利欧股份", 100, 4.0,
        fill_id="fill-fee-buy", fees=1.25,
    )
    after_buy = json.loads(portfolio_path.read_text(encoding="utf-8"))
    sell = apply_trade_to_user_portfolio(
        str(portfolio_path), "sell", "002131", "利欧股份", 100, 4.5,
        fill_id="fill-fee-sell", fees=1.75,
    )
    after_sell = json.loads(portfolio_path.read_text(encoding="utf-8"))

    assert after_buy["available_cash"] == 1598.75
    assert after_buy["total_assets"] == 1998.75
    assert after_buy["positions"][0]["avg_cost"] == 4.0125
    assert after_buy["positions"][0]["total_cost"] == 401.25
    assert buy["fill"]["cash_effect"] == -401.25
    assert buy["fill"]["fee_status"] == "known"
    assert after_sell["available_cash"] == 2047.0
    assert after_sell["realized_pnl"] == 47.0
    assert after_sell["total_assets"] == 2047.0
    assert sell["fill"]["cash_effect"] == 448.25
    assert sell["fill"]["shares"] == 100
    assert len(after_sell["trade_events"]) == 2
    assert after_sell["closed_positions"][0]["trade_history"][-1]["fill_id"] == "fill-fee-sell"


def test_apply_trade_partial_sell_allocates_buy_fee_through_average_cost(tmp_path):
    from app.services.portfolio_store import apply_trade_to_user_portfolio

    portfolio_path = tmp_path / "user_portfolio.json"
    portfolio_path.write_text(json.dumps({
        "positions": [],
        "available_cash": 2000.0,
        "realized_pnl": 0,
    }), encoding="utf-8")

    apply_trade_to_user_portfolio(
        str(portfolio_path), "buy", "002131", "利欧股份", 100, 4.0,
        fill_id="fill-partial-buy", fees=1.25,
    )
    apply_trade_to_user_portfolio(
        str(portfolio_path), "sell", "002131", "利欧股份", 40, 4.5,
        fill_id="fill-partial-sell", fees=0.75,
    )

    updated = json.loads(portfolio_path.read_text(encoding="utf-8"))
    position = updated["positions"][0]
    assert position["shares"] == 60
    assert position["avg_cost"] == 4.0125
    assert position["total_cost"] == 240.75
    assert updated["realized_pnl"] == 18.75


def test_apply_trade_legacy_caller_without_fill_metadata_remains_supported(tmp_path):
    from app.services.portfolio_store import apply_trade_to_user_portfolio

    portfolio_path = tmp_path / "user_portfolio.json"
    portfolio_path.write_text(json.dumps({
        "positions": [],
        "available_cash": 1000.0,
    }), encoding="utf-8")

    result = apply_trade_to_user_portfolio(
        str(portfolio_path), "buy", "000100", "TCL科技", 100, 4.5, "2026-07-20",
    )

    updated = json.loads(portfolio_path.read_text(encoding="utf-8"))
    assert result["ok"] is True
    assert result["duplicate"] is False
    assert result["fill"]["fill_id"] is None
    assert updated["positions"][0]["shares"] == 100
    assert "trade_events" not in updated


def test_bot_buy_supplies_unique_fill_metadata_without_changing_command_contract(tmp_path, monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.database import Base
    from app.models import SimAccount
    from app.services import bot_commands

    portfolio_path = tmp_path / "user_portfolio.json"
    portfolio_path.write_text(json.dumps({
        "positions": [],
        "available_cash": 100000.0,
    }), encoding="utf-8")
    monkeypatch.setenv("CONGXI_PORTFOLIO_PATH", str(portfolio_path))

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    db = Session(engine)
    db.add(SimAccount())
    db.commit()
    captured = []

    def fake_apply(*args, **kwargs):
        captured.append((args, kwargs))
        return {
            "ok": True,
            "duplicate": False,
            "fill": {"fill_id": kwargs["fill_id"]},
            "_portfolio_before": {"positions": [], "available_cash": 100000.0},
            "portfolio_fingerprint": "fake-after-fingerprint",
        }

    monkeypatch.setattr(bot_commands, "apply_trade_to_user_portfolio", fake_apply)
    try:
        result = bot_commands._execute_buy(db, "002131", "利欧股份", 100, 4.0)
        sell_result = bot_commands._execute_sell(db, "002131", "利欧股份", 100, 4.2)
    finally:
        db.close()
        engine.dispose()

    assert result["ok"] is True
    assert result["action"] == "buy"
    assert captured[0][1]["fill_id"]
    assert captured[0][1]["source"] == "bot_command"
    assert captured[0][1]["occurred_at"]
    assert sell_result["ok"] is True
    assert sell_result["action"] == "sell"
    assert captured[1][1]["fill_id"]
    assert captured[1][1]["fill_id"] != captured[0][1]["fill_id"]
    assert captured[1][1]["source"] == "bot_command"


@pytest.mark.parametrize("failure_mode", ["error_result", "exception"])
def test_bot_portfolio_failure_rolls_back_real_database_transaction(
    tmp_path,
    monkeypatch,
    failure_mode,
):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session, sessionmaker

    from app.database import Base
    from app.models import Position, SimAccount, TradeLog
    from app.services import bot_commands

    portfolio_path = tmp_path / "user_portfolio.json"
    original_portfolio = {
        "positions": [],
        "available_cash": 100000.0,
        "realized_pnl": 0,
    }
    portfolio_path.write_text(json.dumps(original_portfolio), encoding="utf-8")
    monkeypatch.setenv("CONGXI_PORTFOLIO_PATH", str(portfolio_path))

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine)
    setup_db = factory()
    setup_db.add(SimAccount())
    setup_db.commit()
    setup_db.close()
    monkeypatch.setattr(bot_commands, "SessionLocal", factory)
    if failure_mode == "error_result":
        def fail_portfolio(*args, **kwargs):
            return {"ok": False, "error": "portfolio write failed"}
    else:
        def fail_portfolio(*args, **kwargs):
            raise RuntimeError("portfolio write exception")
    monkeypatch.setattr(bot_commands, "apply_trade_to_user_portfolio", fail_portfolio)

    result = bot_commands.process_message(
        "买入002131(利欧股份) 100股，成本4.000",
        source_event_id="feishu-msg-failure",
        source="feishu_bridge",
    )

    verify_db: Session = factory()
    try:
        account = verify_db.query(SimAccount).first()
        assert result["ok"] is False
        assert "portfolio write" in result["error"]
        assert verify_db.query(Position).count() == 0
        assert verify_db.query(TradeLog).count() == 0
        assert account.cash == 10000000
    finally:
        verify_db.close()
        engine.dispose()
    assert json.loads(portfolio_path.read_text(encoding="utf-8")) == original_portfolio


def test_database_commit_failure_compensates_portfolio_fill(tmp_path, monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session, sessionmaker

    from app.database import Base
    from app.models import Position, SimAccount, TradeLog
    from app.services import bot_commands

    portfolio_path = tmp_path / "user_portfolio.json"
    original_portfolio = {
        "positions": [],
        "available_cash": 100000.0,
        "realized_pnl": 0,
    }
    portfolio_path.write_text(json.dumps(original_portfolio), encoding="utf-8")
    monkeypatch.setenv("CONGXI_PORTFOLIO_PATH", str(portfolio_path))

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    fail_commit = {"enabled": False}

    class CommitFailingSession(Session):
        def commit(self):
            if fail_commit["enabled"]:
                raise RuntimeError("database commit failed")
            return super().commit()

    factory = sessionmaker(bind=engine, class_=CommitFailingSession)
    setup_db = factory()
    setup_db.add(SimAccount())
    setup_db.commit()
    setup_db.close()
    fail_commit["enabled"] = True
    monkeypatch.setattr(bot_commands, "SessionLocal", factory)

    result = bot_commands.process_message(
        "买入002131(利欧股份) 100股，成本4.000",
        source_event_id="feishu-msg-commit-failure",
        source="feishu_bridge",
    )

    verify_db = Session(engine)
    try:
        account = verify_db.query(SimAccount).first()
        assert result["ok"] is False
        assert "database commit failed" in result["error"]
        assert verify_db.query(Position).count() == 0
        assert verify_db.query(TradeLog).count() == 0
        assert account.cash == 10000000
    finally:
        verify_db.close()
        engine.dispose()
    assert json.loads(portfolio_path.read_text(encoding="utf-8")) == original_portfolio


def test_portfolio_duplicate_detected_after_precheck_does_not_mutate_database(tmp_path, monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session, sessionmaker

    from app.database import Base
    from app.models import Position, SimAccount, TradeLog
    from app.services import bot_commands

    portfolio_path = tmp_path / "user_portfolio.json"
    portfolio_path.write_text(json.dumps({
        "positions": [],
        "available_cash": 100000.0,
        "realized_pnl": 0,
    }), encoding="utf-8")
    monkeypatch.setenv("CONGXI_PORTFOLIO_PATH", str(portfolio_path))

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine)
    setup_db = factory()
    setup_db.add(SimAccount())
    setup_db.commit()
    setup_db.close()
    monkeypatch.setattr(bot_commands, "SessionLocal", factory)
    monkeypatch.setattr(
        bot_commands,
        "apply_trade_to_user_portfolio",
        lambda *args, **kwargs: {
            "ok": True,
            "duplicate": True,
            "fill": {"fill_id": kwargs["fill_id"], "shares": 100, "price": 4.0},
        },
    )

    result = bot_commands.process_message(
        "买入002131(利欧股份) 100股，成本4.000",
        source_event_id="feishu-msg-racing-duplicate",
        source="feishu_bridge",
    )

    verify_db: Session = factory()
    try:
        account = verify_db.query(SimAccount).first()
        assert result["ok"] is True
        assert result["duplicate"] is True
        assert verify_db.query(Position).count() == 0
        assert verify_db.query(TradeLog).count() == 0
        assert account.cash == 10000000
    finally:
        verify_db.close()
        engine.dispose()


def test_same_feishu_message_retry_is_idempotent_in_portfolio_and_database(tmp_path, monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session, sessionmaker

    from app.database import Base
    from app.models import Position, SimAccount, TradeLog
    from app.services import bot_commands

    portfolio_path = tmp_path / "user_portfolio.json"
    portfolio_path.write_text(json.dumps({
        "positions": [],
        "available_cash": 100000.0,
        "realized_pnl": 0,
    }), encoding="utf-8")
    monkeypatch.setenv("CONGXI_PORTFOLIO_PATH", str(portfolio_path))

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine)
    setup_db = factory()
    setup_db.add(SimAccount())
    setup_db.commit()
    setup_db.close()
    monkeypatch.setattr(bot_commands, "SessionLocal", factory)

    first = bot_commands.process_message(
        "买入002131(利欧股份) 100股，成本4.000",
        source_event_id="feishu-msg-001",
        source="feishu_bridge",
    )
    second = bot_commands.process_message(
        "买入002131(利欧股份) 100股，成本4.000",
        source_event_id="feishu-msg-001",
        source="feishu_bridge",
    )

    verify_db: Session = factory()
    try:
        position = verify_db.query(Position).filter(Position.stock_code == "002131").one()
        account = verify_db.query(SimAccount).first()
        assert first["ok"] is True
        assert first["duplicate"] is False
        assert second["ok"] is True
        assert second["duplicate"] is True
        assert second["fill"]["fill_id"] == first["fill"]["fill_id"]
        assert position.quantity == 100
        assert verify_db.query(TradeLog).count() == 1
        assert account.cash == 10000000 - 40000
    finally:
        verify_db.close()
        engine.dispose()

    portfolio = json.loads(portfolio_path.read_text(encoding="utf-8"))
    assert portfolio["positions"][0]["shares"] == 100
    assert len(portfolio["trade_events"]) == 1
    assert portfolio["trade_events"][0]["source"] == "feishu_bridge"
    assert portfolio["trade_events"][0]["source_event_id"] == "feishu-msg-001"


def test_bot_3995_fill_keeps_database_and_json_cash_amounts_consistent(tmp_path, monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session, sessionmaker

    from app.database import Base
    from app.models import Position, SimAccount, TradeLog
    from app.services import bot_commands

    portfolio_path = tmp_path / "user_portfolio.json"
    portfolio_path.write_text(json.dumps({
        "positions": [],
        "available_cash": 100000.0,
        "realized_pnl": 0,
    }), encoding="utf-8")
    monkeypatch.setenv("CONGXI_PORTFOLIO_PATH", str(portfolio_path))

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine)
    setup_db = factory()
    setup_db.add(SimAccount())
    setup_db.commit()
    setup_db.close()
    monkeypatch.setattr(bot_commands, "SessionLocal", factory)

    result = bot_commands.process_message(
        "买入002131(利欧股份) 200股，成本3.995",
        source_event_id="feishu-msg-3995",
        source="feishu_bridge",
    )

    verify_db: Session = factory()
    try:
        account = verify_db.query(SimAccount).first()
        position = verify_db.query(Position).filter(Position.stock_code == "002131").one()
        trade = verify_db.query(TradeLog).one()
        assert result["ok"] is True
        assert account.cash == 10000000 - 79900
        assert position.total_buy_amount == 79900
        assert trade.amount == 79900
        assert position.avg_cost == 400
    finally:
        verify_db.close()
        engine.dispose()

    portfolio = json.loads(portfolio_path.read_text(encoding="utf-8"))
    assert portfolio["available_cash"] == 100000.0 - 799.0
    assert portfolio["positions"][0]["total_cost"] == 799.0
    assert round(account.cash / 100, 2) == portfolio["available_cash"]


def test_concurrent_same_source_event_serializes_to_one_database_and_json_fill(tmp_path, monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session, sessionmaker

    from app.database import Base
    from app.models import Position, SimAccount, TradeLog
    from app.services import bot_commands

    portfolio_path = tmp_path / "user_portfolio.json"
    portfolio_path.write_text(json.dumps({
        "positions": [],
        "available_cash": 100000.0,
        "realized_pnl": 0,
    }), encoding="utf-8")
    monkeypatch.setenv("CONGXI_PORTFOLIO_PATH", str(portfolio_path))
    database_path = tmp_path / "bot-concurrency.sqlite3"
    engine = create_engine(
        f"sqlite:///{database_path}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine)
    setup_db = factory()
    setup_db.add(SimAccount())
    setup_db.commit()
    setup_db.close()
    monkeypatch.setattr(bot_commands, "SessionLocal", factory)

    def run_message():
        return bot_commands.process_message(
            "买入002131(利欧股份) 200股，成本3.995",
            source_event_id="feishu-msg-concurrent",
            source="feishu_bridge",
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: run_message(), range(2)))

    verify_db: Session = factory()
    try:
        position = verify_db.query(Position).filter(Position.stock_code == "002131").one()
        account = verify_db.query(SimAccount).first()
        assert sorted(result["duplicate"] for result in results) == [False, True]
        assert position.quantity == 200
        assert position.total_buy_amount == 79900
        assert verify_db.query(TradeLog).count() == 1
        assert account.cash == 10000000 - 79900
    finally:
        verify_db.close()
        engine.dispose()

    portfolio = json.loads(portfolio_path.read_text(encoding="utf-8"))
    assert portfolio["positions"][0]["shares"] == 200
    assert len(portfolio["trade_events"]) == 1


def test_db_sell_allocates_exact_remaining_cost_basis_without_rounding_drift(tmp_path, monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session, sessionmaker

    from app.database import Base
    from app.models import Position, SimAccount, TradeLog
    from app.services import bot_commands

    portfolio_path = tmp_path / "user_portfolio.json"
    portfolio_path.write_text(json.dumps({
        "positions": [],
        "available_cash": 100000.0,
        "realized_pnl": 0,
    }), encoding="utf-8")
    monkeypatch.setenv("CONGXI_PORTFOLIO_PATH", str(portfolio_path))
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine)
    setup_db = factory()
    setup_db.add(SimAccount())
    setup_db.commit()
    setup_db.close()
    monkeypatch.setattr(bot_commands, "SessionLocal", factory)

    buy = bot_commands.process_message(
        "买入002131(利欧股份) 200股，成本3.995",
        source_event_id="cost-basis-buy-1",
        source="feishu_bridge",
    )
    partial_sell = bot_commands.process_message(
        "卖出002131(利欧股份) 100股，价格4.100",
        source_event_id="cost-basis-sell-1",
        source="feishu_bridge",
    )

    check_partial: Session = factory()
    try:
        partial_position = check_partial.query(Position).filter(Position.stock_code == "002131").one()
        partial_logs = check_partial.query(TradeLog).order_by(TradeLog.id).all()
        assert buy["ok"] is True
        assert partial_sell["ok"] is True
        assert partial_position.quantity == 100
        assert partial_position.total_buy_qty == 100
        assert partial_position.total_buy_amount == 39950
        assert partial_position.realized_pnl == 1050
        assert partial_logs[1].pnl == 1050
    finally:
        check_partial.close()

    partial_portfolio = json.loads(portfolio_path.read_text(encoding="utf-8"))
    assert partial_portfolio["realized_pnl"] == 10.5
    assert partial_portfolio["positions"][0]["total_cost"] == 399.5

    add = bot_commands.process_message(
        "买入002131(利欧股份) 100股，成本4.005",
        source_event_id="cost-basis-buy-2",
        source="feishu_bridge",
    )
    clear = bot_commands.process_message(
        "清仓002131(利欧股份)",
        source_event_id="cost-basis-clear",
        source="feishu_bridge",
    )

    verify_db: Session = factory()
    try:
        position = verify_db.query(Position).filter(Position.stock_code == "002131").one()
        account = verify_db.query(SimAccount).first()
        logs = verify_db.query(TradeLog).order_by(TradeLog.id).all()
        assert add["ok"] is True
        assert clear["ok"] is True
        assert position.quantity == 0
        assert position.total_buy_qty == 0
        assert position.total_buy_amount == 0
        assert position.realized_pnl == 1250
        assert [log.amount for log in logs] == [79900, 41000, 40050, 80200]
        assert account.cash == 10000000 + 1250
    finally:
        verify_db.close()
        engine.dispose()

    final_portfolio = json.loads(portfolio_path.read_text(encoding="utf-8"))
    assert final_portfolio["positions"] == []
    assert final_portfolio["realized_pnl"] == 12.5
    assert final_portfolio["available_cash"] == 100012.5


def test_db_sell_subtracts_known_fee_from_cash_and_exact_allocated_cost_pnl(tmp_path, monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.database import Base
    from app.models import Position, SimAccount, TradeLog
    from app.services import bot_commands

    portfolio_path = tmp_path / "user_portfolio.json"
    portfolio_path.write_text(json.dumps({
        "positions": [],
        "available_cash": 100000.0,
        "realized_pnl": 0,
    }), encoding="utf-8")
    monkeypatch.setenv("CONGXI_PORTFOLIO_PATH", str(portfolio_path))
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    db = Session(engine)
    db.add(SimAccount())
    db.commit()
    try:
        bot_commands._execute_buy(db, "002131", "利欧股份", 200, 3.995)
        db.commit()
        result = bot_commands._execute_sell(
            db,
            "002131",
            "利欧股份",
            100,
            4.1,
            fees=1.5,
        )
        db.commit()

        position = db.query(Position).filter(Position.stock_code == "002131").one()
        sell_log = db.query(TradeLog).filter(TradeLog.direction == "sell").one()
        account = db.query(SimAccount).first()
        assert result["pnl"] == 9.0
        assert position.total_buy_amount == 39950
        assert position.realized_pnl == 900
        assert sell_log.fee == 150
        assert sell_log.pnl == 900
        assert account.cash == 10000000 - 79900 + 41000 - 150
    finally:
        db.close()
        engine.dispose()

    portfolio = json.loads(portfolio_path.read_text(encoding="utf-8"))
    assert portfolio["realized_pnl"] == 9.0
    assert portfolio["available_cash"] == 100000.0 - 799.0 + 410.0 - 1.5


def test_same_source_event_buy_then_clear_is_payload_conflict_not_false_sell_success(tmp_path, monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session, sessionmaker

    from app.database import Base
    from app.models import Position, SimAccount, TradeLog
    from app.services import bot_commands

    portfolio_path = tmp_path / "user_portfolio.json"
    portfolio_path.write_text(json.dumps({
        "positions": [],
        "available_cash": 100000.0,
        "realized_pnl": 0,
    }), encoding="utf-8")
    monkeypatch.setenv("CONGXI_PORTFOLIO_PATH", str(portfolio_path))
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine)
    setup_db = factory()
    setup_db.add(SimAccount())
    setup_db.commit()
    setup_db.close()
    monkeypatch.setattr(bot_commands, "SessionLocal", factory)

    buy = bot_commands.process_message(
        "买入002131(利欧股份) 200股，成本3.995",
        source_event_id="same-event-buy-then-clear",
        source="feishu_bridge",
    )
    before_conflict = portfolio_path.read_text(encoding="utf-8")
    clear = bot_commands.process_message(
        "清仓002131(利欧股份)",
        source_event_id="same-event-buy-then-clear",
        source="feishu_bridge",
    )

    verify_db: Session = factory()
    try:
        position = verify_db.query(Position).filter(Position.stock_code == "002131").one()
        account = verify_db.query(SimAccount).first()
        assert buy["ok"] is True
        assert clear["ok"] is False
        assert "fill_id conflict" in clear["error"]
        assert position.quantity == 200
        assert position.total_buy_amount == 79900
        assert verify_db.query(TradeLog).count() == 1
        assert account.cash == 10000000 - 79900
    finally:
        verify_db.close()
        engine.dispose()
    assert portfolio_path.read_text(encoding="utf-8") == before_conflict


def test_apply_account_constraints_moves_unaffordable_new_stock_to_watchlist():
    from app.engine.workshop import _apply_account_constraints

    decision = {
        "short_term": {
            "recommendations": [
                {"code": "601318", "name": "中国平安", "buy_range": "42.50-43.00元", "reason": "稳健"},
                {"code": "000100", "name": "TCL科技", "buy_range": "4.70-4.85元", "reason": "已有持仓观察"},
            ]
        },
        "mid_low_freq": {"recommendations": []},
    }

    constrained = _apply_account_constraints(
        decision,
        available_cash=1544.89,
        holdings_codes={"000100"},
        total_assets=2024.89,
    )

    recs = constrained["short_term"]["recommendations"]
    assert [r["code"] for r in recs] == ["000100"]
    assert constrained["unaffordable_watchlist"][0]["code"] == "601318"
    assert constrained["account_constraints"]["available_cash"] == 1544.89


def test_apply_account_constraints_uses_profile_limits_and_board_lot_sizes():
    from app.engine.workshop import _apply_account_constraints

    profile = {
        "mode": "growth_sprint",
        "cash_reserve_pct": 10,
        "single_position_limit_pct": 50,
    }
    decision = {
        "short_term": {
            "recommendations": [
                {"code": "000001", "name": "平安银行", "buy_range": "10.00-10.20元"},
                {"code": "688008", "name": "澜起科技", "buy_range": "8.00-8.10元"},
            ]
        },
        "mid_low_freq": {"recommendations": []},
    }

    constrained = _apply_account_constraints(
        decision,
        available_cash=2000,
        total_assets=3000,
        strategy_profile=profile,
    )

    assert [item["code"] for item in constrained["stock_pool"]] == ["000001"]
    assert [item["code"] for item in constrained["unaffordable_watchlist"]] == ["688008"]
    assert "200股" in constrained["unaffordable_watchlist"][0]["reason_unaffordable"]
    assert constrained["account_constraints"] == {
        "profile_mode": "growth_sprint",
        "available_cash": 2000.0,
        "total_assets": 3000.0,
        "cash_reserve_pct": 10.0,
        "single_position_limit_pct": 50.0,
        "reserve_cash": 300.0,
        "executable_cash": 1500.0,
        "lot_size": None,
        "lot_size_by_code": {"000001": 100, "688008": 200},
    }
