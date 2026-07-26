"""Concurrency and recovery tests for long-horizon batch transactions."""
from __future__ import annotations

import base64
import hashlib
import json
import multiprocessing
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
