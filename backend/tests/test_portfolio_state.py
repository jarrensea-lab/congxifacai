"""Portfolio state synchronization and affordability constraints."""
import json


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
        assert pos.quantity == 100
        assert pos.avg_cost == 498
        assert pos.market_price == 480
        assert pos.market_value == 48000
    finally:
        db.query(Position).delete()
        db.commit()
        db.close()


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
