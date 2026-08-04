"""Deterministic financial-quality assessment for target scoring."""
from __future__ import annotations

import math
from typing import Any


_VALID_EARNINGS_PROFILES = frozenset({"cyclical", "compounder"})


def _number(value: Any) -> float | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        parsed = float(str(value).replace("%", "").replace(",", ""))
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _first_number(payload: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = _number(payload.get(key))
        if value is not None:
            return value
    return None


def _earnings_profile(payload: dict[str, Any]) -> str:
    profile = str(
        payload.get("earnings_profile")
        or payload.get("business_cycle")
        or ""
    ).strip().lower()
    return profile if profile in _VALID_EARNINGS_PROFILES else "unknown"


def assess_financial_quality(financial: dict[str, Any] | None) -> dict[str, Any]:
    """Return quality, coverage and cycle-aware valuation eligibility.

    Missing metrics do not count as failures. Coverage records how much of the
    100-point rubric was observable so downstream scoring can discount sparse
    but superficially strong payloads.
    """
    payload = financial if isinstance(financial, dict) else {}
    if str(payload.get("status") or "").strip().lower() != "ok":
        return {
            "status": "missing",
            "score": 0.0,
            "coverage": 0.0,
            "earnings_profile": "unknown",
            "flags": ["financial_source_missing"],
            "valuation_eligible": False,
        }

    profile = _earnings_profile(payload)
    revenue_yoy = _first_number(payload, "revenue_yoy_pct", "or_yoy", "tr_yoy")
    gross_margin = _first_number(payload, "gross_margin_pct", "grossprofit_margin")
    gross_margin_yoy = _first_number(payload, "gross_margin_yoy_pct")
    inventory_yoy = _first_number(payload, "inventory_yoy_pct")
    receivable_yoy = _first_number(payload, "receivable_yoy_pct")
    cashflow_yoy = _first_number(
        payload,
        "operating_cashflow_yoy_pct",
        "ocf_yoy",
    )
    free_cash_flow = _first_number(
        payload,
        "free_cash_flow",
        "free_cashflow",
        "fcf",
    )
    debt_to_asset = _first_number(
        payload,
        "debt_to_asset_pct",
        "debt_to_assets_pct",
        "debt_to_asset",
    )
    pe_ttm = _first_number(payload, "pe_ttm")
    normalized_earnings_yoy = _first_number(
        payload,
        "normalized_earnings_yoy_pct",
        "normalized_profit_yoy_pct",
    )
    normalized_earnings_positive = (
        payload.get("normalized_earnings_positive") is True
        or normalized_earnings_yoy is not None
        and normalized_earnings_yoy > 0
    )

    available = 0.0
    earned = 0.0
    flags: list[str] = []

    if revenue_yoy is not None:
        available += 15
        earned += 15 if revenue_yoy >= 10 else 10 if revenue_yoy > 0 else 4 if revenue_yoy >= -5 else 0

    if gross_margin is not None:
        available += 10
        earned += 10 if gross_margin >= 30 else 7 if gross_margin >= 15 else 4 if gross_margin > 0 else 0

    if gross_margin_yoy is not None:
        available += 10
        earned += 10 if gross_margin_yoy >= 0 else 4 if gross_margin_yoy > -2 else 0
        if gross_margin_yoy < 0:
            flags.append("gross_margin_declining")

    if cashflow_yoy is not None:
        available += 20
        earned += 20 if cashflow_yoy > 0 else 10 if cashflow_yoy >= -10 else 0
        if cashflow_yoy < -30:
            flags.append("operating_cashflow_deteriorating")

    if free_cash_flow is not None:
        available += 20
        if free_cash_flow > 0:
            earned += 20
        elif free_cash_flow < 0:
            flags.append("free_cash_flow_negative")

    if revenue_yoy is not None and inventory_yoy is not None:
        available += 10
        if inventory_yoy <= revenue_yoy:
            earned += 10
        else:
            flags.append("inventory_outpaces_revenue")

    if revenue_yoy is not None and receivable_yoy is not None:
        available += 10
        if receivable_yoy <= revenue_yoy:
            earned += 10
        else:
            flags.append("receivable_outpaces_revenue")

    if debt_to_asset is not None:
        available += 5
        earned += 5 if debt_to_asset < 50 else 3 if debt_to_asset <= 65 else 0
        if debt_to_asset > 70:
            flags.append("leverage_high")

    if profile == "cyclical":
        valuation_eligible = normalized_earnings_positive or (
            free_cash_flow is not None and free_cash_flow > 0
        )
        if pe_ttm is not None and pe_ttm > 0 and not valuation_eligible:
            flags.append("cyclical_valuation_unconfirmed")
    elif profile == "compounder":
        valuation_eligible = pe_ttm is not None and pe_ttm > 0
    else:
        valuation_eligible = False

    return {
        "status": "ok",
        "score": round(earned / available * 100, 1) if available else 0.0,
        "coverage": round(available / 100, 2),
        "earnings_profile": profile,
        "flags": list(dict.fromkeys(flags)),
        "valuation_eligible": valuation_eligible,
    }
