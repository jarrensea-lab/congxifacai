from app.services import small_account_discovery
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


def test_small_account_seed_candidates_preserve_cash_reserve():
    rows = build_small_account_seed_candidates(
        available_cash=1469.57,
        total_assets=6052.57,
    )

    assert rows[0]["max_entry_price"] == 8.64


def test_dynamic_small_account_candidates_rotate_from_live_fund_flow_rows():
    assert hasattr(small_account_discovery, "build_dynamic_small_account_candidates")
    rows = small_account_discovery.build_dynamic_small_account_candidates(
        market_rows=[
            {
                "code": "000563",
                "name": "陕国投A",
                "latest_price": "3.02",
                "change_pct": "5.96%",
                "turnover": "4.37%",
                "net": "1.25亿",
                "amount": "12.3亿",
            },
            {
                "code": "000597",
                "name": "东北制药",
                "latest_price": "4.80",
                "change_pct": "2.10%",
                "turnover": "5.07%",
                "net": "8000万",
                "amount": "6.2亿",
            },
            {
                "code": "000001",
                "name": "平安银行",
                "latest_price": "10.69",
                "change_pct": "1.00%",
                "turnover": "1.20%",
                "net": "2.00亿",
                "amount": "20亿",
            },
            {
                "code": "300149",
                "name": "睿智医药",
                "latest_price": "10.39",
                "change_pct": "19.98%",
                "turnover": "15.58%",
                "net": "8153.85万",
                "amount": "7.50亿",
            },
            {
                "code": "002422",
                "name": "科伦药业",
                "latest_price": "49.07",
                "change_pct": "3.00%",
                "turnover": "3.00%",
                "net": "3.00亿",
                "amount": "30亿",
            },
            {
                "code": "600000",
                "name": "浦发银行",
                "latest_price": "9.00",
                "change_pct": "1.00%",
                "turnover": "1.00%",
                "net": "-1000万",
                "amount": "5亿",
            },
            {
                "code": "000002",
                "name": "ST测试",
                "latest_price": "5.00",
                "change_pct": "1.00%",
                "turnover": "2.00%",
                "net": "5000万",
                "amount": "3亿",
            },
        ],
        available_cash=2103.25,
        total_assets=5975.25,
        existing_codes={"000001"},
        max_candidates=8,
    )

    assert [item["code"] for item in rows] == ["000563", "000597"]
    assert rows[0]["source"] == "dynamic_fund_flow_discovery"
    assert rows[0]["research_only"] is True
    assert rows[0]["max_entry_price"] == 15.05
    assert rows[0]["market_evidence"]["net_flow_yuan"] == 125_000_000
    assert "动态资金流" in rows[0]["watch_reason"]
