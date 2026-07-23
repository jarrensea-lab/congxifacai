from app.services.quant_lifecycle import PositionWatchStore, TargetPoolStore


def test_reconcile_position_watch_adds_missing_real_holding(tmp_path):
    from app.services.profit_truth import reconcile_position_watch

    store = PositionWatchStore(tmp_path / "position_watch.json")

    result = reconcile_position_watch(
        {
            "positions": [
                {
                    "code": "002131",
                    "name": "利欧股份",
                    "shares": 200,
                    "avg_cost": 3.995,
                }
            ]
        },
        store,
    )

    plan = store.get("002131")
    assert result["healthy"] is True
    assert result["added_codes"] == ["002131"]
    assert result["unresolved_codes"] == []
    assert plan["source"] == "portfolio_reconciliation"
    assert plan["stop_loss_price"] == 3.6
    assert plan["target_price"] == 4.79


def test_reconcile_position_watch_preserves_complete_manual_plan(tmp_path):
    from app.services.profit_truth import reconcile_position_watch

    store = PositionWatchStore(tmp_path / "position_watch.json")
    store.upsert_plan(
        "600900",
        "长江电力",
        stop_loss_price=26.64,
        target_price=30.28,
        source="manual_user_confirmed",
    )

    result = reconcile_position_watch(
        {
            "positions": [
                {
                    "code": "600900",
                    "name": "长江电力",
                    "shares": 100,
                    "avg_cost": 28.04,
                }
            ]
        },
        store,
    )

    assert result["added_codes"] == []
    assert store.get("600900")["source"] == "manual_user_confirmed"
    assert store.get("600900")["stop_loss_price"] == 26.64


def test_reconcile_position_watch_fails_closed_without_cost_or_price(tmp_path):
    from app.services.profit_truth import reconcile_position_watch

    store = PositionWatchStore(tmp_path / "position_watch.json")

    result = reconcile_position_watch(
        {"positions": [{"code": "002131", "name": "利欧股份", "shares": 200}]},
        store,
    )

    assert result["healthy"] is False
    assert result["unresolved_codes"] == ["002131"]
    assert store.get("002131") is None


def test_reconcile_closed_loss_moves_watching_target_to_cooldown(tmp_path):
    from app.services.profit_truth import reconcile_closed_loss_cooldowns

    store = TargetPoolStore(tmp_path / "candidate_pool.json")
    store.upsert_target(code="000629", name="钒钛股份", status="watching", source="manual")

    result = reconcile_closed_loss_cooldowns(
        {
            "closed_positions": [
                {
                    "code": "000629",
                    "name": "钒钛股份",
                    "close_date": "2026-07-08",
                    "realized_pnl": -71.0,
                    "realized_pnl_pct": -9.99,
                }
            ]
        },
        store,
    )

    item = store.get("000629")
    assert result["cooled_codes"] == ["000629"]
    assert item["status"] == "cooldown_after_loss"
    assert item["source"] == "portfolio_closed_loss_reconciliation"
    assert item["loss_exit"]["realized_pnl"] == -71.0
    assert item["loss_exit"]["realized_pnl_pct"] == -9.99
    assert item["production_eligibility"]["eligible"] is False


def test_reconcile_closed_loss_does_not_cool_profitable_exit(tmp_path):
    from app.services.profit_truth import reconcile_closed_loss_cooldowns

    store = TargetPoolStore(tmp_path / "candidate_pool.json")
    store.upsert_target(code="000001", name="平安银行", status="watching", source="manual")

    result = reconcile_closed_loss_cooldowns(
        {
            "closed_positions": [
                {
                    "code": "000001",
                    "name": "平安银行",
                    "close_date": "2026-07-08",
                    "realized_pnl": 50.0,
                    "realized_pnl_pct": 3.0,
                }
            ]
        },
        store,
    )

    assert result["cooled_codes"] == []
    assert store.get("000001")["status"] == "watching"
