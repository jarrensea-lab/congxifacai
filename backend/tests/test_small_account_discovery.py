from app.services.small_account_discovery import build_small_account_seed_candidates


def test_small_account_seed_candidates_exclude_existing_and_include_budget_price():
    rows = build_small_account_seed_candidates(
        available_cash=6085.61,
        total_assets=6085.61,
        existing_codes={"000629"},
    )

    codes = {item["code"] for item in rows}
    assert "000629" not in codes
    assert "000100" in codes
    first = rows[0]
    assert first["source"] == "small_account_discovery"
    assert first["lot_size"] == 100
    assert first["max_entry_price"] == 30.42
    assert "池外小账户补扫" in first["watch_reason"]
