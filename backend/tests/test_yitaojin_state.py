from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

import pytest


def _state():
    from app.integrations.yitaojin.state import WatchlistState

    return WatchlistState(
        account_fingerprint="sha256:" + "b" * 64,
        manual_protected_codes=("000001",),
        managed_codes=("600000",),
        pending_removal_counts={"600000": 1},
        successful_apply_count=3,
        last_success_at="2026-07-26T20:45:00+08:00",
        last_snapshot_hash="sha256:" + "c" * 64,
    )


def test_state_store_returns_empty_versioned_state_when_file_is_missing(tmp_path):
    """Catches first-run bootstrap depending on a pre-created state file."""
    from app.integrations.yitaojin.state import YitaojinStateStore

    state = YitaojinStateStore(tmp_path / "watchlist.json").load()

    assert state.schema_version == 1
    assert state.manual_protected_codes == ()
    assert state.managed_codes == ()
    assert state.pending_removal_counts == {}
    assert not (tmp_path / "watchlist.json").exists()


def test_state_store_round_trips_atomically(tmp_path):
    """Catches ownership or removal counters being lost during persistence."""
    from app.integrations.yitaojin.state import YitaojinStateStore

    path = tmp_path / "nested" / "watchlist.json"
    store = YitaojinStateStore(path)

    store.save(_state())
    loaded = store.load()

    assert loaded == _state()
    assert json.loads(path.read_text(encoding="utf-8"))["schema_version"] == 1
    assert not list(path.parent.glob("*.tmp"))


@pytest.mark.parametrize(
    "payload",
    [
        "{broken",
        '{"schema_version": 99}',
    ],
)
def test_state_store_rejects_corrupt_or_unknown_schema_without_overwrite(
    tmp_path,
    payload,
):
    """Catches state corruption resetting ownership and enabling unsafe deletes."""
    from app.integrations.yitaojin.state import StateStoreError, YitaojinStateStore

    path = tmp_path / "watchlist.json"
    path.write_text(payload, encoding="utf-8")
    store = YitaojinStateStore(path)

    with pytest.raises(StateStoreError):
        store.load()

    assert path.read_text(encoding="utf-8") == payload


def test_state_rejects_raw_or_malformed_account_identity():
    """Catches a raw broker account identifier entering ownership state."""
    from app.integrations.yitaojin.state import StateStoreError, WatchlistState

    with pytest.raises(StateStoreError, match="account_fingerprint"):
        WatchlistState(account_fingerprint="raw-account-value")


def test_audit_log_is_append_only_and_concurrency_safe(tmp_path):
    """Catches interleaved writers producing truncated JSONL audit records."""
    from app.integrations.yitaojin.state import YitaojinStateStore

    audit_path = tmp_path / "audit.jsonl"
    store = YitaojinStateStore(
        tmp_path / "watchlist.json",
        audit_path=audit_path,
    )

    def append(index: int) -> None:
        store.append_audit(
            {
                "run_id": f"run-{index}",
                "mode": "dry-run",
                "input_hash": f"hash-{index}",
                "plan": {"add": [], "remove": []},
                "result": {"ok": True},
            }
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(append, range(20)))

    records = [
        json.loads(line)
        for line in audit_path.read_text(encoding="utf-8").splitlines()
    ]
    assert len(records) == 20
    assert {record["run_id"] for record in records} == {
        f"run-{index}" for index in range(20)
    }
    assert all(record["recorded_at"] for record in records)


def test_audit_log_rejects_sensitive_account_fields(tmp_path):
    """Catches raw account identity entering the durable audit trail."""
    from app.integrations.yitaojin.state import StateStoreError, YitaojinStateStore

    store = YitaojinStateStore(
        tmp_path / "watchlist.json",
        audit_path=tmp_path / "audit.jsonl",
    )

    with pytest.raises(StateStoreError, match="sensitive"):
        store.append_audit(
            {
                "run_id": "run-1",
                "mode": "dry-run",
                "input_hash": "hash-1",
                "plan": {},
                "result": {"accountNumber": "forbidden"},
            }
        )
