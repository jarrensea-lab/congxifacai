from pathlib import Path

from app.services.market_state_store import MarketStateStore, evaluate_trade_eligibility


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _build_base_data(root: Path) -> Path:
    _write(
        root / "股票列表.csv",
        "\ufeffTS代码,股票代码,股票名称,地域,所属行业,股票全称,英文全称,拼音缩写,市场类型,交易所代码,交易货币,上市状态,上市日期,退市日期,沪深港通标的,实控人名称,实控人企业性质\n"
        "000001.SZ,000001,平安银行,深圳,银行,平安银行股份有限公司,,PAYH,主板,SZSE,CNY,上市,19910403,,深股通,无实际控制人,无\n"
        "000005.SZ,000005,ST星源(退),,,深圳世纪星源股份有限公司,,STXY,主板,SZSE,CNY,退市,19901210,20240426,否,丁芃,民营企业\n",
    )
    _write(
        root / "退市股票列表.csv",
        "\ufeffTS代码,股票代码,股票名称,地域,所属行业,股票全称,英文全称,拼音缩写,市场类型,交易所代码,交易货币,上市状态,上市日期,退市日期,沪深港通标的,实控人名称,实控人企业性质\n"
        "000005.SZ,000005,ST星源(退),,,深圳世纪星源股份有限公司,,STXY,主板,SZSE,CNY,退市,19901210,20240426,否,丁芃,民营企业\n",
    )
    _write(
        root / "交易日历.csv",
        "\ufeff交易所,日期,是否交易,上一个交易日\n"
        "SSE,2024-04-25,交易,2024-04-24\n"
        "SSE,2024-04-26,交易,2024-04-25\n"
        "SSE,2024-04-27,休市,2024-04-26\n",
    )
    _write(
        root / "ST股票列表_每日更新" / "2024-04" / "ST股票列表_20240426.csv",
        "\ufeff股票代码,股票名称,交易日期,ST类型,ST类型名称\n"
        "000005.SZ,ST星源,20240426,ST,风险警示板\n",
    )
    _write(
        root / "停复牌_每日更新" / "2024-04" / "每日停复牌_20240426.csv",
        "\ufeff股票代码,交易日期,停复牌时间段,停复牌类型\n"
        "000005.SZ,20240426,,停牌\n"
        "000001.SZ,20240426,,复牌\n",
    )
    _write(
        root / "涨跌停价格_每日更新" / "2024-04" / "每日涨跌停价格_20240426.csv",
        "\ufeff股票代码,交易日期,昨日收盘价,涨停价,跌停价\n"
        "000001.SZ,20240426,10.00,11.00,9.00\n"
        "000005.SZ,20240426,1.00,1.05,0.95\n",
    )
    return root


def test_market_state_store_returns_daily_trade_state(tmp_path):
    store = MarketStateStore(_build_base_data(tmp_path))

    state = store.get_trade_state("000001", "2024-04-26", price=10.98)

    assert state["code"] == "000001.SZ"
    assert state["name"] == "平安银行"
    assert state["date"] == "2024-04-26"
    assert state["is_trading_day"] is True
    assert state["listed"] is True
    assert state["delisted"] is False
    assert state["st"] is False
    assert state["suspended"] is False
    assert state["resumed"] is True
    assert state["limit_up"] == 11.0
    assert state["limit_down"] == 9.0
    assert state["near_limit_up"] is True
    assert state["tradable"] is True
    assert state["block_reasons"] == []


def test_market_state_store_blocks_untradable_stock(tmp_path):
    store = MarketStateStore(_build_base_data(tmp_path))

    state = store.get_trade_state("000005", "2024-04-26", price=0.95)

    assert state["listed"] is False
    assert state["delisted"] is True
    assert state["st"] is True
    assert state["suspended"] is True
    assert state["at_limit_down"] is True
    assert state["tradable"] is False
    assert state["block_reasons"] == ["delisted", "st_flag", "suspended", "at_limit_down"]


def test_evaluate_trade_eligibility_uses_configured_store(tmp_path, monkeypatch):
    root = _build_base_data(tmp_path)
    monkeypatch.setenv("CONGXI_A_SHARE_BASE_DATA_DIR", str(root))

    result = evaluate_trade_eligibility("000005.SZ", "2024-04-26", price=1.0)

    assert result["tradable"] is False
    assert "delisted" in result["block_reasons"]
    assert "st_flag" in result["block_reasons"]
