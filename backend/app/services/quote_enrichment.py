"""Attach live quote snapshots to strategy decisions before report rendering."""

from typing import Any


def _iter_recommendations(decision: dict) -> list[dict]:
    recommendations: list[dict] = []
    for bucket in ("short_term", "mid_low_freq"):
        section = decision.get(bucket, {})
        if not isinstance(section, dict):
            continue
        for rec in section.get("recommendations", []) or []:
            if isinstance(rec, dict):
                recommendations.append(rec)
    for rec in decision.get("stock_pool", []) or []:
        if isinstance(rec, dict) and rec not in recommendations:
            recommendations.append(rec)
    return recommendations


def _recommendation_codes(decision: dict) -> list[str]:
    codes: list[str] = []
    seen = set()
    for rec in _iter_recommendations(decision):
        code = str(rec.get("code") or "").strip()
        if not code or code in seen:
            continue
        seen.add(code)
        codes.append(code)
    return codes


async def enrich_decision_with_realtime_quotes(decision: dict, quote_source: Any) -> dict:
    """Mutate and return a decision with realtime_quote snapshots on recommendations."""
    codes = _recommendation_codes(decision)
    if not codes:
        decision["quote_validation"] = {"status": "skipped", "codes": [], "error": ""}
        return decision

    try:
        quotes = await quote_source.fetch_batch(codes)
    except Exception as exc:
        decision["quote_validation"] = {"status": "failed", "codes": codes, "error": str(exc)}
        return decision

    matched = []
    for rec in _iter_recommendations(decision):
        code = str(rec.get("code") or "").strip()
        quote = quotes.get(code)
        if quote:
            rec["realtime_quote"] = quote
            matched.append(code)

    decision["quote_validation"] = {
        "status": "success" if matched else "missing",
        "codes": codes,
        "matched_codes": sorted(set(matched), key=matched.index),
        "error": "",
    }
    return decision

