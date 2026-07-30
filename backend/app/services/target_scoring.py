"""Target scoring for profit-oriented pool decisions."""
from __future__ import annotations

from typing import Any

from app.services.composite_score import build_composite_score, grade_for_score
from app.services.market_regime import evaluate_market_regime
from app.services.long_thesis import evaluate_thesis_status
from app.services.playbook_engine import select_playbook
from app.services.position_sizing import calculate_position_size
from app.services.quant_lifecycle import LONG_HORIZON_STATUSES, lot_size_for_code
from app.services.strategy_profile import (
    calculate_stop_loss_price,
    calculate_target_price,
    get_strategy_profile,
)


REQUIRED_SOURCES = ("quote", "kline", "fund_flow", "financial")
LONG_WATCH_MIN_SCORE = 70


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


def _valuation_zone(price: float, valuation_anchor: dict[str, Any] | None) -> str:
    if price <= 0 or not isinstance(valuation_anchor, dict):
        return "unknown"
    accumulation_price = _to_float(valuation_anchor.get("accumulation_price"))
    fair_price = _to_float(valuation_anchor.get("fair_price"))
    overpriced_price = _to_float(valuation_anchor.get("overpriced_price"))
    if accumulation_price > 0 and price <= accumulation_price:
        return "accumulation_zone"
    if overpriced_price > 0 and price >= overpriced_price:
        return "overpriced_zone"
    if fair_price > 0 and price <= fair_price:
        return "fair_zone"
    if fair_price > 0:
        return "above_fair_zone"
    return "unknown"


def _current_long_evidence_ids(thesis: dict[str, Any]) -> list[str]:
    values = thesis.get("current_long_evidence_ids")
    if not isinstance(values, list):
        return []
    return list(dict.fromkeys(
        str(value).strip()
        for value in values
        if str(value).strip()
    ))


def score_long_quality(snapshot: dict[str, Any], thesis: dict[str, Any] | None) -> dict[str, Any]:
    """Return long-horizon quality fields without creating a buy signal."""
    price = _to_float((snapshot.get("quote") or {}).get("price"))
    if not isinstance(thesis, dict) or not thesis:
        return {
            "long_quality_score": 0,
            "thesis_status": "",
            "valuation_zone": "unknown",
            "red_line_status": "",
            "long_horizon_reason": "",
            "verification_status": "",
            "current_long_evidence_ids": [],
        }
    status = evaluate_thesis_status(thesis)
    raw_quality = _to_float(thesis.get("quality_score") or thesis.get("score"))
    evaluated_status = str(status.get("status") or "")
    declared_status = str(thesis.get("thesis_status") or "")
    if evaluated_status == "broken" or declared_status == "broken":
        thesis_status = "broken"
    elif declared_status == "forming":
        thesis_status = "forming"
    elif evaluated_status in {"stale", "weakened"}:
        thesis_status = evaluated_status
    else:
        thesis_status = declared_status or evaluated_status
    if thesis_status in {"broken", "unknown"}:
        quality = 0
    elif thesis_status == "stale":
        quality = min(raw_quality, 40)
    elif thesis_status == "weakened":
        quality = min(raw_quality, 60)
    else:
        quality = raw_quality
    verification_status = str(thesis.get("verification_status") or "")
    missing_verification = thesis.get("missing_verification")
    if thesis_status == "unknown":
        reason = "thesis_status_unknown"
    elif thesis_status == "forming" or verification_status == "incomplete":
        missing = (
            [str(value) for value in missing_verification if str(value)]
            if isinstance(missing_verification, list)
            else []
        )
        reason = "verification_incomplete"
        if missing:
            reason += ":" + ",".join(missing)
    elif declared_status == "broken" and evaluated_status != "broken":
        reason = str(thesis.get("status_reason") or "thesis_marked_broken")
    else:
        reason = str(status.get("reason") or "")
    return {
        "long_quality_score": round(max(0, min(100, quality)), 1),
        "thesis_status": thesis_status,
        "valuation_zone": _valuation_zone(price, thesis.get("valuation_anchor")),
        "red_line_status": str(status.get("red_line_status") or ""),
        "long_horizon_reason": reason,
        "verification_status": verification_status,
        "current_long_evidence_ids": _current_long_evidence_ids(thesis),
    }


def next_target_status(
    current_status: str,
    action: str,
    long_view: dict[str, Any] | None,
    authorization_valid: bool = False,
) -> str:
    """Return the next lifecycle state without treating long quality as a buy gate."""
    current = str(current_status or "watching")
    normalized_action = str(action or "watch").strip().lower()
    view = long_view if isinstance(long_view, dict) else {}
    thesis_status = str(view.get("thesis_status") or "").strip().lower()
    red_line_status = str(view.get("red_line_status") or "").strip().lower()
    verification_status = str(
        view.get("verification_status") or ""
    ).strip().lower()
    missing_verification = view.get("missing_verification")
    verification_missing = (
        verification_status == "incomplete"
        or isinstance(missing_verification, list) and bool(missing_verification)
    )

    if normalized_action in {"remove", "removed", "exit", "sell"}:
        return "removed"
    if normalized_action == "exit_candidate":
        return "exit_candidate"
    if thesis_status == "broken" or red_line_status == "triggered":
        return "thesis_review"
    if thesis_status == "forming" or verification_missing:
        return "long_research"
    if normalized_action in {"buy", "add"}:
        if authorization_valid:
            return "executable"
        return current if current in LONG_HORIZON_STATUSES else "watching"
    if (
        current == "long_research"
        and thesis_status == "healthy"
        and _to_float(view.get("long_quality_score")) >= LONG_WATCH_MIN_SCORE
    ):
        return "long_watch"
    if current in LONG_HORIZON_STATUSES:
        return current
    if normalized_action == "research_only":
        return "research_reference"
    return "watching"


def score_target(
    snapshot: dict[str, Any],
    *,
    available_cash: float,
    total_assets: float,
    long_thesis: dict[str, Any] | None = None,
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
    stop_loss = calculate_stop_loss_price(price, profile)
    target_price = calculate_target_price(price, profile)
    regime = evaluate_market_regime(snapshot)
    playbook = select_playbook(snapshot) if price > 0 else {}
    long_view = score_long_quality(snapshot, long_thesis)
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
    change_pct = _to_float(quote.get("change_pct"))
    hard_gates_passed = (
        price > 0
        and not missing_data
        and lot_value <= available_cash
        and lot_value <= budget
        and playbook.get("triggered") is True
        and change_pct < 9
    )
    composite = build_composite_score(
        snapshot,
        playbook=playbook,
        entry_price=price,
        stop_loss=stop_loss,
        target_price=target_price,
        all_hard_gates_passed=hard_gates_passed,
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
        "range_position_pct": playbook.get("range_position_pct"),
        "market_regime": regime,
        "lot_size": lot_size,
        "lot_value": lot_value,
        "executable_budget": budget,
        "next_signal": "",
        "combined_decision_reason": "",
        "score_version": composite["score_version"],
        "score_components": composite["components"],
        "component_source_status": composite["source_status"],
        "grade": composite["grade"],
        "top_reasons": composite["top_reasons"],
        "primary_risk": composite["primary_risk"],
        **long_view,
    }

    def finish(updates: dict[str, Any]) -> dict[str, Any]:
        payload = {**base, **updates}
        payload["grade"] = grade_for_score(
            _to_float(payload.get("score")),
            all_hard_gates_passed=(
                payload.get("action") in {"buy", "add"}
                and not payload.get("block_reason")
            ),
        )
        production_eligibility = (
            snapshot.get("production_eligibility")
            if isinstance(snapshot.get("production_eligibility"), dict)
            else {}
        )
        payload["production_eligibility"] = production_eligibility
        if production_eligibility.get("eligible") is False and payload.get("action") in {"buy", "add"}:
            original_status = str(production_eligibility.get("original_status") or "research_reference")
            payload.update({
                "action": "research_only",
                "block_reason": "research_only_provenance",
                "position_amount": 0,
                "position_shares": 0,
                "risk_amount": 0,
                "decision_reason": (
                    f"{name}({code}) 原始状态为 {original_status}，仅用于研究/观察，"
                    "未经显式、可审计的生产晋级，不得生成买入动作。"
                ),
                "next_signal": "完成显式、可审计的生产晋级后，再按账户、剧本和风险门重新评分。",
            })
        decision_reason = str(payload.get("decision_reason") or "")
        long_reason = str(payload.get("long_horizon_reason") or "")
        if decision_reason and long_reason:
            payload["combined_decision_reason"] = f"{decision_reason} 长期跟踪：{long_reason}。"
        else:
            payload["combined_decision_reason"] = decision_reason
        return payload

    if price <= 0:
        return finish({
            "score": 30,
            "action": "watch",
            "block_reason": "price_missing",
            "decision_reason": "缺少实时价格，无法计算触发价、止损和一手金额。",
        })

    if lot_value > available_cash or lot_value > budget:
        return finish({
            "score": 55,
            "action": "research_only",
            "block_reason": "lot_size_exceeded",
            "decision_reason": f"{name}({code}) 买不起最小交易单位：{lot_size}股约需¥{lot_value:,.2f}，当前可执行预算约¥{budget:,.2f}。",
        })

    if missing_data:
        next_signal = "补齐" + "、".join(missing_data)
        if price > 0:
            next_signal += f"，且价格维持在¥{price:.2f}上方、量比>=2、成交额>=1亿元、资金流转正。"
        else:
            next_signal += "，并恢复实时价格后再给触发价。"
        return finish({
            "score": 40,
            "action": "watch",
            "block_reason": "missing_required_data",
            "decision_reason": "缺少结构化数据项：" + "、".join(missing_data) + "；先补数据，不使用泛化观望兜底。",
            "next_signal": next_signal,
        })

    if long_view["thesis_status"] == "broken":
        return finish({
            "score": 45,
            "action": "watch",
            "block_reason": "long_thesis_broken",
            "stop_loss": stop_loss,
            "target_price": target_price,
            "decision_reason": f"{name}({code}) 中长期 thesis 红线触发：{long_view['long_horizon_reason']}。先复核 thesis，不进入买入动作。",
            "next_signal": "完成 thesis review，确认红线解除或标的转入 exit_candidate / removed 后再恢复交易评分。",
        })

    total_score = composite["total"]

    if playbook.get("block_reason") == "blocked_high_position":
        return finish({
            "score": min(total_score, 68),
            "action": "watch",
            "block_reason": "blocked_high_position",
            "stop_loss": stop_loss,
            "target_price": target_price,
            "decision_reason": f"{name}({code}) {playbook.get('reason')} 先观察，不给建仓指令。",
            "next_signal": playbook.get("next_signal") or f"等待回踩至¥{price * 0.97:.2f}附近并重新评分。",
        })

    if change_pct >= 9:
        return finish({
            "score": total_score,
            "action": "watch",
            "block_reason": "blocked_chasing",
            "stop_loss": stop_loss,
            "target_price": target_price,
            "decision_reason": f"{name}({code}) 涨幅接近追高区，等待回踩确认。",
            "next_signal": f"等待回踩至¥{price * 0.97:.2f}附近且不破¥{stop_loss:.2f}，再重新评分。",
        })

    if sizing.get("block_reason") == "risk_budget_too_small":
        return finish({
            "score": total_score,
            "action": "watch",
            "block_reason": "risk_budget_too_small",
            "stop_loss": stop_loss,
            "target_price": target_price,
            "decision_reason": f"{name}({code}) 一手亏损风险超过单笔风险预算，不能为了试错强行买入。",
            "next_signal": f"等待价格回落或止损距离收窄，使一手风险不超过¥{sizing.get('risk_budget', 0):.2f}。",
        })

    if playbook.get("playbook") == "dip_entry" and not regime.get("can_dip", True):
        return finish({
            "score": total_score,
            "action": "watch",
            "block_reason": "regime_blocks_dip",
            "stop_loss": stop_loss,
            "target_price": target_price,
            "decision_reason": f"{name}({code}) 低吸形态出现，但{regime.get('reason')} 先不接飞刀。",
            "next_signal": "等大盘止跌、板块相对强度修复后，再重新评估 dip_entry。",
        })

    if total_score >= 70 and playbook.get("triggered"):
        return finish({
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
        })

    return finish({
        "score": total_score,
        "action": "watch",
        "block_reason": "price_not_triggered",
        "stop_loss": stop_loss,
        "target_price": target_price,
        "decision_reason": f"{name}({code}) 数据已覆盖，但{playbook.get('reason', '交易剧本未触发')}，等待交易剧本确认。",
        "next_signal": playbook.get("next_signal") or f"明日等价格站稳¥{price:.2f}、量比>=2、成交额>=1亿元且资金流不转弱。",
    })
