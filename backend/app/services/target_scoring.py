"""Target scoring for profit-oriented pool decisions."""
from __future__ import annotations

from typing import Any

from app.services.market_regime import evaluate_market_regime
from app.services.playbook_engine import select_playbook
from app.services.position_sizing import calculate_position_size
from app.services.quant_lifecycle import lot_size_for_code
from app.services.strategy_profile import get_strategy_profile


REQUIRED_SOURCES = ("quote", "kline", "fund_flow", "financial")


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(str(value).replace("%", "").replace(",", ""))
    except (TypeError, ValueError):
        return default


def _status_ok(payload: dict[str, Any] | None) -> bool:
    return isinstance(payload, dict) and payload.get("status") == "ok"


def _buy_budget(available_cash: float, total_assets: float) -> float:
    profile = get_strategy_profile()
    assets = _to_float(total_assets, _to_float(available_cash))
    cash = _to_float(available_cash)
    single_limit = assets * (_to_float(profile.get("single_position_limit_pct"), 50) / 100) if assets else cash
    reserve_cash = assets * (_to_float(profile.get("cash_reserve_pct"), 10) / 100) if assets else 0
    return round(max(0.0, min(cash - reserve_cash, single_limit)), 2)


def _technical_score(snapshot: dict[str, Any]) -> int:
    quote = snapshot.get("quote") or {}
    kline = snapshot.get("kline") or {}
    bars = kline.get("bars") or []
    score = 50
    change_pct = _to_float(quote.get("change_pct"))
    vol_ratio = _to_float(quote.get("vol_ratio"))
    amount_wan = _to_float(quote.get("amount_wan"))
    if change_pct >= 3:
        score += 12
    if vol_ratio >= 2:
        score += 10
    if amount_wan >= 10000:
        score += 8
    if len(bars) >= 3:
        closes = [_to_float(item.get("close")) for item in bars[-3:]]
        if closes == sorted(closes) and closes[-1] > closes[0]:
            score += 8
    return max(0, min(100, score))


def score_target(
    snapshot: dict[str, Any],
    *,
    available_cash: float,
    total_assets: float,
) -> dict[str, Any]:
    """Return a deterministic score card and action for one target."""
    code = str(snapshot.get("code") or "").strip()
    name = str(snapshot.get("name") or code)
    quote = snapshot.get("quote") or {}
    profile = get_strategy_profile()
    price = _to_float(quote.get("price"))
    lot_size = lot_size_for_code(code)
    lot_value = round(price * lot_size, 2) if price > 0 else 0.0
    budget = _buy_budget(available_cash, total_assets)
    missing_data = [key for key in REQUIRED_SOURCES if not _status_ok(snapshot.get(key))]
    stop_loss = round(price * 0.95, 2) if price > 0 else 0
    target_price = round(price * 1.12, 2) if price > 0 else 0
    regime = evaluate_market_regime(snapshot)
    playbook = select_playbook(snapshot) if price > 0 else {}
    sizing = (
        calculate_position_size(
            code=code,
            entry_price=price,
            stop_loss=stop_loss,
            available_cash=available_cash,
            total_assets=total_assets,
            profile=profile,
        )
        if price > 0
        else {}
    )

    base = {
        "code": code,
        "name": name,
        "score": 0,
        "action": "watch",
        "block_reason": "",
        "decision_reason": "",
        "missing_data": missing_data,
        "entry_price": round(price, 2) if price else 0,
        "stop_loss": stop_loss,
        "target_price": target_price,
        "position_amount": 0,
        "position_shares": 0,
        "risk_budget": sizing.get("risk_budget", 0),
        "risk_amount": 0,
        "playbook": playbook.get("playbook", "watch"),
        "market_regime": regime,
        "lot_size": lot_size,
        "lot_value": lot_value,
        "executable_budget": budget,
        "next_signal": "",
    }

    if price <= 0:
        return {
            **base,
            "score": 30,
            "action": "watch",
            "block_reason": "price_missing",
            "decision_reason": "缺少实时价格，无法计算触发价、止损和一手金额。",
        }

    if lot_value > available_cash or lot_value > budget:
        return {
            **base,
            "score": 55,
            "action": "research_only",
            "block_reason": "lot_size_exceeded",
            "decision_reason": f"{name}({code}) 买不起最小交易单位：{lot_size}股约需¥{lot_value:,.2f}，当前可执行预算约¥{budget:,.2f}。",
        }

    if missing_data:
        next_signal = "补齐" + "、".join(missing_data)
        if price > 0:
            next_signal += f"，且价格维持在¥{price:.2f}上方、量比>=2、成交额>=1亿元、资金流转正。"
        else:
            next_signal += "，并恢复实时价格后再给触发价。"
        return {
            **base,
            "score": 40,
            "action": "watch",
            "block_reason": "missing_required_data",
            "decision_reason": "缺少结构化数据项：" + "、".join(missing_data) + "；先补数据，不使用泛化观望兜底。",
            "next_signal": next_signal,
        }

    technical = _technical_score(snapshot)
    serenity_score = _to_float((snapshot.get("serenity") or {}).get("score"))
    total_score = round(min(100, technical * 0.7 + serenity_score * 0.3 + _to_float(playbook.get("score_bonus"))), 1)
    change_pct = _to_float(quote.get("change_pct"))

    if change_pct >= 9:
        return {
            **base,
            "score": total_score,
            "action": "watch",
            "block_reason": "blocked_chasing",
            "stop_loss": stop_loss,
            "target_price": target_price,
            "decision_reason": f"{name}({code}) 涨幅接近追高区，等待回踩确认。",
            "next_signal": f"等待回踩至¥{price * 0.97:.2f}附近且不破¥{stop_loss:.2f}，再重新评分。",
        }

    if sizing.get("block_reason") == "risk_budget_too_small":
        return {
            **base,
            "score": total_score,
            "action": "watch",
            "block_reason": "risk_budget_too_small",
            "stop_loss": stop_loss,
            "target_price": target_price,
            "decision_reason": f"{name}({code}) 一手亏损风险超过单笔风险预算，不能为了试错强行买入。",
            "next_signal": f"等待价格回落或止损距离收窄，使一手风险不超过¥{sizing.get('risk_budget', 0):.2f}。",
        }

    if playbook.get("playbook") == "dip_entry" and not regime.get("can_dip", True):
        return {
            **base,
            "score": total_score,
            "action": "watch",
            "block_reason": "regime_blocks_dip",
            "stop_loss": stop_loss,
            "target_price": target_price,
            "decision_reason": f"{name}({code}) 低吸形态出现，但{regime.get('reason')} 先不接飞刀。",
            "next_signal": "等大盘止跌、板块相对强度修复后，再重新评估 dip_entry。",
        }

    if total_score >= 70 and playbook.get("triggered"):
        return {
            **base,
            "score": total_score,
            "action": "buy",
            "entry_price": round(price, 2),
            "stop_loss": stop_loss,
            "target_price": target_price,
            "position_amount": sizing.get("position_amount", 0),
            "position_shares": sizing.get("shares", 0),
            "risk_amount": sizing.get("risk_amount", 0),
            "decision_reason": f"{name}({code}) {playbook.get('reason')} 按单笔风险预算试错，硬止损¥{stop_loss:.2f}。",
            "next_signal": playbook.get("next_signal") or f"若明日回踩不破¥{stop_loss:.2f}，可按¥{price:.2f}附近人工复核。",
        }

    return {
        **base,
        "score": total_score,
        "action": "watch",
        "block_reason": "price_not_triggered",
        "stop_loss": stop_loss,
        "target_price": target_price,
        "decision_reason": f"{name}({code}) 数据已覆盖，但{playbook.get('reason', '交易剧本未触发')}，等待交易剧本确认。",
        "next_signal": playbook.get("next_signal") or f"明日等价格站稳¥{price:.2f}、量比>=2、成交额>=1亿元且资金流不转弱。",
    }
