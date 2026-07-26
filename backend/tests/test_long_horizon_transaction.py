"""Concurrency and recovery tests for long-horizon batch transactions."""
from __future__ import annotations

import base64
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
from threading import Event, Thread

import pytest

from app.services.evidence_ledger import EvidenceLedgerStore
from app.services.long_horizon_transaction import LongHorizonBatchTransaction
from app.services.long_thesis import LongThesisStore
from app.services.quant_lifecycle import TargetPoolStore


def _process_append_and_upsert(
    ledger_path: str,
    thesis_path: str,
    transaction_lock_path: str,
    started,
    finished,
    errors,
) -> None:
    started.set()
    try:
        ledger = EvidenceLedgerStore(
            ledger_path,
            transaction_lock_path=transaction_lock_path,
        )
        thesis = LongThesisStore(
            thesis_path,
            transaction_lock_path=transaction_lock_path,
        )
        ledger.append_many([{
            "evidence_id": "ev_process_after_rollback",
            "type": "test",
            "summary": "process write",
        }])
        thesis.upsert({
            "symbol": "688002",
            "name": "进程外部写",
            "core_thesis": "rollback 后完成",
        })
    except Exception as exc:  # pragma: no cover - surfaced through the queue.
        errors.put(f"{type(exc).__name__}: {exc}")
    finally:
        finished.set()


def _process_begin_partial_write_and_crash(
    journal_path: str,
    thesis_path: str,
    ledger_path: str,
    target_path: str,
    ready,
) -> None:
    transaction = LongHorizonBatchTransaction(
        journal_path,
        [
            ("long_thesis", thesis_path),
            ("evidence_ledger", ledger_path),
            ("target_pool", target_path),
        ],
    )
    ledger = EvidenceLedgerStore(ledger_path)
    with transaction.locked():
        transaction.begin()
        ledger.append_many([{
            "evidence_id": "ev_crashed_transaction",
            "type": "test",
            "summary": "partial write before process crash",
        }])
        ready.set()
        os._exit(17)


def _transaction(
    tmp_path: Path,
    *,
    transaction_lock_path: Path | None = None,
):
    thesis_path = tmp_path / "long_thesis.json"
    ledger_path = tmp_path / "evidence_ledger.jsonl"
    target_path = tmp_path / "target_pool.json"
    transaction = LongHorizonBatchTransaction(
        tmp_path / "long_horizon_transaction.json",
        [
            ("long_thesis", thesis_path),
            ("evidence_ledger", ledger_path),
            ("target_pool", target_path),
        ],
        lock_path=transaction_lock_path,
    )
    return transaction, thesis_path, ledger_path, target_path


def _transaction_state_path(transaction: LongHorizonBatchTransaction) -> Path:
    return Path(f"{transaction.lock_path}.state")


def _store_bytes(paths):
    return {
        path: path.read_bytes() if path.exists() else None
        for path in paths
    }


def _ordinary_store_writes(
    thesis_path: Path,
    ledger_path: Path,
    target_path: Path,
    suffix: str,
):
    ledger = EvidenceLedgerStore(ledger_path)
    thesis = LongThesisStore(thesis_path)
    target = TargetPoolStore(target_path)
    return [
        lambda: ledger.append_many([{
            "evidence_id": f"ev_external_{suffix}",
            "type": "test",
            "summary": "ordinary write after crash window",
        }]),
        lambda: thesis.upsert({
            "symbol": "688001",
            "name": f"外部 thesis {suffix}",
            "core_thesis": "ordinary write after crash window",
        }),
        lambda: target.upsert_target(
            code="920001",
            name=f"外部 target {suffix}",
            status="long_research",
            source="long_horizon",
        ),
    ]


def _assert_writes_recovery_required(writes, paths, before):
    for write in writes:
        with pytest.raises(
            RuntimeError,
            match="long_horizon_recovery_required",
        ):
            write()
        assert _store_bytes(paths) == before


def test_transaction_requires_explicit_lock_for_stores_in_different_directories(
    tmp_path,
):
    with pytest.raises(
        ValueError,
        match="explicit transaction lock path required",
    ):
        LongHorizonBatchTransaction(
            tmp_path / "journal" / "pending.json",
            [
                ("long_thesis", tmp_path / "one" / "long_thesis.json"),
                ("evidence_ledger", tmp_path / "two" / "evidence.jsonl"),
                ("target_pool", tmp_path / "three" / "target.json"),
            ],
        )


def test_exclusive_transaction_blocks_thread_store_writes_until_commit(tmp_path):
    transaction, thesis_path, ledger_path, target_path = _transaction(tmp_path)
    ledger = EvidenceLedgerStore(ledger_path)
    thesis = LongThesisStore(thesis_path)
    target = TargetPoolStore(target_path)
    started = [Event(), Event(), Event()]
    finished = [Event(), Event(), Event()]

    def append_evidence():
        started[0].set()
        ledger.append_many([{
            "evidence_id": "ev_thread_after_commit",
            "type": "test",
            "summary": "thread append",
        }])
        finished[0].set()

    def upsert_thesis():
        started[1].set()
        thesis.upsert({
            "symbol": "688001",
            "name": "线程 thesis",
            "core_thesis": "commit 后完成",
        })
        finished[1].set()

    def upsert_target():
        started[2].set()
        target.upsert_target(
            code="920001",
            name="线程 target",
            status="long_research",
            source="long_horizon",
        )
        finished[2].set()

    threads = [
        Thread(target=append_evidence),
        Thread(target=upsert_thesis),
        Thread(target=upsert_target),
    ]
    blocked: list[bool] = []
    with transaction.locked():
        transaction.begin()
        for thread in threads:
            thread.start()
        assert all(event.wait(timeout=5) for event in started)
        blocked = [not event.wait(timeout=0.2) for event in finished]
        transaction.commit()
    for thread in threads:
        thread.join(timeout=5)

    assert blocked == [True, True, True]
    assert all(event.is_set() for event in finished)
    assert {item["evidence_id"] for item in ledger.load_all()} == {
        "ev_thread_after_commit"
    }
    assert thesis.get("688001")["name"] == "线程 thesis"
    assert target.get("920001")["name"] == "线程 target"


def test_exclusive_transaction_blocks_process_writes_until_rollback(tmp_path):
    lock_path = tmp_path / "explicit-long-horizon.lock"
    transaction, thesis_path, ledger_path, _ = _transaction(
        tmp_path,
        transaction_lock_path=lock_path,
    )
    ledger = EvidenceLedgerStore(
        ledger_path,
        transaction_lock_path=lock_path,
    )
    thesis = LongThesisStore(
        thesis_path,
        transaction_lock_path=lock_path,
    )
    context = multiprocessing.get_context("spawn")
    started = context.Event()
    finished = context.Event()
    errors = context.Queue()
    process = context.Process(
        target=_process_append_and_upsert,
        args=(
            str(ledger_path),
            str(thesis_path),
            str(lock_path),
            started,
            finished,
            errors,
        ),
    )

    with transaction.locked():
        transaction.begin()
        ledger.append_many([{
            "evidence_id": "ev_transaction_rolled_back",
            "type": "test",
            "summary": "must be rolled back",
        }])
        thesis.upsert({
            "symbol": "688001",
            "name": "事务内写",
            "core_thesis": "must be rolled back",
        })
        process.start()
        assert started.wait(timeout=10)
        blocked = not finished.wait(timeout=0.5)
        transaction.rollback()

    assert finished.wait(timeout=10)
    process.join(timeout=10)
    assert process.exitcode == 0
    assert errors.empty()
    assert blocked is True
    assert {item["evidence_id"] for item in ledger.load_all()} == {
        "ev_process_after_rollback"
    }
    assert thesis.get("688001") is None
    assert thesis.get("688002")["name"] == "进程外部写"


@pytest.mark.parametrize("corrupted_entry_index", [1, 2])
def test_recovery_validates_all_entries_before_changing_any_store(
    tmp_path,
    corrupted_entry_index,
):
    transaction, thesis_path, ledger_path, target_path = _transaction(tmp_path)
    stores = [
        ("long_thesis", thesis_path),
        ("evidence_ledger", ledger_path),
        ("target_pool", target_path),
    ]
    for name, path in stores:
        path.write_bytes(f"current-{name}".encode())
    before = {path: path.read_bytes() for _, path in stores}
    entries = [
        {
            "name": name,
            "path": str(path.resolve()),
            "existed": True,
            "content_b64": base64.b64encode(
                f"backup-{name}".encode()
            ).decode("ascii"),
        }
        for name, path in stores
    ]
    entries[corrupted_entry_index]["content_b64"] = "not-valid-base64!!!"
    transaction.journal_path.write_text(
        json.dumps({
            "version": 1,
            "batch_id": "batch-invalid-late-entry",
            "status": "pending",
            "stores": entries,
        }),
        encoding="utf-8",
    )

    with transaction.locked():
        with pytest.raises(
            RuntimeError,
            match="transaction journal backup invalid",
        ):
            transaction.recover_pending()

    assert {path: path.read_bytes() for _, path in stores} == before
    assert transaction.journal_path.exists()


def test_recovery_rejects_late_hash_mismatch_before_changing_stores(tmp_path):
    transaction, thesis_path, ledger_path, target_path = _transaction(tmp_path)
    stores = [
        ("long_thesis", thesis_path),
        ("evidence_ledger", ledger_path),
        ("target_pool", target_path),
    ]
    entries = []
    for name, path in stores:
        path.write_bytes(f"current-{name}".encode())
        backup = f"backup-{name}".encode()
        entries.append({
            "name": name,
            "path": str(path.resolve()),
            "existed": True,
            "content_b64": base64.b64encode(backup).decode("ascii"),
            "content_sha256": hashlib.sha256(backup).hexdigest(),
        })
    before = {path: path.read_bytes() for _, path in stores}
    entries[2]["content_sha256"] = "0" * 64
    transaction.journal_path.write_text(
        json.dumps({
            "version": 1,
            "batch_id": "batch-invalid-hash",
            "status": "pending",
            "stores": entries,
        }),
        encoding="utf-8",
    )

    with transaction.locked():
        with pytest.raises(RuntimeError, match="backup hash mismatch"):
            transaction.recover_pending()

    assert {path: path.read_bytes() for _, path in stores} == before


def test_crashed_pending_transaction_blocks_all_writers_until_recovery(
    tmp_path,
):
    transaction, thesis_path, ledger_path, target_path = _transaction(tmp_path)
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    process = context.Process(
        target=_process_begin_partial_write_and_crash,
        args=(
            str(transaction.journal_path),
            str(thesis_path),
            str(ledger_path),
            str(target_path),
            ready,
        ),
    )
    process.start()
    assert ready.wait(timeout=10)
    process.join(timeout=10)
    assert process.exitcode == 17
    stores = (thesis_path, ledger_path, target_path)
    after_crash = {
        path: path.read_bytes() if path.exists() else None
        for path in stores
    }
    ledger = EvidenceLedgerStore(ledger_path)
    thesis = LongThesisStore(thesis_path)
    target = TargetPoolStore(target_path)
    ordinary_writes = [
        lambda: ledger.append_many([{
            "evidence_id": "ev_external_after_crash",
            "type": "test",
            "summary": "must wait for recovery",
        }]),
        lambda: thesis.upsert({
            "symbol": "688001",
            "name": "外部 thesis",
            "core_thesis": "must wait for recovery",
        }),
        lambda: target.upsert_target(
            code="920001",
            name="外部 target",
            status="long_research",
            source="long_horizon",
        ),
    ]

    for write in ordinary_writes:
        with pytest.raises(
            RuntimeError,
            match="long_horizon_recovery_required",
        ):
            write()
        assert {
            path: path.read_bytes() if path.exists() else None
            for path in stores
        } == after_crash

    with transaction.locked():
        recovered = transaction.recover_pending()
    assert recovered["status"] == "recovered"

    for write in ordinary_writes:
        write()
    assert {
        item["evidence_id"] for item in ledger.load_all()
    } == {"ev_external_after_crash"}
    assert thesis.get("688001")["name"] == "外部 thesis"
    assert target.get("920001")["name"] == "外部 target"
    after_retry = {path: path.read_bytes() for path in stores}
    with transaction.locked():
        assert transaction.recover_pending() is None
    assert {path: path.read_bytes() for path in stores} == after_retry


def test_corrupted_pending_journal_keeps_ordinary_writers_fail_closed(
    tmp_path,
):
    transaction, thesis_path, ledger_path, _ = _transaction(tmp_path)
    with transaction.locked():
        transaction.begin()
        transaction.journal_path.write_text("{broken", encoding="utf-8")
    ledger = EvidenceLedgerStore(ledger_path)
    before = ledger_path.read_bytes() if ledger_path.exists() else None

    for _ in range(2):
        with pytest.raises(
            RuntimeError,
            match="long_horizon_recovery_required",
        ):
            ledger.append_many([{
                "evidence_id": "ev_must_not_write",
                "type": "test",
                "summary": "corrupted recovery required",
            }])
        assert (
            ledger_path.read_bytes() if ledger_path.exists() else None
        ) == before

    with transaction.locked():
        with pytest.raises(RuntimeError, match="journal unreadable"):
            transaction.recover_pending()
    with pytest.raises(
        RuntimeError,
        match="long_horizon_recovery_required",
    ):
        LongThesisStore(thesis_path).upsert({
            "symbol": "688001",
            "name": "仍需恢复",
            "core_thesis": "must remain blocked",
        })


def test_legacy_empty_lock_with_valid_journal_blocks_all_writers(tmp_path):
    transaction, thesis_path, ledger_path, target_path = _transaction(tmp_path)
    with transaction.locked():
        transaction.begin()
        EvidenceLedgerStore(ledger_path).append_many([{
            "evidence_id": "ev_legacy_partial",
            "type": "test",
            "summary": "must be restored",
        }])
    transaction.lock_path.write_text("", encoding="utf-8")
    _transaction_state_path(transaction).unlink(missing_ok=True)
    paths = (thesis_path, ledger_path, target_path)
    before = _store_bytes(paths)
    writes = _ordinary_store_writes(
        thesis_path,
        ledger_path,
        target_path,
        "legacy",
    )

    _assert_writes_recovery_required(writes, paths, before)

    with transaction.locked():
        assert transaction.recover_pending()["status"] == "recovered"
    for write in writes:
        write()
    after_retry = _store_bytes(paths)
    with transaction.locked():
        assert transaction.recover_pending() is None
    assert _store_bytes(paths) == after_retry


@pytest.mark.parametrize("window", ["state_temp", "journal_before_replace"])
def test_pre_journal_crash_windows_block_all_writers_until_cleanup(
    tmp_path,
    window,
):
    transaction, thesis_path, ledger_path, target_path = _transaction(tmp_path)
    state_path = _transaction_state_path(transaction)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    if window == "state_temp":
        state_temp = state_path.with_name(
            f".{state_path.name}.fault.tmp"
        )
        state_temp.write_text('{"status":"preparing"', encoding="utf-8")
    else:
        state_path.write_text(
            json.dumps({
                "version": 1,
                "batch_id": "batch-before-journal-replace",
                "status": "preparing",
                "journal_path": str(transaction.journal_path.resolve()),
            }),
            encoding="utf-8",
        )
        journal_temp = transaction.journal_path.with_name(
            f".{transaction.journal_path.name}.fault.tmp"
        )
        journal_temp.write_text("partial journal", encoding="utf-8")
    paths = (thesis_path, ledger_path, target_path)
    before = _store_bytes(paths)
    writes = _ordinary_store_writes(
        thesis_path,
        ledger_path,
        target_path,
        window,
    )

    _assert_writes_recovery_required(writes, paths, before)

    with transaction.locked():
        cleaned = transaction.recover_pending()
    assert cleaned["status"] == "recovery_state_cleared"
    for write in writes:
        write()


def test_journal_unlinked_before_state_clean_blocks_writers_until_cleanup(
    tmp_path,
):
    transaction, thesis_path, ledger_path, target_path = _transaction(tmp_path)
    with transaction.locked():
        transaction.begin()
        EvidenceLedgerStore(ledger_path).append_many([{
            "evidence_id": "ev_committed_before_cleanup",
            "type": "test",
            "summary": "must remain after cleanup",
        }])
    transaction.journal_path.unlink()
    transaction.lock_path.write_text("", encoding="utf-8")
    state_path = _transaction_state_path(transaction)
    state_path.write_text(
        json.dumps({
            "version": 1,
            "batch_id": "batch-unlinked-before-clean",
            "status": "committing",
            "journal_path": str(transaction.journal_path.resolve()),
        }),
        encoding="utf-8",
    )
    paths = (thesis_path, ledger_path, target_path)
    before = _store_bytes(paths)
    writes = _ordinary_store_writes(
        thesis_path,
        ledger_path,
        target_path,
        "unlink_clear",
    )

    _assert_writes_recovery_required(writes, paths, before)

    with transaction.locked():
        cleaned = transaction.recover_pending()
    assert cleaned["status"] == "recovery_state_cleared"
    for write in writes:
        write()
    evidence_ids = {
        item["evidence_id"] for item in EvidenceLedgerStore(
            ledger_path
        ).load_all()
    }
    assert evidence_ids == {
        "ev_committed_before_cleanup",
        "ev_external_unlink_clear",
    }
