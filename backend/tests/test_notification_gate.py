from datetime import datetime, timedelta
import json

from app.services.notification_gate import NotificationGate, build_alert_digest


def test_notification_gate_deduplicates_same_event_within_cooldown(tmp_path):
    gate = NotificationGate(tmp_path / "notification_state.json")
    alert = {
        "stock_code": "000725",
        "stock_name": "京东方A",
        "action": "actionable",
        "playbook": "breakout_entry",
        "message": "breakout_entry 触发",
    }

    assert gate.filter_alerts([alert], stage="午后") == [alert]
    assert gate.filter_alerts([alert], stage="午后") == []


def test_notification_gate_allows_after_cooldown(tmp_path):
    path = tmp_path / "notification_state.json"
    old = datetime.now() - timedelta(minutes=31)
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "updated_at": old.isoformat(timespec="seconds"),
                "events": {
                    "午后:000725:actionable:breakout_entry": {
                        "last_sent_at": old.isoformat(timespec="seconds"),
                        "stock_code": "000725",
                        "action": "actionable",
                        "stage": "午后",
                    }
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    gate = NotificationGate(path)
    alert = {
        "stock_code": "000725",
        "stock_name": "京东方A",
        "action": "actionable",
        "playbook": "breakout_entry",
        "message": "breakout_entry 触发",
    }

    assert gate.filter_alerts([alert], stage="午后") == [alert]


def test_notification_gate_recovers_from_malformed_events_state(tmp_path):
    path = tmp_path / "notification_state.json"
    path.write_text('{"version": 1, "events": []}', encoding="utf-8")
    gate = NotificationGate(path)
    alert = {
        "stock_code": "000725",
        "stock_name": "京东方A",
        "action": "actionable",
        "playbook": "breakout_entry",
        "message": "breakout_entry 触发",
    }

    assert gate.filter_alerts([alert], stage="午后") == [alert]
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload["events"], dict)


def test_build_alert_digest_aggregates_multiple_alerts():
    digest = build_alert_digest(
        [
            {
                "stock_code": "000725",
                "stock_name": "京东方A",
                "action": "actionable",
                "message": "建议300股",
                "suggestion": "人工复核后可试仓",
            },
            {
                "stock_code": "000100",
                "stock_name": "TCL科技",
                "action": "risk_budget_too_small",
                "message": "一手风险超过预算",
            },
        ],
        title="候选池提醒",
    )

    assert "候选池提醒" in digest
    assert "京东方A(000725)" in digest
    assert "TCL科技(000100)" in digest
