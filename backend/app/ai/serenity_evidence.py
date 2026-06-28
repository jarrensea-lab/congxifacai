"""Evidence task helpers for Serenity bottleneck research.

This module does not fetch live data yet. It creates deterministic verification
tasks so reports can separate seed ideas from evidence that still needs proof.
"""

from typing import Any, Dict, List


SOURCE_LABELS = {
    "announcement": "公告/财报/订单",
    "financial_report": "财务指标",
    "market_data": "行情与估值",
}


def _task_priority(candidate: Dict[str, Any]) -> str:
    evidence_items = candidate.get("evidence_items") or []
    if any(item.get("strength") == "weak" for item in evidence_items):
        return "high"
    if candidate.get("red_flag_signals"):
        return "high"
    return "medium"


def build_verification_tasks(theme: str, candidate: Dict[str, Any]) -> List[Dict[str, str]]:
    """Build evidence-verification tasks for one Serenity candidate."""
    name = candidate.get("name", "")
    code = candidate.get("code", "")
    chokepoint = candidate.get("chokepoint", "")
    verify_next = candidate.get("verify_next") or "核验公告、财报、订单、客户和毛利率。"
    priority = _task_priority(candidate)

    tasks = [
        {
            "theme": theme,
            "candidate_name": name,
            "candidate_code": code,
            "chokepoint": chokepoint,
            "priority": priority,
            "source_type": "announcement",
            "source_label": SOURCE_LABELS["announcement"],
            "task": verify_next,
        },
        {
            "theme": theme,
            "candidate_name": name,
            "candidate_code": code,
            "chokepoint": chokepoint,
            "priority": "medium",
            "source_type": "financial_report",
            "source_label": SOURCE_LABELS["financial_report"],
            "task": "核验收入、毛利率、存货、应收账款和经营现金流是否支持瓶颈逻辑。",
        },
        {
            "theme": theme,
            "candidate_name": name,
            "candidate_code": code,
            "chokepoint": chokepoint,
            "priority": "low",
            "source_type": "market_data",
            "source_label": SOURCE_LABELS["market_data"],
            "task": "核验价格位置、成交额、估值分位和一手金额是否适合小账户观察。",
        },
    ]
    return tasks


def _to_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def build_quote_evidence(
    candidate: Dict[str, Any],
    quote: Dict[str, Any],
    available_cash: float = 0,
) -> Dict[str, Any]:
    """Convert a quote snapshot into Serenity market-data evidence."""
    price = _to_float(quote.get("price"))
    lot_value = round(price * 100, 2) if price > 0 else 0.0
    cash = _to_float(available_cash)
    cash_coverage_ratio = round(cash / lot_value, 2) if lot_value else 0.0
    amount_wan = _to_float(quote.get("amount_wan"))
    mcap_yi = _to_float(quote.get("mcap_yi"))
    pe_ttm = _to_float(quote.get("pe_ttm"))
    pb = _to_float(quote.get("pb"))
    change_pct = _to_float(quote.get("change_pct"))
    source = quote.get("source") or "行情"

    fact = (
        f"{candidate.get('name', '')}({candidate.get('code', '')})行情核验："
        f"现价 {price:.2f} 元，一手金额 {lot_value:.2f} 元，"
        f"现金覆盖约 {cash_coverage_ratio:.2f} 手；"
        f"成交额 {amount_wan:.0f} 万元，市值 {mcap_yi:.2f} 亿元，"
        f"PE(TTM) {pe_ttm:.2f}，PB {pb:.2f}，涨跌幅 {change_pct:+.2f}%。"
    )

    return {
        "fact": fact,
        "strength": "medium" if price > 0 else "weak",
        "source": f"{source}行情",
        "metrics": {
            "price": round(price, 2),
            "lot_value": lot_value,
            "cash_coverage_ratio": cash_coverage_ratio,
            "amount_wan": round(amount_wan, 2),
            "mcap_yi": round(mcap_yi, 2),
            "pe_ttm": round(pe_ttm, 2),
            "pb": round(pb, 2),
            "change_pct": round(change_pct, 2),
        },
    }
