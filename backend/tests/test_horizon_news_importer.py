"""Tests for importing Horizon TusharePro news into Sentinel input format."""

import json


def test_import_horizon_news_deduplicates_and_enriches_events(tmp_path):
    from app.data_sources.horizon_news_importer import import_horizon_news_events

    raw_dir = tmp_path / "raw" / "2026-06-27"
    raw_dir.mkdir(parents=True)
    event = {
        "id": "cls-20260627-0933-0001",
        "source": "财联社",
        "channel": "公司",
        "published_at": "2026-06-27T09:33:00+08:00",
        "fetched_at": "2026-06-27T09:35:00+08:00",
        "content": "许继电气披露电网设备订单进展。",
        "is_key": True,
        "symbols": [],
        "themes": [],
        "dedupe_key": "same-news",
        "raw_hash": "raw-1",
    }
    (raw_dir / "2026-06-27_cls.jsonl").write_text(
        json.dumps(event, ensure_ascii=False) + "\n" + json.dumps(event, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    events = import_horizon_news_events(tmp_path, "2026-06-27")

    assert len(events) == 1
    assert events[0]["evidence_status"] == "enriched"
    assert events[0]["ingested_at"].endswith("+08:00")
    assert "000400.SZ" in events[0]["symbols"]
    assert "电网设备" in events[0]["themes"]


def test_import_horizon_news_rejects_secret_fields(tmp_path):
    from app.data_sources.horizon_news_importer import import_horizon_news_events

    raw_dir = tmp_path / "raw" / "2026-06-27"
    raw_dir.mkdir(parents=True)
    (raw_dir / "2026-06-27_xq.jsonl").write_text(
        json.dumps(
            {
                "id": "xq-secret",
                "source": "雪球",
                "channel": "市场",
                "published_at": "2026-06-27T09:33:00+08:00",
                "fetched_at": "2026-06-27T09:35:00+08:00",
                "content": "测试新闻",
                "is_key": False,
                "symbols": [],
                "themes": [],
                "dedupe_key": "secret-news",
                "raw_hash": "raw-2",
                "token": "must-not-pass",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    events = import_horizon_news_events(tmp_path, "2026-06-27")

    assert events == []


def test_write_sentinel_news_events_outputs_jsonl(tmp_path):
    from app.data_sources.horizon_news_importer import write_sentinel_news_events

    output_path = tmp_path / "bundle" / "news_events.jsonl"
    events = [
        {
            "id": "cls-1",
            "source": "财联社",
            "channel": "公司",
            "published_at": "2026-06-27T09:33:00+08:00",
            "fetched_at": "2026-06-27T09:35:00+08:00",
            "ingested_at": "2026-06-27T09:36:00+08:00",
            "content": "测试新闻",
            "is_key": True,
            "symbols": ["000400.SZ"],
            "themes": ["电网设备"],
            "dedupe_key": "cls-1",
            "raw_hash": "raw-1",
            "evidence_status": "enriched",
        }
    ]

    write_sentinel_news_events(events, output_path)

    lines = output_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["id"] == "cls-1"
