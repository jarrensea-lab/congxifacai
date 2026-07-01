"""Structured per-target data snapshots for strategy scoring."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Awaitable, Callable


def _ok(**payload: Any) -> dict[str, Any]:
    return {"status": "ok", **payload}


def _missing(reason: str, **payload: Any) -> dict[str, Any]:
    return {"status": "missing", "reason": reason, **payload}


async def _call(coro: Awaitable[Any], fallback: Any) -> Any:
    try:
        return await coro
    except Exception as exc:  # pragma: no cover - defensive boundary for flaky data vendors
        return {"__error__": str(exc), "__fallback__": fallback}


def _extract_error(payload: Any) -> str | None:
    if isinstance(payload, dict) and "__error__" in payload:
        return str(payload["__error__"])
    return None


def _match_by_code(rows: Any, code: str) -> dict[str, Any] | None:
    if not isinstance(rows, list):
        return None
    for row in rows:
        if isinstance(row, dict) and str(row.get("code", "")).strip() == code:
            return row
    return None


async def build_target_snapshot(
    code: str,
    *,
    name: str = "",
    quote_source: Any = None,
    market_source: Any = None,
    news_source: Any = None,
    financial_fetcher: Callable[[list[str]], Awaitable[dict[str, Any]]] | None = None,
    sentinel: dict[str, Any] | None = None,
    serenity: dict[str, Any] | None = None,
    kline_count: int = 120,
) -> dict[str, Any]:
    """Collect one normalized snapshot from quote, market, news, financial, and research inputs."""
    clean_code = str(code or "").strip()
    snapshot: dict[str, Any] = {
        "code": clean_code,
        "name": name or clean_code,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }

    quote_payload: dict[str, Any] | None = None
    if quote_source is None:
        snapshot["quote"] = _missing("quote_source_missing")
    elif hasattr(quote_source, "fetch_batch"):
        quotes = await _call(quote_source.fetch_batch([clean_code]), {})
        error = _extract_error(quotes)
        if error:
            snapshot["quote"] = _missing("quote_fetch_failed", error=error)
        else:
            quote_payload = (quotes or {}).get(clean_code) if isinstance(quotes, dict) else None
            snapshot["quote"] = _ok(**quote_payload) if isinstance(quote_payload, dict) else _missing("quote_not_found")
    else:
        snapshot["quote"] = _missing("quote_fetch_batch_unavailable")

    if quote_source is None or not hasattr(quote_source, "fetch_kline"):
        snapshot["kline"] = _missing("kline_source_missing")
    else:
        kline = await _call(quote_source.fetch_kline(clean_code, count=kline_count), {})
        error = _extract_error(kline)
        if error:
            snapshot["kline"] = _missing("kline_fetch_failed", error=error)
        elif isinstance(kline, dict) and kline.get("bars"):
            snapshot["kline"] = _ok(**kline)
        else:
            snapshot["kline"] = _missing("kline_not_found")

    if market_source is None:
        snapshot["fund_flow"] = _missing("market_source_missing")
        snapshot["northbound"] = _missing("market_source_missing")
    else:
        if hasattr(market_source, "fetch_fund_flow_individual"):
            flows = await _call(market_source.fetch_fund_flow_individual(), [])
            error = _extract_error(flows)
            matched_flow = _match_by_code(flows, clean_code)
            if error:
                snapshot["fund_flow"] = _missing("fund_flow_fetch_failed", error=error)
            elif matched_flow:
                snapshot["fund_flow"] = _ok(**matched_flow)
            else:
                snapshot["fund_flow"] = _missing("fund_flow_not_found")
        else:
            snapshot["fund_flow"] = _missing("fund_flow_method_missing")

        if hasattr(market_source, "fetch_hsgt_flow"):
            northbound = await _call(market_source.fetch_hsgt_flow(), [])
            error = _extract_error(northbound)
            if error:
                snapshot["northbound"] = _missing("northbound_fetch_failed", error=error)
            elif northbound:
                snapshot["northbound"] = _ok(rows=northbound)
            else:
                snapshot["northbound"] = _missing("northbound_not_found")
        else:
            snapshot["northbound"] = _missing("northbound_method_missing")

    if news_source is None or not hasattr(news_source, "fetch_stock_news"):
        snapshot["news"] = _missing("news_source_missing")
    else:
        news = await _call(news_source.fetch_stock_news(clean_code, limit=5), [])
        error = _extract_error(news)
        if error:
            snapshot["news"] = _missing("news_fetch_failed", error=error)
        elif news:
            snapshot["news"] = _ok(items=news)
        else:
            snapshot["news"] = _missing("news_not_found")

    if financial_fetcher is None:
        snapshot["financial"] = _missing("financial_fetcher_missing")
    else:
        financial = await _call(financial_fetcher([clean_code]), {})
        error = _extract_error(financial)
        item = financial.get(clean_code) if isinstance(financial, dict) else None
        if error:
            snapshot["financial"] = _missing("financial_fetch_failed", error=error)
        elif isinstance(item, dict) and item.get("status") in {"success", "ok"}:
            cleaned = {key: value for key, value in item.items() if key != "status"}
            snapshot["financial"] = _ok(**cleaned)
        elif isinstance(item, dict):
            snapshot["financial"] = _missing("financial_not_ready", **item)
        else:
            snapshot["financial"] = _missing("financial_not_found")

    snapshot["sentinel"] = _ok(**sentinel) if isinstance(sentinel, dict) else _missing("sentinel_evidence_missing")
    snapshot["serenity"] = _ok(**serenity) if isinstance(serenity, dict) else _missing("serenity_report_missing")
    return snapshot
