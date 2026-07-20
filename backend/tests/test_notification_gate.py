from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
import json
from threading import Barrier

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


def test_notification_gate_deduplicates_same_signal_concurrently(tmp_path):
    path = tmp_path / "notification_state.json"
    alert = {
        "stock_code": "002123",
        "action": "actionable",
        "playbook": "breakout_entry",
        "signal_id": "same-concurrent-signal",
    }
    worker_count = 16
    start = Barrier(worker_count)

    def deliver_once():
        start.wait()
        return NotificationGate(path).filter_alerts([alert], stage="盘中")

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        results = list(executor.map(lambda _: deliver_once(), range(worker_count)))

    assert sum(bool(result) for result in results) == 1
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert list(payload["events"]) == ["signal:same-concurrent-signal:actionable"]


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


def test_notification_gate_recovers_from_malformed_individual_event(tmp_path):
    path = tmp_path / "notification_state.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "events": {"signal:signal-one:actionable": "dirty-event"},
            }
        ),
        encoding="utf-8",
    )
    gate = NotificationGate(path)
    alert = {
        "stock_code": "002123",
        "action": "actionable",
        "signal_id": "signal-one",
    }

    assert gate.filter_alerts([alert], stage="盘中") == [alert]
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload["events"]["signal:signal-one:actionable"], dict)


def test_notification_gate_compares_aware_last_sent_time_without_crashing(tmp_path):
    path = tmp_path / "notification_state.json"
    recent = datetime.now().astimezone()
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "events": {
                    "signal:signal-one:actionable": {
                        "last_sent_at": recent.isoformat(timespec="seconds")
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    gate = NotificationGate(path)
    alert = {
        "stock_code": "002123",
        "action": "actionable",
        "signal_id": "signal-one",
    }

    assert gate.filter_alerts([alert], stage="盘中") == []


def test_notification_gate_delivers_entry_cancellation_after_actionable(tmp_path):
    gate = NotificationGate(tmp_path / "notification_state.json")
    actionable = {
        "stock_code": "002123",
        "stock_name": "低价突破",
        "action": "actionable",
        "playbook": "breakout_entry",
        "signal_id": "same-signal",
        "message": "连续确认后触发",
    }
    cancelled = {
        "stock_code": "002123",
        "stock_name": "低价突破",
        "action": "entry_cancelled",
        "playbook": "breakout_entry",
        "signal_id": "same-signal",
        "message": "买入信号已失效",
    }

    assert gate.filter_alerts([actionable], stage="盘中") == [actionable]
    assert gate.filter_alerts([cancelled], stage="盘中") == [cancelled]


def test_notification_gate_deduplicates_same_signal_across_stages(tmp_path):
    gate = NotificationGate(tmp_path / "notification_state.json")
    alert = {
        "stock_code": "002123",
        "action": "actionable",
        "playbook": "breakout_entry",
        "signal_id": "same-signal",
    }

    assert gate.filter_alerts([alert], stage="早盘") == [alert]
    assert gate.filter_alerts([alert], stage="午后") == []


def test_notification_gate_prefers_signal_id_for_distinct_authorizations(tmp_path):
    gate = NotificationGate(tmp_path / "notification_state.json")
    first = {
        "stock_code": "002123",
        "action": "actionable",
        "playbook": "breakout_entry",
        "signal_id": "signal-one",
    }
    second = {**first, "signal_id": "signal-two"}

    assert gate.filter_alerts([first], stage="盘中") == [first]
    assert gate.filter_alerts([second], stage="盘中") == [second]


def test_notification_gate_does_not_swallow_distinct_entry_cancellations(tmp_path):
    gate = NotificationGate(tmp_path / "notification_state.json")
    first = {
        "stock_code": "002123",
        "action": "entry_cancelled",
        "playbook": "breakout_entry",
        "signal_id": "signal-one",
    }
    second = {**first, "signal_id": "signal-two"}

    assert gate.filter_alerts([first], stage="盘中") == [first]
    assert gate.filter_alerts([second], stage="盘中") == [second]


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
