import json
from pathlib import Path

from app.services.long_thesis import LongThesisStore, evaluate_thesis_status


def _sample_thesis():
    return {
        "symbol": "002123",
        "name": "测试长期标的",
        "horizon": "6-36m",
        "core_thesis": "处于明确产业链瓶颈，财务验证窗口在未来两个季度。",
        "assumptions": [
            {
                "id": "a1",
                "claim": "收入增速连续两个季度改善",
                "verification": "quarterly_financial",
                "frequency": "quarterly",
                "status": "verified",
            },
            {
                "id": "a2",
                "claim": "毛利率不低于30%",
                "verification": "quarterly_financial",
                "frequency": "quarterly",
                "status": "weakened",
            },
        ],
        "red_lines": [
            {
                "id": "r1",
                "condition": "核心订单被证伪",
                "severity": "critical",
                "status": "clear",
                "action": "exit_review",
            }
        ],
        "valuation_anchor": {
            "fair_zone": [4.0, 5.2],
            "accumulation_zone": [3.2, 3.8],
            "overpriced_zone": [6.5, 99],
            "method": "pe_ps_fcf_cross_check",
        },
        "quality_score": 76,
        "confidence": "B",
        "evidence_ids": ["ev_test"],
    }


def test_long_thesis_store_upserts_and_appends_review(tmp_path):
    store = LongThesisStore(tmp_path / "long_thesis.json")

    stored = store.upsert(_sample_thesis())
    store.append_review(
        "002123",
        {
            "date": "2026-07-06",
            "status": "weakened",
            "summary": "毛利率假设边际弱化，继续观察。",
        },
    )

    loaded = store.get("002123")
    assert stored["symbol"] == "002123"
    assert loaded["thesis_status"] == "weakened"
    assert loaded["reviews"][0]["summary"] == "毛利率假设边际弱化，继续观察。"
    assert store.load()["items"]["002123"]["valuation_anchor"]["accumulation_zone"] == [3.2, 3.8]


def test_evaluate_thesis_status_marks_red_line_as_broken():
    thesis = _sample_thesis()
    thesis["red_lines"][0]["status"] = "triggered"

    result = evaluate_thesis_status(thesis)

    assert result["status"] == "broken"
    assert result["red_line_status"] == "triggered"
    assert result["reason"] == "核心订单被证伪"


def test_evaluate_thesis_status_marks_stale_without_recent_review():
    thesis = _sample_thesis()
    thesis["updated_at"] = "2026-01-01 00:00:00"

    result = evaluate_thesis_status(thesis, as_of="2026-07-06", stale_after_days=90)

    assert result["status"] == "stale"
    assert result["red_line_status"] == "clear"


def test_long_thesis_example_is_store_compatible(tmp_path):
    example_path = Path("data/examples/long_thesis.example.json")
    thesis = json.loads(example_path.read_text(encoding="utf-8"))
    store = LongThesisStore(tmp_path / "long_thesis.json")

    stored = store.upsert(thesis)

    assert stored["symbol"] == thesis["symbol"]
    assert stored["assumptions"]
    assert stored["red_lines"]
    assert stored["valuation_anchor"]["method"]
