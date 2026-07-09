from app.data_sources import scrapling_fetcher


def test_scrapling_fetcher_reports_unavailable_when_not_installed(monkeypatch):
    monkeypatch.setattr(scrapling_fetcher, "scrapling_available", lambda: False)

    result = scrapling_fetcher.fetch_page_text("https://example.com", selector="body")

    assert result["status"] == "unavailable"
    assert result["source"] == "scrapling"
    assert result["reason"] == "scrapling_not_installed"
    assert "url" in result
