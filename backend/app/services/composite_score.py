"""Explainable six-component scorecard for v9 target decisions."""
from __future__ import annotations

from typing import Any

from app.services.playbook_engine import select_playbook
from app.services.strategy_profile import (
    calculate_stop_loss_price,
    calculate_target_price,
)
from app.version import SCORE_VERSION


COMPONENT_MAX = {
    "trend_volume": 30.0,
    "fund_flow": 20.0,
    "industry_catalyst": 15.0,
    "fundamental_valuation": 15.0,
    "relative_strength": 10.0,
    "risk_reward": 10.0,
}


def _number(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(str(value).replace("%", "").replace(",", ""))
    except (TypeError, ValueError):
        return default


def _source_status(payload: Any) -> str:
    if not isinstance(payload, dict) or not payload:
        return "missing"
    status = str(payload.get("status") or "").strip().lower()
    return status if status in {"ok", "missing", "failed", "stale"} else "missing"


def grade_for_score(
    total: float,
    *,
    all_hard_gates_passed: bool,
) -> str:
    if total >= 85 and all_hard_gates_passed:
        return "S"
    if total >= 75:
        return "A"
    if total >= 65:
        return "B"
    return "C"


def _trend_volume(snapshot: dict[str, Any], playbook: dict[str, Any]) -> tuple[float, str, str]:
    quote = snapshot.get("quote") if isinstance(snapshot.get("quote"), dict) else {}
    kline = snapshot.get("kline") if isinstance(snapshot.get("kline"), dict) else {}
    if _source_status(quote) != "ok" or _source_status(kline) != "ok":
        return 0.0, "量价数据不完整", "missing"
    change_pct = _number(quote.get("change_pct"))
    vol_ratio = _number(quote.get("vol_ratio"))
    amount_wan = _number(quote.get("amount_wan"))
    points = 8 if change_pct >= 3 else 5 if change_pct >= 0 else 3 if change_pct >= -3 else 0
    points += 7 if vol_ratio >= 2 else 4 if vol_ratio >= 1 else 0
    points += 6 if amount_wan >= 10_000 else 3 if amount_wan >= 5_000 else 0
    bars = [row for row in kline.get("bars") or [] if isinstance(row, dict)]
    closes = [_number(row.get("close")) for row in bars[-3:]]
    if len(closes) == 3 and closes == sorted(closes) and closes[-1] > closes[0]:
        points += 5
    if playbook.get("triggered") is True:
        points += 4
    points = min(COMPONENT_MAX["trend_volume"], float(points))
    reason = (
        f"量价结构{points:.0f}/30：涨幅{change_pct:.1f}%、"
        f"量比{vol_ratio:.1f}、成交额{amount_wan / 10_000:.1f}亿元"
    )
    return points, reason, "ok"


def _fund_flow(snapshot: dict[str, Any]) -> tuple[float, str, str]:
    payload = (
        snapshot.get("fund_flow")
        if isinstance(snapshot.get("fund_flow"), dict)
        else {}
    )
    status = _source_status(payload)
    if status != "ok":
        return 0.0, "个股资金流缺失", status
    text = " ".join(str(value) for value in payload.values())
    net = _number(
        payload.get("main_net_amount_wan")
        or payload.get("main_net_wan")
        or payload.get("net_amount_wan")
        or payload.get("net")
    )
    if any(token in text for token in ("净流入", "转正", "回流")) or net > 0:
        return 20.0, "主动资金净流入并与价格方向确认", "ok"
    if "收敛" in text:
        return 14.0, "资金流出正在收敛，尚需继续确认", "ok"
    if "净流出" in text or net < 0:
        return 0.0, "主动资金仍在净流出", "ok"
    return 10.0, "资金流数据可用但方向不够明确", "ok"


def _industry_catalyst(snapshot: dict[str, Any]) -> tuple[float, str, str]:
    news = snapshot.get("news") if isinstance(snapshot.get("news"), dict) else {}
    sentinel = (
        snapshot.get("sentinel")
        if isinstance(snapshot.get("sentinel"), dict)
        else {}
    )
    serenity = (
        snapshot.get("serenity")
        if isinstance(snapshot.get("serenity"), dict)
        else {}
    )
    available = [
        _source_status(news) == "ok",
        _source_status(sentinel) == "ok",
        _source_status(serenity) == "ok",
    ]
    if not any(available):
        return 0.0, "行业与催化证据缺失", "missing"
    points = 0.0
    if isinstance(news.get("items"), list) and news["items"]:
        points += 4
    if sentinel.get("evidence_ids"):
        points += 4
    serenity_score = _number(serenity.get("score"))
    if serenity_score >= 70:
        points += 5
    elif serenity_score >= 50:
        points += 3
    if serenity.get("theme"):
        points += 2
    points = min(COMPONENT_MAX["industry_catalyst"], points)
    return points, f"行业与催化证据{points:.0f}/15，已有新闻、主题或研究交叉验证", "ok"


def _fundamental_valuation(snapshot: dict[str, Any]) -> tuple[float, str, str]:
    payload = (
        snapshot.get("financial")
        if isinstance(snapshot.get("financial"), dict)
        else {}
    )
    status = _source_status(payload)
    if status != "ok":
        return 0.0, "财务与估值数据缺失", status
    points = 5.0
    revenue_yoy = _number(payload.get("revenue_yoy_pct"))
    gross_margin = _number(payload.get("gross_margin_pct"))
    pe_ttm = _number(payload.get("pe_ttm"))
    if revenue_yoy > 0:
        points += 5
    if gross_margin > 0:
        points += 3
    if 0 < pe_ttm <= 30:
        points += 2
    points = min(COMPONENT_MAX["fundamental_valuation"], points)
    return points, f"基本面与估值{points:.0f}/15，营收增速{revenue_yoy:.1f}%", "ok"


def _relative_strength(snapshot: dict[str, Any]) -> tuple[float, str, str]:
    regime = (
        snapshot.get("market_regime")
        if isinstance(snapshot.get("market_regime"), dict)
        else {}
    )
    if not regime:
        return 4.0, "市场相对强度缺少板块排名，按中性下限计分", "missing"
    label = str(regime.get("label") or regime.get("status") or "neutral").lower()
    sector_rank = _number(regime.get("sector_relative_rank"), 50)
    points = 6 if sector_rank <= 30 else 4 if sector_rank <= 60 else 1
    if label in {"bull", "bullish", "uptrend"}:
        points += 4
    elif label in {"neutral", "range", ""}:
        points += 2
    points = min(COMPONENT_MAX["relative_strength"], float(points))
    return points, f"市场与板块相对强度{points:.0f}/10", "ok"


def _risk_reward(
    snapshot: dict[str, Any],
    *,
    entry_price: float | None,
    stop_loss: float | None,
    target_price: float | None,
) -> tuple[float, str, str]:
    quote = snapshot.get("quote") if isinstance(snapshot.get("quote"), dict) else {}
    entry = _number(entry_price, _number(quote.get("price")))
    stop = _number(stop_loss, calculate_stop_loss_price(entry) if entry > 0 else 0)
    target = _number(
        target_price,
        calculate_target_price(entry) if entry > 0 else 0,
    )
    risk = entry - stop
    reward = target - entry
    if entry <= 0 or risk <= 0 or reward <= 0:
        return 0.0, "缺少有效入场、止损或目标价", "missing"
    ratio = reward / risk
    points = 8 if ratio >= 2 else 6 if ratio >= 1.5 else 3 if ratio >= 1 else 0
    if abs(_number(quote.get("change_pct"))) <= 8:
        points += 2
    points = min(COMPONENT_MAX["risk_reward"], float(points))
    return points, f"计划盈亏比{ratio:.1f}:1，风险收益{points:.0f}/10", "ok"


def build_composite_score(
    snapshot: dict[str, Any],
    *,
    playbook: dict[str, Any] | None = None,
    entry_price: float | None = None,
    stop_loss: float | None = None,
    target_price: float | None = None,
    all_hard_gates_passed: bool = True,
) -> dict[str, Any]:
    """Return the versioned score, concise reasons, risks, and source truth."""
    selected_playbook = playbook or select_playbook(snapshot)
    component_rows = {
        "trend_volume": _trend_volume(snapshot, selected_playbook),
        "fund_flow": _fund_flow(snapshot),
        "industry_catalyst": _industry_catalyst(snapshot),
        "fundamental_valuation": _fundamental_valuation(snapshot),
        "relative_strength": _relative_strength(snapshot),
        "risk_reward": _risk_reward(
            snapshot,
            entry_price=entry_price,
            stop_loss=stop_loss,
            target_price=target_price,
        ),
    }
    components = {
        key: round(max(0.0, min(COMPONENT_MAX[key], row[0])), 1)
        for key, row in component_rows.items()
    }
    total = round(sum(components.values()), 1)
    ranked_reasons = sorted(
        (
            (components[key] / COMPONENT_MAX[key], key, row[1])
            for key, row in component_rows.items()
            if components[key] > 0
        ),
        reverse=True,
    )
    top_reasons = [row[2] for row in ranked_reasons[:3]]
    while len(top_reasons) < 3:
        top_reasons.append("该维度暂无足够加分证据")
    weakest = min(
        component_rows,
        key=lambda key: components[key] / COMPONENT_MAX[key],
    )
    input_names = {
        "trend_volume": ("quote", "kline"),
        "fund_flow": ("fund_flow",),
        "industry_catalyst": ("news", "sentinel", "serenity"),
        "fundamental_valuation": ("financial",),
        "relative_strength": ("market_regime",),
        "risk_reward": ("quote",),
    }
    missing_inputs = list(dict.fromkeys(
        input_name
        for key, row in component_rows.items()
        if row[2] != "ok"
        for input_name in input_names[key]
    ))
    return {
        "score_version": SCORE_VERSION,
        "total": total,
        "grade": grade_for_score(
            total,
            all_hard_gates_passed=all_hard_gates_passed,
        ),
        "components": components,
        "source_status": {
            key: row[2]
            for key, row in component_rows.items()
        },
        "top_reasons": top_reasons,
        "primary_risk": component_rows[weakest][1],
        "missing_inputs": missing_inputs,
    }
