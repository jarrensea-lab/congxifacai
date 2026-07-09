"""Realtime market data facade: Tencent quote + fastest Scrapling K-line."""
from __future__ import annotations

from typing import Any

from app.data_sources.realtime_kline_scraper import ScraplingRealtimeKlineSource
from app.data_sources.tencent_client import TencentDataSource


class FastRealtimeMarketDataSource:
    """Keep Tencent realtime quotes, but fetch K-lines from the fastest scraper."""

    def __init__(self, quote_source: Any | None = None, kline_source: Any | None = None, fallback_kline_source: Any | None = None):
        self.quote_source = quote_source or TencentDataSource()
        self.kline_source = kline_source or ScraplingRealtimeKlineSource()
        self.fallback_kline_source = fallback_kline_source or TencentDataSource()
        self.name = "fast_realtime_market_data"

    async def fetch(self, stock_code: str) -> dict[str, Any] | None:
        return await self.quote_source.fetch(stock_code)

    async def fetch_batch(self, codes: list[str]) -> dict[str, dict[str, Any]]:
        return await self.quote_source.fetch_batch(codes)

    async def fetch_kline(self, stock_code: str, period: str = "day", count: int = 120) -> dict[str, Any]:
        result = await self.kline_source.fetch_kline(stock_code, period=period, count=count)
        if result.get("bars"):
            return result
        fallback = await self.fallback_kline_source.fetch_kline(stock_code, period=period, count=count)
        if fallback.get("bars"):
            fallback["source"] = f"{fallback.get('source', 'fallback')}+fallback_after_scrapling"
        return fallback
