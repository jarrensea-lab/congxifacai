"""Explicit non-stock market instruments allowed by quote providers."""
from __future__ import annotations

from typing import Any

from app.utils.a_share_codes import tencent_symbol


TENCENT_MARKET_INDEX_SYMBOLS = frozenset({
    "sh000001",
    "sz399001",
    "sz399006",
})


def resolve_tencent_market_instrument(value: Any) -> str:
    """Resolve a strict A-share stock or an explicitly allowlisted index."""
    raw = str(value or "").strip().lower()
    if raw in TENCENT_MARKET_INDEX_SYMBOLS:
        return raw
    return tencent_symbol(value)
