"""Realtime market data facade: Tencent quote + fastest Scrapling K-line."""
from __future__ import annotations

from typing import Any

from app.data_sources.realtime_kline_scraper import ScraplingRealtimeKlineSource
from app.data_sources.tencent_client import TencentDataSource


class FastRealtimeMarketDataSource:
    """Keep Tencent realtime quotes, but fetch K-lines from the fastest scraper."""

    def __init__(
        self,
        quote_source: Any | None = None,
        kline_source: Any | None = None,
        fallback_kline_source: Any | None = None,
        offline_kline_source: Any | None = None,
    ):
        self.quote_source = quote_source or TencentDataSource()
        self.kline_source = kline_source or ScraplingRealtimeKlineSource()
        self.fallback_kline_source = fallback_kline_source or TencentDataSource()
        self.offline_kline_source = (
            offline_kline_source
            if offline_kline_source is not None
            else self._default_offline_source()
        )
        self.name = "fast_realtime_market_data"

    @staticmethod
    def _default_offline_source() -> Any | None:
        try:
            from app.data_sources.offline_market_data import OfflineMinuteDataSource

            source = OfflineMinuteDataSource.from_default_registry()
            return source if source.is_available() else None
        except (OSError, ValueError):
            return None

    async def fetch(self, stock_code: str) -> dict[str, Any] | None:
        return await self.quote_source.fetch(stock_code)

    async def fetch_batch(self, codes: list[str]) -> dict[str, dict[str, Any]]:
        return await self.quote_source.fetch_batch(codes)

    @staticmethod
    async def _fetch_kline_or_empty(
        source: Any,
        stock_code: str,
        *,
        period: str,
        count: int,
    ) -> dict[str, Any]:
        try:
            result = await source.fetch_kline(
                stock_code,
                period=period,
                count=count,
            )
        except Exception as exc:
            return {
                "code": stock_code,
                "period": period,
                "bars": [],
                "status": "error",
                "error": str(exc),
            }
        return result if isinstance(result, dict) else {"bars": []}

    async def fetch_kline(self, stock_code: str, period: str = "day", count: int = 120) -> dict[str, Any]:
        result = await self._fetch_kline_or_empty(
            self.kline_source,
            stock_code,
            period=period,
            count=count,
        )
        if result.get("bars"):
            return result
        fallback = await self._fetch_kline_or_empty(
            self.fallback_kline_source,
            stock_code,
            period=period,
            count=count,
        )
        if fallback.get("bars"):
            fallback["source"] = f"{fallback.get('source', 'fallback')}+fallback_after_scrapling"
            return fallback
        if self.offline_kline_source is None:
            return fallback
        offline = await self._fetch_kline_or_empty(
            self.offline_kline_source,
            stock_code,
            period=period,
            count=count,
        )
        if offline.get("bars"):
            return {
                **offline,
                "source": "offline_minute_archive+historical_fallback",
                "freshness_status": "historical_only",
                "history_only": True,
            }
        return fallback
