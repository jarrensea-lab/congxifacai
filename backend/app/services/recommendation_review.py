"""Review executed recommendation outcomes from the local portfolio."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from app.config import PROJECT_ROOT
from app.data_sources.realtime_market_data import FastRealtimeMarketDataSource
from app.services.portfolio_store import load_user_portfolio, recalculate_portfolio


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(str(value).replace("%", "").replace(",", ""))
    except (TypeError, ValueError):
        return default


def _range_position_pct(bars: list[dict[str, Any]], price: float, lookback: int = 20) -> float | None:
    recent = bars[-lookback:] if len(bars) >= 10 else []
    highs = [_to_float(item.get("high") or item.get("close")) for item in recent]
    lows = [_to_float(item.get("low") or item.get("close")) for item in recent]
    highs = [item for item in highs if item > 0]
    lows = [item for item in lows if item > 0]
    if not highs or not lows:
        return None
    high = max(highs)
    low = min(lows)
    if high <= low:
        return None
    return max(0.0, min(100.0, (price - low) / (high - low) * 100))


def _entry_score(range_position_pct: float | None, return_pct: float, has_stop_plan: bool) -> tuple[int, list[str]]:
    score = 70
    flags: list[str] = []
    if range_position_pct is None:
        score -= 10
        flags.append("entry_range_missing")
    elif range_position_pct >= 80:
        score -= 35
        flags.append("high_position_entry")
    elif range_position_pct >= 65:
        score -= 18
        flags.append("upper_range_entry")
    elif range_position_pct <= 35:
        score += 8
    if return_pct < -3:
        score -= 20
        flags.append("loss_after_entry")
    elif return_pct > 2:
        score += 8
    if not has_stop_plan:
        score -= 20
        flags.append("stop_plan_missing")
    return max(0, min(100, score)), flags


def _trade_rows(portfolio: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for pos in portfolio.get("positions", []):
        code = str(pos.get("code") or "")
        if not code:
            continue
        rows.append({
            "code": code,
            "name": pos.get("name") or code,
            "state": "open",
            "shares": int(pos.get("shares") or 0),
            "entry_price": _to_float(pos.get("avg_cost")),
            "exit_or_mark_price": _to_float(pos.get("current_price"), _to_float(pos.get("avg_cost"))),
            "trade_date": (pos.get("trade_history") or [{}])[-1].get("date", ""),
            "pnl": _to_float(pos.get("pnl")),
            "pnl_pct": _to_float(pos.get("pnl_pct")),
        })
    for pos in portfolio.get("closed_positions", []):
        code = str(pos.get("code") or "")
        if not code:
            continue
        rows.append({
            "code": code,
            "name": pos.get("name") or code,
            "state": "closed",
            "shares": int(pos.get("shares") or 0),
            "entry_price": _to_float(pos.get("avg_cost")),
            "exit_or_mark_price": _to_float(pos.get("close_price")),
            "trade_date": pos.get("close_date", ""),
            "pnl": _to_float(pos.get("realized_pnl")),
            "pnl_pct": _to_float(pos.get("realized_pnl_pct")),
        })
    return rows


async def build_recommendation_review(
    *,
    portfolio_path: str | None = None,
    quote_source: Any | None = None,
) -> dict[str, Any]:
    portfolio = recalculate_portfolio(load_user_portfolio(portfolio_path))
    quote_source = quote_source or FastRealtimeMarketDataSource()
    rows = _trade_rows(portfolio)
    codes = list(dict.fromkeys(row["code"] for row in rows))
    quotes = await quote_source.fetch_batch(codes) if codes else {}
    stop_items = {}
    watch_path = Path(PROJECT_ROOT).parent / "data" / "position_watch.json"
    if watch_path.exists():
        import json

        stop_items = (json.loads(watch_path.read_text(encoding="utf-8")).get("items") or {})

    reviewed: list[dict[str, Any]] = []
    for row in rows:
        code = row["code"]
        quote = quotes.get(code) or {}
        current = _to_float(quote.get("price"), row["exit_or_mark_price"])
        if row["state"] == "open" and current > 0:
            row["exit_or_mark_price"] = current
            row["pnl"] = round((current - row["entry_price"]) * row["shares"], 2)
            row["pnl_pct"] = round((current - row["entry_price"]) / row["entry_price"] * 100, 2) if row["entry_price"] else 0
        kline = await quote_source.fetch_kline(code, "day", count=20) if hasattr(quote_source, "fetch_kline") else {}
        bars = (kline or {}).get("bars") or []
        entry_range = _range_position_pct(bars, row["entry_price"])
        score, flags = _entry_score(entry_range, row["pnl_pct"], code in stop_items or row["state"] == "closed")
        reviewed.append({
            **row,
            "current_price": current,
            "range_position_pct": round(entry_range, 1) if entry_range is not None else None,
            "behavior_score": score,
            "flags": flags,
            "quote_change_pct": _to_float(quote.get("change_pct")),
        })

    executed_count = len(reviewed)
    avg_return = round(sum(row["pnl_pct"] for row in reviewed) / executed_count, 2) if executed_count else 0.0
    win_rate = round(sum(1 for row in reviewed if row["pnl"] > 0) / executed_count * 100, 1) if executed_count else 0.0
    avg_score = round(sum(row["behavior_score"] for row in reviewed) / executed_count, 1) if executed_count else 0.0
    problem_flags = sorted({flag for row in reviewed for flag in row["flags"]})
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "executed": {
            "count": executed_count,
            "avg_return_pct": avg_return,
            "win_rate_pct": win_rate,
            "avg_behavior_score": avg_score,
            "total_pnl": round(sum(row["pnl"] for row in reviewed), 2),
            "problem_flags": problem_flags,
        },
        "portfolio": {
            "realized_pnl": portfolio.get("realized_pnl", 0),
            "total_pnl": portfolio.get("total_pnl", 0),
            "total_pnl_all": portfolio.get("total_pnl_all", 0),
            "total_assets": portfolio.get("total_assets", 0),
        },
        "items": reviewed,
        "system_gap": "sentinel_advice_performance_has_no_executed_samples",
    }


def render_recommendation_review_markdown(review: dict[str, Any]) -> str:
    executed = review.get("executed") or {}
    lines = [
        f"# 推荐执行复盘评分 - {review.get('generated_at', '')[:10]}",
        "",
        "## 总分",
        "",
        f"- 执行样本数：{executed.get('count', 0)}",
        f"- 平均收益率：{executed.get('avg_return_pct', 0):+.2f}%",
        f"- 胜率：{executed.get('win_rate_pct', 0):.1f}%",
        f"- 平均行为分：{executed.get('avg_behavior_score', 0):.1f}/100",
        f"- 总盈亏：¥{executed.get('total_pnl', 0):+.2f}",
        "",
        "## 单笔评分",
        "",
        "| 标的 | 状态 | 成本/标记 | 盈亏 | 入场区间 | 行为分 | 问题 |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    for item in review.get("items") or []:
        range_text = "缺失" if item.get("range_position_pct") is None else f"{item['range_position_pct']:.1f}%"
        lines.append(
            f"| {item.get('name')}({item.get('code')}) | {item.get('state')} | "
            f"¥{item.get('entry_price', 0):.3f}/¥{item.get('exit_or_mark_price', 0):.3f} | "
            f"¥{item.get('pnl', 0):+.2f}({item.get('pnl_pct', 0):+.2f}%) | "
            f"{range_text} | {item.get('behavior_score', 0)} | "
            f"{', '.join(item.get('flags') or []) or 'none'} |"
        )
    lines.extend([
        "",
        "## 复盘结论",
        "",
        "- 项目原有 Sentinel advice_performance 未记录本轮真实执行样本，导致推荐行为没有进入绩效闭环。",
        "- 行为分低的主要原因会集中在 high_position_entry / upper_range_entry / loss_after_entry。",
        "- 修复方向：高位放量突破不再直接给买入，只能转为 blocked_high_position 观察，等待回踩后重新评分。",
    ])
    return "\n".join(lines) + "\n"
