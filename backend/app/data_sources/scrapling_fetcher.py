"""Optional Scrapling-backed web fetcher for evidence collection."""
from __future__ import annotations

from datetime import datetime
from typing import Any


def scrapling_available() -> bool:
    try:
        import scrapling  # noqa: F401
    except Exception:
        return False
    return True


def fetch_page_text(url: str, *, selector: str = "body", timeout: int = 20) -> dict[str, Any]:
    """Fetch selected page text with Scrapling when installed.

    This is an evidence collection adapter. It intentionally does not convert
    scraped content into trading actions; downstream code must normalize and
    score evidence before it can affect a strategy.
    """
    fetched_at = datetime.now().isoformat(timespec="seconds")
    if not scrapling_available():
        return {
            "status": "unavailable",
            "source": "scrapling",
            "url": url,
            "selector": selector,
            "fetched_at": fetched_at,
            "reason": "scrapling_not_installed",
        }
    try:
        from scrapling.fetchers import Fetcher

        page = Fetcher.get(url, timeout=timeout)
        matches = page.css(selector)
        text = "\n".join(item.get(default="").strip() for item in matches if item.get(default="").strip())
        return {
            "status": "ok" if text else "empty",
            "source": "scrapling",
            "url": url,
            "selector": selector,
            "fetched_at": fetched_at,
            "text": text,
        }
    except Exception as exc:
        return {
            "status": "error",
            "source": "scrapling",
            "url": url,
            "selector": selector,
            "fetched_at": fetched_at,
            "error": str(exc),
        }
