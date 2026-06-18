"""③ 执行规划引擎 — 仓位扫描 + 资金分配 + 操作指令生成"""


def generate_execution_plan(strategy_instance, holdings: list, available_cash: float) -> dict:
    """根据策略决策卡和实际仓位，生成操作计划书。

    Args:
        strategy_instance: StrategyInstance (已确认)
        holdings: [{"code":"000001","name":"平安银行","position":1000,"cost":12.5,"current_price":13.0},...]
        available_cash: 可用资金

    Returns:
        操作计划书 dict
    """
    stock_pool = strategy_instance.stock_pool or []
    position_limit = strategy_instance.position_limit_pct / 100
    single_limit = strategy_instance.single_stock_limit_pct / 100

    # 计算总资产
    total_assets = available_cash + sum(
        h.get("position", 0) * h.get("current_price", h.get("cost", 0))
        for h in holdings
    )

    buy_list = []
    sell_list = []
    hold_list = []

    # 当前持仓的代码集合
    holding_codes = {h["code"] for h in holdings}

    # 标的池中未持有的 → 买入清单
    pool_count = len(stock_pool)
    for stock in stock_pool:
        code = stock["code"]
        if code not in holding_codes:
            weight = stock.get("weight", 1.0 / max(pool_count, 1))
            allocated = available_cash * weight * position_limit
            buy_list.append({
                "code": code,
                "name": stock.get("name", ""),
                "action": "buy",
                "allocated_amount": round(allocated, 2),
                "reason": stock.get("reason", ""),
            })

    # 已持有但不在标的池 → 卖出清单
    for holding in holdings:
        code = holding["code"]
        if code not in {s["code"] for s in stock_pool}:
            sell_list.append({
                "code": code,
                "name": holding.get("name", ""),
                "action": "sell",
                "position": holding.get("position", 0),
                "reason": "不在当前策略标的池",
            })
        else:
            hold_list.append(holding)

    # 风控硬约束检查
    total_allocated = sum(b["allocated_amount"] for b in buy_list)
    checks = {
        "total_position_ok": (total_allocated / max(total_assets, 1)) <= position_limit,
        "single_position_ok": all(
            b["allocated_amount"] / max(total_assets, 1) <= single_limit
            for b in buy_list
        ),
        "holding_period_days": strategy_instance.holding_period_days,
    }

    return {
        "buy_list": buy_list,
        "sell_list": sell_list,
        "hold_list": hold_list,
        "total_assets": round(total_assets, 2),
        "available_cash": round(available_cash, 2),
        "total_allocated": round(total_allocated, 2),
        "expected_return_best": strategy_instance.expected_return_best or "+12%",
        "expected_return_neutral": strategy_instance.expected_return_neutral or "+5%",
        "expected_return_worst": strategy_instance.expected_return_worst or "-3%",
        "risk_checks": checks,
    }
