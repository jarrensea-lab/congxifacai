from app.services.report_validation import validate_report


def test_report_validation_accepts_actionable_v9_contract():
    content = """
## 今日可操作结论
今日结论：可操作 1 只
账户：可用现金 ¥800.00；现金安全垫 ¥160.00；单票预算 ¥640.00
### 可操作标的
平安银行(000001)
最大计划亏损：¥30.00
### 今日变化
#### 今日新增
平安银行(000001)
### 管线状态
完整
""".strip()

    result = validate_report(
        content,
        pipeline_result={
            "metrics": {"scored_count": 1},
            "scorecards": [{"code": "000001", "action": "buy"}],
            "lifecycle_events": [{"code": "000001", "event": "added"}],
        },
    )

    assert result == {"ok": True, "errors": [], "warnings": []}


def test_report_validation_rejects_hidden_candidates():
    result = validate_report(
        "## 今日可操作结论\n今日结论：不买\n短线池：暂无\n### 管线状态\n完整",
        pipeline_result={
            "metrics": {"scored_count": 5},
            "scorecards": [],
            "lifecycle_events": [{"code": "000001", "event": "added"}],
        },
    )

    assert result["ok"] is False
    assert "lifecycle_change_missing" in result["errors"]
    assert "no_action_reason_missing" in result["errors"]


def test_report_validation_rejects_more_than_three_action_cards():
    content = """
## 今日可操作结论
今日结论：可操作 4 只
账户：可用现金 ¥8,000.00
### 可操作标的
最大计划亏损：¥10.00
最大计划亏损：¥10.00
最大计划亏损：¥10.00
最大计划亏损：¥10.00
### 今日变化
无新增、降级或剔除。
### 管线状态
完整
""".strip()

    result = validate_report(
        content,
        pipeline_result={
            "metrics": {"scored_count": 4},
            "scorecards": [
                {"code": str(index), "action": "buy"}
                for index in range(4)
            ],
            "lifecycle_events": [],
        },
    )

    assert result["ok"] is False
    assert "too_many_action_cards" in result["errors"]
