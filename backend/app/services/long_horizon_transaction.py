"""Durable three-store transaction journal for long-horizon materialization."""
from __future__ import annotations

import base64
import json
import os
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime
from fcntl import LOCK_EX, LOCK_UN, flock
from pathlib import Path
from threading import RLock
from typing import Iterator


_PROCESS_TRANSACTION_LOCK = RLock()


def default_transaction_path(thesis_path: Path) -> Path:
    configured = os.environ.get("CONGXI_LONG_HORIZON_TRANSACTION_PATH")
    if configured:
        return Path(configured)
    return thesis_path.parent / "long_horizon_transaction.json"


def _fsync_directory(path: Path) -> None:
    directory_fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        _fsync_directory(path.parent)
    finally:
        temp_path.unlink(missing_ok=True)


def _durable_unlink(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        return
    _fsync_directory(path.parent)


class LongHorizonBatchTransaction:
    """Persist and restore exact bytes for a three-store materialization batch."""

    def __init__(
        self,
        journal_path: str | Path,
        stores: list[tuple[str, str | Path]],
    ):
        self.journal_path = Path(journal_path)
        self.stores = [(name, Path(path)) for name, path in stores]

    @contextmanager
    def locked(self) -> Iterator[None]:
        lock_path = self.journal_path.with_name(
            f".{self.journal_path.name}.lock"
        )
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with (
            _PROCESS_TRANSACTION_LOCK,
            lock_path.open("a+", encoding="utf-8") as lock_file,
        ):
            flock(lock_file.fileno(), LOCK_EX)
            try:
                yield
            finally:
                flock(lock_file.fileno(), LOCK_UN)

    def begin(self) -> str:
        if self.journal_path.exists():
            raise RuntimeError("pending transaction journal must be recovered first")
        batch_id = f"lh_{uuid.uuid4().hex}"
        snapshots = []
        for name, path in self.stores:
            existed = path.exists()
            content = path.read_bytes() if existed else b""
            snapshots.append({
                "name": name,
                "path": str(path.resolve()),
                "existed": existed,
                "content_b64": base64.b64encode(content).decode("ascii"),
            })
        journal = {
            "version": 1,
            "batch_id": batch_id,
            "status": "pending",
            "created_at": datetime.now().astimezone().isoformat(),
            "stores": snapshots,
        }
        _atomic_write(
            self.journal_path,
            (json.dumps(journal, ensure_ascii=False, indent=2) + "\n").encode(
                "utf-8"
            ),
        )
        return batch_id

    def recover_pending(self) -> dict[str, object] | None:
        if not self.journal_path.exists():
            return None
        try:
            journal = json.loads(self.journal_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                f"transaction journal unreadable: {type(exc).__name__}: {exc}"
            ) from exc
        if (
            not isinstance(journal, dict)
            or journal.get("version") != 1
            or journal.get("status") != "pending"
            or not isinstance(journal.get("stores"), list)
        ):
            raise RuntimeError("transaction journal schema invalid")
        expected = {
            name: path.resolve()
            for name, path in self.stores
        }
        entries = journal["stores"]
        if len(entries) != len(expected):
            raise RuntimeError("transaction journal store count mismatch")

        restored_names: list[str] = []
        seen_names: set[str] = set()
        for entry in entries:
            if not isinstance(entry, dict):
                raise RuntimeError("transaction journal store entry invalid")
            name = str(entry.get("name") or "")
            if name in seen_names or name not in expected:
                raise RuntimeError("transaction journal store name invalid")
            seen_names.add(name)
            journal_store_path = Path(str(entry.get("path") or "")).resolve()
            if journal_store_path != expected[name]:
                raise RuntimeError(
                    f"transaction journal path mismatch for {name}"
                )
            existed = entry.get("existed")
            if not isinstance(existed, bool):
                raise RuntimeError(
                    f"transaction journal existed flag invalid for {name}"
                )
            try:
                original = base64.b64decode(
                    str(entry.get("content_b64") or ""),
                    validate=True,
                )
            except (ValueError, TypeError) as exc:
                raise RuntimeError(
                    f"transaction journal backup invalid for {name}"
                ) from exc
            if existed:
                _atomic_write(expected[name], original)
            else:
                _durable_unlink(expected[name])
            restored_names.append(name)

        _durable_unlink(self.journal_path)
        return {
            "status": "recovered",
            "batch_id": str(journal.get("batch_id") or ""),
            "restored_stores": restored_names,
        }

    def rollback(self) -> dict[str, object]:
        recovered = self.recover_pending()
        if recovered is None:
            return {
                "status": "rollback_not_available",
                "batch_id": "",
                "restored_stores": [],
            }
        return {**recovered, "status": "rolled_back"}

    def commit(self) -> None:
        _durable_unlink(self.journal_path)
