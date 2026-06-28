"""Tests for Sentinel input/output contracts."""

import json


def test_validate_sentinel_input_bundle_accepts_standard_files(tmp_path):
    from app.ai.sentinel_contracts import validate_sentinel_input_bundle

    (tmp_path / "news_events.jsonl").write_text(
        json.dumps(
            {
                "id": "cls-20260627-0933-0001",
                "source": "财联社",
                "channel": "公司",
                "published_at": "2026-06-27T09:33:00+08:00",
                "fetched_at": "2026-06-27T09:35:00+08:00",
                "content": "测试新闻正文",
                "is_key": True,
                "symbols": ["000400.SZ"],
                "themes": ["AI电力"],
                "dedupe_key": "dedupe-1",
                "raw_hash": "raw-1",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "market_snapshot.json").write_text(
        json.dumps({"trade_date": "2026-06-27", "symbols": []}, ensure_ascii=False),
        encoding="utf-8",
    )
    (tmp_path / "portfolio_snapshot.json").write_text(
        json.dumps({"cash": 3085.61, "total_assets": 3085.61, "positions": []}, ensure_ascii=False),
        encoding="utf-8",
    )
    (tmp_path / "candidate_pool.json").write_text(
        json.dumps({"themes": []}, ensure_ascii=False),
        encoding="utf-8",
    )
    (tmp_path / "financial_evidence.json").write_text(
        json.dumps({"status": "unavailable", "items": []}, ensure_ascii=False),
        encoding="utf-8",
    )
    (tmp_path / "risk_context.json").write_text(
        json.dumps({"trade_allowed": False, "constraints": []}, ensure_ascii=False),
        encoding="utf-8",
    )

    result = validate_sentinel_input_bundle(tmp_path)

    assert result["valid"] is True
    assert result["missing_files"] == []
    assert result["secret_fields"] == []
    assert result["news_events_count"] == 1


def test_validate_sentinel_input_bundle_rejects_secret_fields(tmp_path):
    from app.ai.sentinel_contracts import validate_sentinel_input_bundle

    (tmp_path / "news_events.jsonl").write_text(
        json.dumps(
            {
                "id": "xq-20260627-0933-0001",
                "source": "雪球",
                "channel": "市场",
                "published_at": "2026-06-27T09:33:00+08:00",
                "fetched_at": "2026-06-27T09:35:00+08:00",
                "content": "测试新闻正文",
                "is_key": False,
                "symbols": [],
                "themes": [],
                "dedupe_key": "dedupe-1",
                "raw_hash": "raw-1",
                "cookie": "should-not-be-here",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    result = validate_sentinel_input_bundle(tmp_path)

    assert result["valid"] is False
    assert "cookie" in result["secret_fields"]


def test_validate_sentinel_output_bundle_rejects_trade_actions(tmp_path):
    from app.ai.sentinel_contracts import validate_sentinel_output_bundle

    (tmp_path / "sentinel_intraday_alerts.json").write_text(
        json.dumps(
            {
                "alerts": [
                    {
                        "event_id": "event-1",
                        "theme": "AI电力",
                        "symbols": ["000400.SZ"],
                        "action_type": "buy",
                        "reason": "不允许 Sentinel 输出交易动作",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = validate_sentinel_output_bundle(tmp_path)

    assert result["valid"] is False
    assert result["forbidden_actions"] == ["buy"]
