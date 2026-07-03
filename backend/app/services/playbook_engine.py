"""Deterministic trade playbook selection for target scoring."""
from __future__ import annotations

from typing import Any


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(str(value).replace("%", "").replace(",", ""))
    except (TypeError, ValueError):
        return default


def _fund_flow_ok(fund_flow: dict[str, Any]) -> bool:
    text = " ".join(str(value) for value in fund_flow.values())
    main_net = _to_float(
        fund_flow.get("main_net_amount_wan")
        or fund_flow.get("main_net_wan")
        or fund_flow.get("net_amount_wan")
        or fund_flow.get("net")
    )
    if main_net < -2000:
        return False
    return "净流出" not in text or any(token in text for token in ("收敛", "转正", "回流"))


def select_playbook(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Pick the first triggered playbook, with breakout before dip by default."""
    quote = snapshot.get("quote") or {}
    kline = snapshot.get("kline") or {}
    fund_flow = snapshot.get("fund_flow") or {}
    bars = kline.get("bars") or []
    price = _to_float(quote.get("price"))
    change_pct = _to_float(quote.get("change_pct"))
    vol_ratio = _to_float(quote.get("vol_ratio"))
    amount_wan = _to_float(quote.get("amount_wan"))

    base = {
        "playbook": "watch",
        "triggered": False,
        "score_bonus": 0,
        "reason": "",
        "next_signal": "",
    }

    if price <= 0:
        return {
            **base,
            "reason": "缺少实时价格，无法选择交易剧本。",
            "next_signal": "恢复实时价格后重新评估 breakout_entry / dip_entry。",
        }

    if change_pct >= 3 and vol_ratio >= 2 and amount_wan >= 10000:
        return {
            **base,
            "playbook": "breakout_entry",
            "triggered": True,
            "reason": "放量突破触发：涨幅、量比和成交额同时达标。",
            "next_signal": f"若回踩不破¥{price * 0.95:.2f}且量能不塌，可继续人工复核。",
        }

    if len(bars) < 3:
        return {
            **base,
            "reason": "K线不足，无法确认回踩不破结构。",
            "next_signal": "补齐最近3根以上K线后再评估 dip_entry。",
        }

    highs = [_to_float(item.get("high") or item.get("close")) for item in bars[-5:]]
    lows = [_to_float(item.get("low") or item.get("close")) for item in bars[-5:]]
    recent_high = max(highs) if highs else 0
    support = max(lows[:-1] or lows)
    pulled_back = recent_high > 0 and price <= recent_high * 0.97
    holds_support = support > 0 and price >= support * 0.995
    calm_change = -3 <= change_pct <= 2
    liquid_enough = amount_wan >= 5000

    if pulled_back and holds_support and calm_change and liquid_enough and _fund_flow_ok(fund_flow):
        return {
            **base,
            "playbook": "dip_entry",
            "triggered": True,
            "score_bonus": 8,
            "reason": "低吸回踩触发：从近期高点回落但未破支撑，量能和资金未恶化。",
            "next_signal": f"若继续不破¥{support:.2f}且资金流不转弱，可按低吸剧本人工复核。",
        }

    return {
        **base,
        "reason": "未触发 breakout_entry 或 dip_entry。",
        "next_signal": "等待放量突破，或回踩不破支撑且资金流出收敛。",
    }
