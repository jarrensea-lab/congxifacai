"""Pure market-index quote validation and completeness aggregation."""
from collections.abc import Mapping, Sequence
from datetime import datetime
import math


EXPECTED_MARKET_INDEX_CODES = ("sh000001", "sz399001", "sz399006")
MARKET_DATA_MAX_AGE_SECONDS = 15 * 60
MARKET_DATA_FUTURE_TOLERANCE_SECONDS = 60


def parse_aware_market_timestamp(value) -> datetime | None:
    """Parse an ISO timestamp, rejecting values without an explicit UTC offset."""
    cutoff_text = (
        value.isoformat() if isinstance(value, datetime) else str(value or "").strip()
    )
    if not cutoff_text:
        return None
    try:
        parsed = datetime.fromisoformat(cutoff_text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def is_recent_market_cutoff(
    value,
    *,
    now: datetime,
    max_age_seconds: int = MARKET_DATA_MAX_AGE_SECONDS,
) -> bool:
    """Return whether an aware market timestamp is within the trusted window."""
    parsed = parse_aware_market_timestamp(value)
    if parsed is None or now.tzinfo is None or now.utcoffset() is None:
        return False
    age_seconds = (now - parsed).total_seconds()
    return (
        -MARKET_DATA_FUTURE_TOLERANCE_SECONDS
        <= age_seconds
        <= max_age_seconds
    )


def fresh_market_quote_truth(
    quote: Mapping,
    *,
    now: datetime,
    max_age_seconds: int = MARKET_DATA_MAX_AGE_SECONDS,
) -> tuple[datetime, str] | None:
    """Return parsed quote time and accepted freshness for a trusted quote."""
    freshness = str(
        quote.get("freshness_status") or quote.get("freshness") or ""
    ).strip().lower()
    if freshness not in {"fresh", "ok"}:
        return None
    raw_cutoff = (
        quote.get("data_cutoff")
        or quote.get("quote_timestamp")
        or quote.get("timestamp")
    )
    parsed = parse_aware_market_timestamp(raw_cutoff)
    if parsed is None or not is_recent_market_cutoff(
        parsed,
        now=now,
        max_age_seconds=max_age_seconds,
    ):
        return None
    return parsed, freshness


def aggregate_market_quote_truth(
    quotes,
    expected_codes: Sequence[str],
    *,
    default_provider: str,
    now: datetime,
    max_age_seconds: int = MARKET_DATA_MAX_AGE_SECONDS,
) -> dict:
    """Validate quotes and report exact expected-versus-verified coverage."""
    verified_quotes: dict[str, Mapping] = {}
    missing_sources: list[str] = []
    rejected_sources: list[str] = []
    providers: set[str] = set()
    data_cutoffs: list[datetime] = []
    accepted_freshness: set[str] = set()

    for code in expected_codes:
        try:
            quote = quotes.get(code) if quotes is not None else None
        except Exception:
            quote = None
        if (
            isinstance(quote, Exception)
            or not isinstance(quote, Mapping)
            or not quote
        ):
            missing_sources.append(code)
            continue
        try:
            price = float(quote.get("price") or 0)
        except (TypeError, ValueError):
            price = 0
        quote_truth = fresh_market_quote_truth(
            quote,
            now=now,
            max_age_seconds=max_age_seconds,
        )
        if not math.isfinite(price) or price <= 0 or quote_truth is None:
            rejected_sources.append(code)
            continue
        cutoff, freshness = quote_truth
        verified_quotes[code] = quote
        providers.add(str(quote.get("source") or default_provider))
        data_cutoffs.append(cutoff)
        accepted_freshness.add(freshness)

    if verified_quotes and (missing_sources or rejected_sources):
        source_status = "degraded"
        freshness_status = "degraded"
        source_error = "partial_market_coverage"
    elif verified_quotes:
        source_status = "ok"
        freshness_status = (
            "fresh" if accepted_freshness == {"fresh"} else "ok"
        )
        source_error = ""
    else:
        source_status = "failed"
        freshness_status = "failed"
        source_error = "fresh_market_indices_unavailable"

    earliest_cutoff = min(data_cutoffs) if data_cutoffs else None
    return {
        "quotes": verified_quotes,
        "market_source_status": {
            "status": source_status,
            "provider": (
                "+".join(sorted(providers)) if providers else default_provider
            ),
            "data_cutoff": (
                earliest_cutoff.isoformat() if earliest_cutoff else None
            ),
            "freshness_status": freshness_status,
            "error": source_error,
            "missing_sources": missing_sources,
            "rejected_sources": rejected_sources,
            "coverage": {
                "expected": len(expected_codes),
                "verified": len(verified_quotes),
            },
        },
    }
