"""Review executed recommendation outcomes from the local portfolio."""
from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config import PROJECT_ROOT
from app.data_sources.realtime_market_data import FastRealtimeMarketDataSource
from app.services.execution_ledger import ExecutionLedger
from app.services.portfolio_store import load_user_portfolio, recalculate_portfolio


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "" or isinstance(value, bool):
            return default
        parsed = float(str(value).replace("%", "").replace(",", ""))
        return parsed if math.isfinite(parsed) else default
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
        history = pos.get("trade_history") or []
        rows.append({
            "code": code,
            "name": pos.get("name") or code,
            "state": "open",
            "shares": int(pos.get("shares") or 0),
            "entry_price": _to_float(pos.get("avg_cost")),
            "exit_or_mark_price": _to_float(pos.get("current_price"), _to_float(pos.get("avg_cost"))),
            "trade_date": (history or [{}])[-1].get("date", ""),
            "fill_ids": [item.get("fill_id") for item in history if item.get("fill_id")],
            "pnl": _to_float(pos.get("pnl")),
            "pnl_pct": _to_float(pos.get("pnl_pct")),
        })
    for pos in portfolio.get("closed_positions", []):
        code = str(pos.get("code") or "")
        if not code:
            continue
        history = pos.get("trade_history") or []
        rows.append({
            "code": code,
            "name": pos.get("name") or code,
            "state": "closed",
            "shares": int(pos.get("shares") or 0),
            "entry_price": _to_float(pos.get("avg_cost")),
            "exit_or_mark_price": _to_float(pos.get("close_price")),
            "trade_date": pos.get("close_date", ""),
            "fill_ids": [item.get("fill_id") for item in history if item.get("fill_id")],
            "pnl": _to_float(pos.get("realized_pnl")),
            "pnl_pct": _to_float(pos.get("realized_pnl_pct")),
        })
    return rows


def _validate_portfolio_structure(portfolio: Any) -> list[str]:
    if not isinstance(portfolio, dict):
        return ["portfolio_not_object"]

    diagnostics: list[str] = []
    for field in ("positions", "closed_positions", "trade_events"):
        rows = portfolio.get(field, [])
        if not isinstance(rows, list):
            diagnostics.append(f"{field}_not_list")
            continue
        for index, item in enumerate(rows):
            if not isinstance(item, dict):
                diagnostics.append(f"{field}_entry_not_object")
                continue
            if field == "trade_events":
                for required_field in ("fill_id", "code"):
                    value = item.get(required_field)
                    if not isinstance(value, str) or not value.strip():
                        diagnostics.append(f"trade_events[{index}].{required_field}_invalid")
                for optional_field in ("side", "signal_id", "recommendation_id"):
                    value = item.get(optional_field)
                    if value is not None and (
                        not isinstance(value, str) or not value.strip()
                    ):
                        diagnostics.append(f"trade_events[{index}].{optional_field}_invalid")
            if field not in {"positions", "closed_positions"} or "trade_history" not in item:
                continue
            trade_history = item.get("trade_history")
            if not isinstance(trade_history, list):
                diagnostics.append("trade_history_not_list")
            elif any(not isinstance(history_item, dict) for history_item in trade_history):
                diagnostics.append("trade_history_entry_not_object")
    return sorted(set(diagnostics))


def _is_finite_number(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError, OverflowError):
        return False


def _is_integer_at_least(value: Any, minimum: int) -> bool:
    if not _is_finite_number(value):
        return False
    numeric = float(value)
    return numeric.is_integer() and numeric >= minimum


def _validate_portfolio_numbers(portfolio: dict[str, Any]) -> list[str]:
    diagnostics: list[str] = []
    position_numeric_fields = (
        "avg_cost",
        "current_price",
        "total_cost",
        "current_value",
        "pnl",
        "pnl_pct",
        "realized_pnl",
        "realized_pnl_pct",
        "close_price",
    )
    for collection in ("positions", "closed_positions"):
        for index, item in enumerate(portfolio.get(collection, [])):
            shares = item.get("shares")
            minimum = 1 if collection == "positions" else 0
            if not _is_integer_at_least(shares, minimum):
                qualifier = "positive" if minimum == 1 else "nonnegative"
                diagnostics.append(f"{collection}[{index}].shares_not_{qualifier}_integer")
            for field in position_numeric_fields:
                if field in item and not _is_finite_number(item[field]):
                    diagnostics.append(f"{collection}[{index}].{field}_not_finite")

    for field in (
        "realized_pnl",
        "total_pnl",
        "total_pnl_all",
        "total_assets",
        "total_cost",
        "total_value",
        "available_cash",
        "cash",
    ):
        if field in portfolio and not _is_finite_number(portfolio[field]):
            diagnostics.append(f"portfolio.{field}_not_finite")
    return sorted(set(diagnostics))


def _empty_review(system_gap: str, *, diagnostics: list[str] | None = None) -> dict[str, Any]:
    review = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "executed": {
            "count": 0,
            "attributed_count": 0,
            "unattributed_count": 0,
            "metric_scope": "portfolio_behavior_not_strategy_attribution",
            "avg_return_pct": 0.0,
            "win_rate_pct": 0.0,
            "avg_behavior_score": 0.0,
            "total_pnl": 0.0,
            "problem_flags": [],
        },
        "portfolio": {
            "realized_pnl": 0,
            "total_pnl": 0,
            "total_pnl_all": 0,
            "total_assets": 0,
        },
        "items": [],
        "strategy_attribution": {
            "closed_count": 0,
            "total_pnl": 0.0,
            "avg_return_pct": 0.0,
            "win_rate_pct": 0.0,
            "metric_scope": "complete_execution_attribution_only",
        },
        "system_gap": system_gap,
    }
    if diagnostics:
        review["diagnostics"] = diagnostics
    return review


def _sync_portfolio_execution_events(
    portfolio: dict[str, Any],
    ledger: ExecutionLedger,
) -> list[str]:
    """Idempotently project linked portfolio fills/outcomes into the audit ledger."""
    history = ledger.read_with_diagnostics()
    if not history.get("ok"):
        return ["execution_ledger_history_invalid"]
    existing = history.get("events") or []
    existing_fills = {
        str(event.get("fill_id"))
        for event in existing
        if event.get("event_type") == "fill" and event.get("fill_id")
    }
    existing_open_outcomes = {
        str(event.get("fill_id"))
        for event in existing
        if event.get("event_type") == "outcome"
        and event.get("fill_id")
        and str((event.get("payload") or {}).get("state") or "").lower() in {"open", "pending"}
    }
    existing_terminal_outcomes = {
        str(event.get("fill_id"))
        for event in existing
        if event.get("event_type") == "outcome"
        and event.get("fill_id")
        and str((event.get("payload") or {}).get("state") or "").lower() in {"closed", "terminal"}
    }
    open_codes = {
        str(position.get("code") or "")
        for position in portfolio.get("positions", [])
        if isinstance(position, dict)
    }
    closed_by_fill: dict[str, dict[str, Any]] = {}
    for position in portfolio.get("closed_positions", []):
        if not isinstance(position, dict):
            continue
        linked_buys = [
            item
            for item in position.get("trade_history", [])
            if isinstance(item, dict)
            and item.get("fill_id")
            and item.get("signal_id")
            and item.get("recommendation_id")
            and str(item.get("type") or item.get("side") or "buy") == "buy"
        ]
        if len(linked_buys) == 1:
            closed_by_fill[str(linked_buys[0]["fill_id"])] = position

    diagnostics: list[str] = []
    for trade in portfolio.get("trade_events", []):
        if not isinstance(trade, dict):
            continue
        fill_id = str(trade.get("fill_id") or "").strip()
        signal_id = str(trade.get("signal_id") or "").strip()
        recommendation_id = str(trade.get("recommendation_id") or "").strip()
        code = str(trade.get("code") or "").strip()
        if not all((fill_id, signal_id, recommendation_id, code)):
            continue
        occurred_at = str(trade.get("occurred_at") or "").strip()
        if not occurred_at:
            occurred_at = datetime.now().astimezone().isoformat(timespec="seconds")
        if fill_id not in existing_fills:
            result = ledger.append_event({
                "event_id": f"evt_fill_{fill_id}",
                "event_type": "fill",
                "occurred_at": occurred_at,
                "code": code,
                "signal_id": signal_id,
                "recommendation_id": recommendation_id,
                "fill_id": fill_id,
                "source": "portfolio_trade_event",
                "payload": {
                    "side": trade.get("side"),
                    "shares": trade.get("shares"),
                    "price": trade.get("price"),
                    "fees": trade.get("fees"),
                    "fee_status": trade.get("fee_status"),
                },
            })
            if not result.get("ok"):
                diagnostics.append(f"fill_sync_failed:{fill_id}")
                continue
            existing_fills.add(fill_id)

        closed = closed_by_fill.get(fill_id)
        if closed is not None and fill_id not in existing_terminal_outcomes:
            outcome = ledger.append_event({
                "event_id": f"evt_outcome_closed_{fill_id}",
                "event_type": "outcome",
                "occurred_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "code": code,
                "signal_id": signal_id,
                "recommendation_id": recommendation_id,
                "fill_id": fill_id,
                "source": "portfolio_closed_position",
                "payload": {
                    "state": "closed",
                    "pnl": _to_float(closed.get("realized_pnl")),
                    "return_pct": _to_float(closed.get("realized_pnl_pct")),
                },
            })
            if not outcome.get("ok"):
                diagnostics.append(f"outcome_sync_failed:{fill_id}")
            else:
                existing_terminal_outcomes.add(fill_id)
        elif code in open_codes and fill_id not in existing_open_outcomes:
            outcome = ledger.append_event({
                "event_id": f"evt_outcome_open_{fill_id}",
                "event_type": "outcome",
                "occurred_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "code": code,
                "signal_id": signal_id,
                "recommendation_id": recommendation_id,
                "fill_id": fill_id,
                "source": "portfolio_open_position",
                "payload": {"state": "open"},
            })
            if not outcome.get("ok"):
                diagnostics.append(f"outcome_sync_failed:{fill_id}")
            else:
                existing_open_outcomes.add(fill_id)
    return diagnostics


async def build_recommendation_review(
    *,
    portfolio_path: str | None = None,
    execution_ledger_path: str | None = None,
    quote_source: Any | None = None,
) -> dict[str, Any]:
    try:
        raw_portfolio = load_user_portfolio(portfolio_path)
    except FileNotFoundError:
        return _empty_review("portfolio_missing")
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        return _empty_review(
            "portfolio_invalid",
            diagnostics=[f"portfolio_json_invalid:{type(exc).__name__}"],
        )
    structural_errors = _validate_portfolio_structure(raw_portfolio)
    if structural_errors:
        return _empty_review("portfolio_invalid", diagnostics=structural_errors)
    numeric_errors = _validate_portfolio_numbers(raw_portfolio)
    if numeric_errors:
        return _empty_review("portfolio_invalid", diagnostics=numeric_errors)
    try:
        portfolio = recalculate_portfolio(raw_portfolio)
    except (TypeError, ValueError, OverflowError):
        return _empty_review("portfolio_invalid", diagnostics=["portfolio_value_invalid"])
    calculated_numeric_errors = _validate_portfolio_numbers(portfolio)
    if calculated_numeric_errors:
        return _empty_review("portfolio_invalid", diagnostics=calculated_numeric_errors)
    quote_source = quote_source or FastRealtimeMarketDataSource()
    rows = _trade_rows(portfolio)
    portfolio_trade_events = [
        item for item in portfolio.get("trade_events", []) if isinstance(item, dict)
    ]
    resolved_ledger_path = execution_ledger_path
    if resolved_ledger_path is None and portfolio_path is not None:
        resolved_ledger_path = str(Path(portfolio_path).parent / "execution_ledger.jsonl")
    ledger = ExecutionLedger(resolved_ledger_path)
    sync_diagnostics = _sync_portfolio_execution_events(portfolio, ledger)
    attribution = ledger.join_attribution(
        portfolio_trade_events=portfolio_trade_events,
    )
    attribution_by_fill = {
        item.get("fill_id"): item
        for item in attribution.get("chains", [])
        if item.get("fill_id")
    }
    portfolio_fills_by_code: dict[str, list[str]] = {}
    for event in portfolio_trade_events:
        if event.get("code") and event.get("fill_id"):
            portfolio_fills_by_code.setdefault(str(event["code"]), []).append(str(event["fill_id"]))
    codes = list(dict.fromkeys(row["code"] for row in rows))
    quotes = await quote_source.fetch_batch(codes) if codes else {}
    stop_items = {}
    watch_path = Path(PROJECT_ROOT).parent / "data" / "position_watch.json"
    if watch_path.exists():
        stop_items = (json.loads(watch_path.read_text(encoding="utf-8")).get("items") or {})

    reviewed: list[dict[str, Any]] = []
    for row in rows:
        code = row["code"]
        fill_ids = list(dict.fromkeys(row.get("fill_ids") or portfolio_fills_by_code.get(code, [])))
        chain_rows = [attribution_by_fill[fill_id] for fill_id in fill_ids if fill_id in attribution_by_fill]
        fully_attributed = bool(fill_ids) and len(chain_rows) == len(fill_ids) and all(
            item.get("status") == "attributed" for item in chain_rows
        )
        item_attribution = {
            "status": "attributed" if fully_attributed else "unattributed",
            "fill_ids": fill_ids,
            "reasons": []
            if fully_attributed
            else sorted({
                reason
                for item in chain_rows
                for reason in item.get("reasons", [])
            } or ({"fill_id_missing"} if not fill_ids else {"execution_chain_missing"})),
        }
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
            "attribution": item_attribution,
        })

    executed_count = len(reviewed)
    avg_return = round(sum(row["pnl_pct"] for row in reviewed) / executed_count, 2) if executed_count else 0.0
    win_rate = round(sum(1 for row in reviewed if row["pnl"] > 0) / executed_count * 100, 1) if executed_count else 0.0
    avg_score = round(sum(row["behavior_score"] for row in reviewed) / executed_count, 1) if executed_count else 0.0
    attributed_count = sum(row["attribution"]["status"] == "attributed" for row in reviewed)
    problem_flags = sorted({flag for row in reviewed for flag in row["flags"]})
    attribution_history_invalid = (
        not attribution.get("ok")
        and attribution.get("error") == "execution_ledger_history_invalid"
    ) or "execution_ledger_history_invalid" in sync_diagnostics
    complete_chain_count = sum(
        item.get("status") in {"attributed", "pending"}
        for item in attribution.get("chains", [])
    )
    if attribution_history_invalid:
        system_gap = "execution_ledger_history_invalid"
    elif executed_count == 0:
        system_gap = "no_executed_samples"
    elif complete_chain_count == 0:
        system_gap = "execution_chain_missing"
    else:
        system_gap = ""
    review = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "executed": {
            "count": executed_count,
            "attributed_count": attributed_count,
            "unattributed_count": executed_count - attributed_count,
            "metric_scope": "portfolio_behavior_not_strategy_attribution",
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
        "strategy_attribution": attribution.get("strategy_metrics") or _empty_review("")["strategy_attribution"],
        "system_gap": system_gap,
    }
    if attribution_history_invalid:
        review["diagnostics"] = ["execution_ledger_history_invalid"]
    return review


def render_recommendation_review_markdown(review: dict[str, Any]) -> str:
    executed = review.get("executed") or {}
    strategy_attribution = review.get("strategy_attribution") or {}
    if review.get("system_gap") == "portfolio_missing":
        gap_conclusion = "- 未找到本地持仓文件，真实执行复盘本次只输出空样本。"
    elif review.get("system_gap") == "portfolio_invalid":
        diagnostics = ", ".join(review.get("diagnostics") or [])
        gap_conclusion = f"- 持仓文件结构无效，已拒绝计算：{diagnostics or '未知结构错误'}。"
    elif review.get("system_gap") == "execution_ledger_history_invalid":
        gap_conclusion = "- 执行账本历史无效，策略归因已失败关闭；持仓行为指标仍单独展示。"
    elif review.get("system_gap") == "no_executed_samples":
        gap_conclusion = "- 当前没有真实执行样本，暂不能计算策略归因。"
    elif review.get("system_gap") == "execution_chain_missing":
        gap_conclusion = "- 已有真实执行，但推荐、授权决策与成交链路不完整，不能归因到策略。"
    else:
        gap_conclusion = "- 至少一笔真实执行具备完整归因链，策略绩效仅统计完整链样本。"
    lines = [
        f"# 推荐执行复盘评分 - {review.get('generated_at', '')[:10]}",
        "",
        "## 总分",
        "",
        f"- 执行样本数：{executed.get('count', 0)}",
        f"- 完整归因：{executed.get('attributed_count', 0)}",
        f"- 未归因：{executed.get('unattributed_count', 0)}",
        f"- 平均收益率：{executed.get('avg_return_pct', 0):+.2f}%",
        f"- 胜率：{executed.get('win_rate_pct', 0):.1f}%",
        f"- 平均行为分：{executed.get('avg_behavior_score', 0):.1f}/100",
        f"- 总盈亏：¥{executed.get('total_pnl', 0):+.2f}",
        f"- 策略归因已平仓样本：{strategy_attribution.get('closed_count', 0)}",
        f"- 策略归因盈亏：¥{strategy_attribution.get('total_pnl', 0):+.2f}",
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
        gap_conclusion,
        "- 行为分低的主要原因会集中在 high_position_entry / upper_range_entry / loss_after_entry。",
        "- 修复方向：高位放量突破不再直接给买入，只能转为 blocked_high_position 观察，等待回踩后重新评分。",
    ])
    return "\n".join(lines) + "\n"
