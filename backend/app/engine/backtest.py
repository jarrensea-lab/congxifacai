"""回测引擎 — 基于历史 K线数据的策略模拟

V6 新增: 为每支持仓股和推荐股提供历史回测指标，包括胜率、收益、最大回撤。
回测结果通过飞书多维表格和画板展示。

使用方式:
    result = await run_backtest(stock_code, period_days=180)
    # result: {total_trades, win_rate_pct, avg_profit_pct, ...}
"""
from typing import Dict, Any
from app.utils.logger import logger


async def run_backtest(
    stock_code: str,
    period_days: int = 180,
    strategy: str = "ma_crossover",
) -> Dict[str, Any]:
    """对指定股票运行回测。

    Args:
        stock_code: 股票代码
        period_days: 回测周期 (默认 6 个月)
        strategy: 策略类型 ("ma_crossover" 或 "momentum")

    Returns:
        回测指标 dict:
        {
            "total_trades": 总交易笔数,
            "win_rate_pct": 胜率(%),
            "avg_profit_pct": 平均盈利(%),
            "avg_loss_pct": 平均亏损(%),
            "max_drawdown_pct": 最大回撤(%),
            "cumulative_return_pct": 累计收益率(%),
            "sharpe_ratio": 夏普比率(简化),
            "trades": [{"date": "...", "action": "buy/sell", "price": ..., "pnl_pct": ...}],
        }
    """

    # 1. 获取历史K线数据
    bars = await _fetch_kline(stock_code, period_days)

    if not bars or len(bars) < 30:
        return {
            "total_trades": 0,
            "win_rate_pct": 0,
            "avg_profit_pct": 0,
            "avg_loss_pct": 0,
            "max_drawdown_pct": 0,
            "cumulative_return_pct": 0,
            "sharpe_ratio": 0,
            "trades": [],
            "note": "历史数据不足 (<30个交易日), 无法回测",
        }

    # 2. 运行策略模拟
    if strategy == "ma_crossover":
        trades = _simulate_ma_crossover(bars)
    else:
        trades = _simulate_momentum(bars)

    # 3. 计算指标
    metrics = _calculate_metrics(trades)

    return {
        **metrics,
        "trades": trades,
        "period": f"最近{period_days}天",
        "strategy": strategy,
    }


async def _fetch_kline(stock_code: str, period_days: int):
    """获取历史K线 — 优先 Tushare, 备选腾讯"""
    # 尝试 Tushare
    try:
        from app.data_sources.tushare_client import TushareDataSource
        tushare = TushareDataSource()
        if tushare.is_available():
            kline = await tushare.fetch_kline(stock_code, "day", count=period_days)
            bars = kline.get("bars", [])
            if bars and len(bars) >= 20:
                return bars
    except Exception as e:
        logger.debug(f"Tushare kline for backtest failed: {e}")

    # 降级到腾讯
    try:
        from app.data_sources.tencent_client import TencentDataSource
        tencent = TencentDataSource()
        kline = await tencent.fetch_kline(stock_code, "day", count=period_days)
        bars = kline.get("bars", [])
        if bars:
            return bars
    except Exception as e:
        logger.debug(f"Tencent kline for backtest failed: {e}")

    return []


def _simulate_ma_crossover(bars: list) -> list:
    """MA5/MA20 金叉死叉策略模拟

    - 金叉(MA5上穿MA20) → 买入
    - 死叉(MA5下穿MA20) → 卖出
    - 简化: 不考虑交易成本
    """
    if len(bars) < 30:
        return []

    closes = [b.get("close", 0) for b in bars]

    # 计算均线
    def ma(data, period):
        result = [0] * len(data)
        for i in range(period - 1, len(data)):
            result[i] = sum(data[i - period + 1:i + 1]) / period
        return result

    ma5 = ma(closes, 5)
    ma20 = ma(closes, 20)

    trades = []
    position = None
    entry_price = 0

    for i in range(21, len(bars)):
        if position is None:
            # 金叉 → 买入
            if ma5[i - 1] <= ma20[i - 1] and ma5[i] > ma20[i]:
                entry_price = closes[i]
                position = "long"
                trades.append({
                    "date": str(bars[i].get("date", "")),
                    "action": "buy",
                    "price": round(entry_price, 2),
                    "pnl_pct": 0,
                })
        else:
            # 死叉 → 卖出
            if ma5[i - 1] >= ma20[i - 1] and ma5[i] < ma20[i]:
                pnl_pct = round((closes[i] - entry_price) / entry_price * 100, 2)
                trades.append({
                    "date": str(bars[i].get("date", "")),
                    "action": "sell",
                    "price": round(closes[i], 2),
                    "pnl_pct": pnl_pct,
                })
                position = None
                entry_price = 0

    return trades


def _simulate_momentum(bars: list) -> list:
    """动量策略 — 5日涨幅>3%买入, 3日连跌卖出"""
    if len(bars) < 5:
        return []
    closes = [b.get("close", 0) for b in bars]
    trades = []
    position = None
    entry_price = 0
    lose_streak = 0

    for i in range(5, len(closes)):
        if position is None:
            change_5d = (closes[i] - closes[i - 5]) / closes[i - 5] * 100
            if change_5d > 3:
                entry_price = closes[i]
                position = "long"
                lose_streak = 0
                trades.append({
                    "date": str(bars[i].get("date", "")),
                    "action": "buy",
                    "price": round(entry_price, 2),
                    "pnl_pct": 0,
                })
        else:
            if closes[i] < closes[i - 1]:
                lose_streak += 1
            else:
                lose_streak = 0

            if lose_streak >= 3:
                pnl_pct = round((closes[i] - entry_price) / entry_price * 100, 2)
                trades.append({
                    "date": str(bars[i].get("date", "")),
                    "action": "sell",
                    "price": round(closes[i], 2),
                    "pnl_pct": pnl_pct,
                })
                position = None
                entry_price = 0
    return trades


def _calculate_metrics(trades: list) -> Dict[str, Any]:
    """根据交易记录计算回测指标"""
    if not trades:
        return {
            "total_trades": 0, "win_rate_pct": 0,
            "avg_profit_pct": 0, "avg_loss_pct": 0,
            "max_drawdown_pct": 0, "cumulative_return_pct": 0,
            "sharpe_ratio": 0,
        }

    sells = [t for t in trades if t["action"] == "sell"]
    pnls = [t["pnl_pct"] for t in sells]

    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]

    win_rate = len(wins) / len(pnls) * 100 if pnls else 0
    avg_profit = sum(wins) / len(wins) if wins else 0
    avg_loss = sum(losses) / len(losses) if losses else 0
    cumulative = sum(pnls)

    # 简化夏普比率
    if len(pnls) > 1:
        mean = sum(pnls) / len(pnls)
        variance = sum((p - mean) ** 2 for p in pnls) / len(pnls)
        std = variance ** 0.5
        sharpe = mean / std * (252 ** 0.5) if std > 0 else 0
    else:
        sharpe = 0

    # 简化最大回撤
    max_dd = min(pnls + [0]) if pnls else 0

    return {
        "total_trades": len(pnls),
        "win_rate_pct": round(win_rate, 1),
        "avg_profit_pct": round(avg_profit, 1),
        "avg_loss_pct": round(avg_loss, 1),
        "max_drawdown_pct": round(max_dd, 1),
        "cumulative_return_pct": round(cumulative, 1),
        "sharpe_ratio": round(sharpe, 2),
    }
