"""Deterministic trade playbook selection for target scoring."""
from __future__ import annotations

import math
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


def _valid_historical_bars(bars: Any) -> list[dict[str, float]]:
    """Normalize completed historical OHLC bars; the realtime quote is separate.

    Every valid bar, including the last one, is prior completed history and
    therefore participates in resistance calculation.
    """
    if not isinstance(bars, list):
        return []
    normalized: list[dict[str, float]] = []
    for item in bars:
        if not isinstance(item, dict):
            continue
        values = {key: _to_float(item.get(key)) for key in ("open", "high", "low", "close")}
        if any(not math.isfinite(value) or value <= 0 for value in values.values()):
            continue
        if values["high"] < max(values["open"], values["low"], values["close"]):
            continue
        if values["low"] > min(values["open"], values["high"], values["close"]):
            continue
        normalized.append(values)
    return normalized


def _recent_range_position_pct(bars: list[dict[str, Any]], price: float, lookback: int = 20) -> float | None:
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


def select_playbook(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Pick the first triggered playbook, with breakout before dip by default."""
    quote = snapshot.get("quote") or {}
    kline = snapshot.get("kline") if isinstance(snapshot.get("kline"), dict) else {}
    fund_flow = snapshot.get("fund_flow") or {}
    raw_bars = kline.get("bars") or []
    bars = [item for item in raw_bars if isinstance(item, dict)] if isinstance(raw_bars, list) else []
    historical_bars = _valid_historical_bars(raw_bars)
    price = _to_float(quote.get("price"))
    change_pct = _to_float(quote.get("change_pct"))
    vol_ratio = _to_float(quote.get("vol_ratio"))
    amount_wan = _to_float(quote.get("amount_wan"))
    range_position_pct = _recent_range_position_pct(historical_bars, price)

    base = {
        "playbook": "watch",
        "triggered": False,
        "score_bonus": 0,
        "reason": "",
        "next_signal": "",
        "range_position_pct": range_position_pct,
        "block_reason": "",
    }

    if price <= 0:
        return {
            **base,
            "reason": "缺少实时价格，无法选择交易剧本。",
            "next_signal": "恢复实时价格后重新评估 breakout_entry / dip_entry。",
        }

    if change_pct >= 3 and vol_ratio >= 2 and amount_wan >= 10000:
        if len(historical_bars) < 20:
            return {
                **base,
                "playbook": "breakout_watch",
                "block_reason": "breakout_kline_insufficient",
                "reason": "放量上涨但有效历史OHLC K线不足20根，无法确认突破结构。",
                "next_signal": "补齐最近20根有效历史OHLC K线后重新评估 breakout_entry。",
            }
        trigger_price = _to_float(snapshot.get("trigger_price"))
        if not math.isfinite(trigger_price) or trigger_price <= 0:
            return {
                **base,
                "playbook": "breakout_watch",
                "block_reason": "breakout_trigger_invalid",
                "reason": "缺少有效正数触发价，不能确认突破买点。",
                "next_signal": "补齐有效正数触发价后重新评估 breakout_entry。",
            }
        if price < trigger_price:
            return {
                **base,
                "playbook": "breakout_watch",
                "block_reason": "breakout_trigger_not_crossed",
                "reason": f"放量上涨但现价¥{price:.2f}尚未达到触发价¥{trigger_price:.2f}。",
                "next_signal": f"现价达到或越过¥{trigger_price:.2f}后重新评估 breakout_entry。",
            }
        recent_history = historical_bars[-20:]
        resistance = max(item["high"] for item in recent_history)
        if price < resistance and range_position_pct is not None and range_position_pct >= 80:
            return {
                **base,
                "playbook": "breakout_watch",
                "block_reason": "blocked_high_position",
                "reason": f"放量上涨但位于近20日区间高位({range_position_pct:.1f}%)，不再按突破追买。",
                "next_signal": f"等待回踩到近20日区间80%以下或回踩不破¥{price * 0.97:.2f}后重新评分。",
            }
        if price < resistance:
            return {
                **base,
                "playbook": "breakout_watch",
                "block_reason": "breakout_resistance_not_crossed",
                "reason": f"放量上涨但现价¥{price:.2f}尚未达到近20根历史K线前高¥{resistance:.2f}。",
                "next_signal": f"现价达到或越过历史前高¥{resistance:.2f}后重新评估 breakout_entry。",
            }
        return {
            **base,
            "playbook": "breakout_entry",
            "triggered": True,
            "reason": "放量突破触发：涨幅、量比和成交额达标，且现价达到或越过历史前高。",
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
