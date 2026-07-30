from app.services.target_lifecycle_events import classify_lifecycle_event


def test_new_target_is_reported_as_added_with_score():
    event = classify_lifecycle_event(
        previous=None,
        current={
            "code": "000001",
            "name": "平安银行",
            "status": "watching",
            "score": 76,
            "decision_reason": "资金流和量价同步改善。",
        },
    )

    assert event["event"] == "added"
    assert event["new_score"] == 76
    assert "资金流" in event["reason"]


def test_lifecycle_event_explains_downgrade_and_removal():
    downgraded = classify_lifecycle_event(
        previous={"status": "executable", "score": 82},
        current={
            "status": "watching",
            "score": 68,
            "block_reason": "fund_flow_reversed",
        },
    )
    removed = classify_lifecycle_event(
        previous={"status": "watching", "score": 76},
        current={
            "status": "removed",
            "score": 52,
            "block_reason": "fund_flow_reversed",
        },
    )

    assert downgraded["event"] == "downgraded"
    assert "资金" in downgraded["reason"]
    assert removed["event"] == "removed"
    assert "资金" in removed["reason"]


def test_unchanged_target_is_retained_without_generic_reason():
    event = classify_lifecycle_event(
        previous={"status": "watching", "score": 72},
        current={
            "status": "watching",
            "score": 73,
            "decision_reason": "趋势和资金结构保持。",
        },
    )

    assert event["event"] == "retained"
    assert event["reason"] == "趋势和资金结构保持。"
