"""Fast real-time K-line source backed by Scrapling and Eastmoney push2his."""
from __future__ import annotations

from time import monotonic
from typing import Any

from app.data_sources.base import BaseDataSource


def _market_prefix(code: str) -> str:
    clean = str(code or "").replace("sh", "").replace("sz", "").replace("bj", "")
    return "1" if clean.startswith(("6", "9")) else "0"


def _klt(period: str) -> str:
    normalized = str(period or "day").lower()
    return {
        "day": "101",
        "daily": "101",
        "d": "101",
        "week": "102",
        "month": "103",
        "1": "1",
        "1m": "1",
        "m1": "1",
        "5": "5",
        "5m": "5",
        "m5": "5",
        "15": "15",
        "15m": "15",
        "m15": "15",
        "30": "30",
        "30m": "30",
        "m30": "30",
        "60": "60",
        "60m": "60",
        "m60": "60",
    }.get(normalized, normalized)


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


class ScraplingRealtimeKlineSource(BaseDataSource):
    """Fetch Eastmoney K-line JSON through Scrapling.

    Local benchmark on 2026-07-10 showed Eastmoney push2his 1m K-line was the
    fastest reachable real-time K-line endpoint from this machine.
    """

    def __init__(self, *, failure_threshold: int = 3, cooldown_seconds: float = 300.0):
        super().__init__("eastmoney_scrapling_kline")
        self.failure_threshold = max(1, int(failure_threshold))
        self.cooldown_seconds = max(0.0, float(cooldown_seconds))
        self._consecutive_errors = 0
        self._circuit_open_until = 0.0

    def is_available(self) -> bool:
        try:
            from scrapling.fetchers import Fetcher  # noqa: F401
        except Exception:
            return False
        return True

    async def fetch(self, stock_code: str) -> dict[str, Any] | None:
        return await self.fetch_kline(stock_code, period="1", count=5)

    async def fetch_kline(self, stock_code: str, period: str = "1", count: int = 120) -> dict[str, Any]:
        clean = str(stock_code or "").replace("sh", "").replace("sz", "").replace("bj", "")
        if not clean:
            return {"code": stock_code, "period": period, "bars": [], "source": self.name}
        now = monotonic()
        if self._circuit_open_until > now:
            return {
                "code": clean,
                "period": period,
                "bars": [],
                "source": self.name,
                "status": "circuit_open",
                "reason": "recent_vendor_failures",
            }
        if not self.is_available():
            return {
                "code": clean,
                "period": period,
                "bars": [],
                "source": self.name,
                "status": "unavailable",
                "reason": "scrapling_not_installed",
            }
        try:
            from scrapling.fetchers import Fetcher

            url = self._url(clean, period=period, count=count)
            response = Fetcher.get(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "Referer": "https://quote.eastmoney.com/",
                },
                timeout=8,
            )
            payload = response.json()
            bars = self._parse_bars((payload.get("data") or {}).get("klines") or [])
            self._consecutive_errors = 0
            self._circuit_open_until = 0.0
            return {
                "code": clean,
                "period": period,
                "bars": bars[-count:] if len(bars) > count else bars,
                "source": self.name,
                "vendor": "eastmoney_push2his",
                "status": "ok" if bars else "empty",
            }
        except Exception as exc:
            self._consecutive_errors += 1
            if self._consecutive_errors >= self.failure_threshold:
                self._circuit_open_until = monotonic() + self.cooldown_seconds
            return {
                "code": clean,
                "period": period,
                "bars": [],
                "source": self.name,
                "status": "error",
                "error": str(exc),
            }

    @staticmethod
    def _url(code: str, *, period: str, count: int) -> str:
        secid = f"{_market_prefix(code)}.{code}"
        return (
            "https://push2his.eastmoney.com/api/qt/stock/kline/get"
            f"?secid={secid}"
            "&fields1=f1,f2,f3,f4,f5,f6"
            "&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"
            f"&klt={_klt(period)}&fqt=1&beg=0&end=20500101&lmt={int(count)}"
        )

    @staticmethod
    def _parse_bars(klines: list[str]) -> list[dict[str, Any]]:
        bars: list[dict[str, Any]] = []
        for line in klines:
            parts = str(line).split(",")
            if len(parts) < 6:
                continue
            bars.append({
                "date": parts[0],
                "open": _to_float(parts[1]),
                "close": _to_float(parts[2]),
                "high": _to_float(parts[3]),
                "low": _to_float(parts[4]),
                "volume": _to_float(parts[5]),
                "amount": _to_float(parts[6]) if len(parts) > 6 else 0.0,
                "amplitude_pct": _to_float(parts[7]) if len(parts) > 7 else 0.0,
                "change_pct": _to_float(parts[8]) if len(parts) > 8 else 0.0,
                "change_amt": _to_float(parts[9]) if len(parts) > 9 else 0.0,
                "turnover_pct": _to_float(parts[10]) if len(parts) > 10 else 0.0,
            })
        return bars
