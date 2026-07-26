"""Durable three-store transaction journal for long-horizon materialization."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime
from fcntl import LOCK_EX, LOCK_SH, LOCK_UN, flock
from pathlib import Path
from threading import RLock, local
from typing import Iterator


_PROCESS_TRANSACTION_LOCK = RLock()
_TRANSACTION_CONTEXT = local()
DEFAULT_MAX_SNAPSHOT_BYTES = 64 * 1024 * 1024


class TransactionSnapshotTooLarge(RuntimeError):
    """Raised before journaling when store snapshots exceed the safe limit."""

    def __init__(self, total_bytes: int, max_bytes: int):
        self.total_bytes = total_bytes
        self.max_bytes = max_bytes
        super().__init__(
            f"transaction snapshot {total_bytes} exceeds limit {max_bytes}"
        )


class LongHorizonRecoveryRequired(RuntimeError):
    """Raised when a durable pending marker blocks ordinary store writes."""

    def __init__(self):
        super().__init__("long_horizon_recovery_required")


def _max_snapshot_bytes(explicit_value: int | None) -> int:
    if explicit_value is not None:
        return max(1, int(explicit_value))
    configured = os.environ.get("CONGXI_LONG_HORIZON_MAX_SNAPSHOT_BYTES")
    if configured:
        try:
            return max(1, int(configured))
        except ValueError:
            pass
    return DEFAULT_MAX_SNAPSHOT_BYTES


def transaction_lock_path_for_store(
    store_path: str | Path,
    explicit_path: str | Path | None = None,
) -> Path:
    configured = explicit_path or os.environ.get(
        "CONGXI_LONG_HORIZON_TRANSACTION_LOCK_PATH"
    )
    if configured:
        return Path(configured)
    return Path(store_path).parent / ".long_horizon_transaction.lock"


@contextmanager
def transaction_guard(
    lock_path: str | Path,
    *,
    exclusive: bool,
) -> Iterator[None]:
    """Acquire the process/thread reentrant transaction RW guard."""
    path = Path(lock_path).resolve()
    key = str(path)
    held = getattr(_TRANSACTION_CONTEXT, "held", None)
    if held is None:
        held = {}
        _TRANSACTION_CONTEXT.held = held
    current = held.get(key)
    if current is not None:
        if exclusive and current["mode"] != "exclusive":
            raise RuntimeError("cannot upgrade shared transaction guard")
        current["depth"] += 1
        try:
            yield
        finally:
            current["depth"] -= 1
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    with _PROCESS_TRANSACTION_LOCK, path.open("a+", encoding="utf-8") as lock_file:
        flock(lock_file.fileno(), LOCK_EX if exclusive else LOCK_SH)
        held[key] = {
            "mode": "exclusive" if exclusive else "shared",
            "depth": 1,
            "file": lock_file,
        }
        try:
            yield
        finally:
            held.pop(key, None)
            flock(lock_file.fileno(), LOCK_UN)


def _current_transaction_context(lock_path: str | Path) -> dict | None:
    held = getattr(_TRANSACTION_CONTEXT, "held", {})
    return held.get(str(Path(lock_path).resolve()))


def _read_lock_marker(lock_path: str | Path) -> dict | None:
    current = _current_transaction_context(lock_path)
    if current is None:
        raise RuntimeError("transaction guard required for marker read")
    lock_file = current["file"]
    lock_file.seek(0)
    raw = lock_file.read().strip()
    if not raw:
        return None
    try:
        marker = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("transaction lock marker unreadable") from exc
    if not isinstance(marker, dict) or not str(marker.get("status") or ""):
        raise RuntimeError("transaction lock marker invalid")
    return marker


def _write_lock_marker(
    lock_path: str | Path,
    payload: dict,
) -> None:
    current = _current_transaction_context(lock_path)
    if current is None or current["mode"] != "exclusive":
        raise RuntimeError("exclusive transaction guard required for marker write")
    lock_file = current["file"]
    lock_file.seek(0)
    lock_file.truncate(0)
    lock_file.write(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    lock_file.flush()
    os.fsync(lock_file.fileno())
    _fsync_directory(Path(lock_path).resolve().parent)


def _clear_lock_marker(lock_path: str | Path) -> None:
    current = _current_transaction_context(lock_path)
    if current is None or current["mode"] != "exclusive":
        raise RuntimeError("exclusive transaction guard required for marker clear")
    lock_file = current["file"]
    lock_file.seek(0)
    lock_file.truncate(0)
    lock_file.flush()
    os.fsync(lock_file.fileno())


@contextmanager
def writer_transaction_guard(lock_path: str | Path) -> Iterator[None]:
    """Hold a shared guard and reject writes while durable recovery is pending."""
    with transaction_guard(lock_path, exclusive=False):
        current = _current_transaction_context(lock_path)
        if current is not None and current["mode"] != "exclusive":
            try:
                marker = _read_lock_marker(lock_path)
            except RuntimeError as exc:
                raise LongHorizonRecoveryRequired() from exc
            if marker is not None:
                raise LongHorizonRecoveryRequired()
        yield


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
        *,
        lock_path: str | Path | None = None,
        max_snapshot_bytes: int | None = None,
    ):
        self.journal_path = Path(journal_path)
        self.stores = [(name, Path(path)) for name, path in stores]
        configured_lock_path = lock_path or os.environ.get(
            "CONGXI_LONG_HORIZON_TRANSACTION_LOCK_PATH"
        )
        store_parents = {
            path.parent.resolve()
            for _, path in self.stores
        }
        if configured_lock_path is None and len(store_parents) > 1:
            raise ValueError(
                "explicit transaction lock path required for stores "
                "in different directories"
            )
        default_lock_source = (
            self.stores[0][1]
            if self.stores
            else self.journal_path
        )
        self.lock_path = transaction_lock_path_for_store(
            default_lock_source,
            configured_lock_path,
        )
        self.max_snapshot_bytes = _max_snapshot_bytes(max_snapshot_bytes)

    @contextmanager
    def locked(self) -> Iterator[None]:
        with transaction_guard(self.lock_path, exclusive=True):
            yield

    def begin(self) -> str:
        if self.journal_path.exists():
            raise RuntimeError("pending transaction journal must be recovered first")
        if _read_lock_marker(self.lock_path) is not None:
            raise LongHorizonRecoveryRequired()
        batch_id = f"lh_{uuid.uuid4().hex}"
        snapshots = []
        total_bytes = 0
        for name, path in self.stores:
            existed = path.exists()
            if existed:
                estimated_total = total_bytes + path.stat().st_size
                if estimated_total > self.max_snapshot_bytes:
                    raise TransactionSnapshotTooLarge(
                        estimated_total,
                        self.max_snapshot_bytes,
                    )
                content = path.read_bytes()
            else:
                content = b""
            total_bytes += len(content)
            if total_bytes > self.max_snapshot_bytes:
                raise TransactionSnapshotTooLarge(
                    total_bytes,
                    self.max_snapshot_bytes,
                )
            snapshots.append({
                "name": name,
                "path": str(path.resolve()),
                "existed": existed,
                "content_b64": base64.b64encode(content).decode("ascii"),
                "content_sha256": hashlib.sha256(content).hexdigest(),
            })
        journal = {
            "version": 1,
            "batch_id": batch_id,
            "status": "pending",
            "created_at": datetime.now().astimezone().isoformat(),
            "stores": snapshots,
        }
        marker = {
            "version": 1,
            "batch_id": batch_id,
            "status": "preparing",
            "journal_path": str(self.journal_path.resolve()),
        }
        _write_lock_marker(self.lock_path, marker)
        try:
            _atomic_write(
                self.journal_path,
                (
                    json.dumps(journal, ensure_ascii=False, indent=2)
                    + "\n"
                ).encode("utf-8"),
            )
        except Exception:
            if not self.journal_path.exists():
                _clear_lock_marker(self.lock_path)
            raise
        _write_lock_marker(
            self.lock_path,
            {**marker, "status": "pending"},
        )
        return batch_id

    def recover_pending(self) -> dict[str, object] | None:
        marker = _read_lock_marker(self.lock_path)
        if marker is not None:
            marker_journal = Path(
                str(marker.get("journal_path") or "")
            )
            if (
                not marker_journal.is_absolute()
                or marker_journal.resolve() != self.journal_path.resolve()
            ):
                raise RuntimeError("transaction lock marker path mismatch")
        if not self.journal_path.exists():
            marker_status = str((marker or {}).get("status") or "")
            if marker_status in {"preparing", "committing", "recovered"}:
                _clear_lock_marker(self.lock_path)
                return {
                    "status": "recovery_marker_cleared",
                    "batch_id": str((marker or {}).get("batch_id") or ""),
                    "restored_stores": [],
                }
            if marker is not None:
                raise RuntimeError(
                    "transaction journal missing while recovery required"
                )
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

        validated_entries: list[tuple[str, Path, bool, bytes]] = []
        seen_names: set[str] = set()
        for entry in entries:
            if not isinstance(entry, dict):
                raise RuntimeError("transaction journal store entry invalid")
            name = str(entry.get("name") or "")
            if name in seen_names or name not in expected:
                raise RuntimeError("transaction journal store name invalid")
            seen_names.add(name)
            raw_store_path = Path(str(entry.get("path") or ""))
            if not raw_store_path.is_absolute():
                raise RuntimeError(
                    f"transaction journal path must be absolute for {name}"
                )
            journal_store_path = raw_store_path.resolve()
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
            content_sha256 = entry.get("content_sha256")
            if content_sha256 is not None and (
                not isinstance(content_sha256, str)
                or hashlib.sha256(original).hexdigest() != content_sha256
            ):
                raise RuntimeError(
                    f"transaction journal backup hash mismatch for {name}"
                )
            validated_entries.append(
                (name, expected[name], existed, original)
            )

        if seen_names != set(expected):
            raise RuntimeError("transaction journal store names mismatch")

        restored_names: list[str] = []
        for name, store_path, existed, original in validated_entries:
            if existed:
                _atomic_write(store_path, original)
            else:
                _durable_unlink(store_path)
            restored_names.append(name)

        _write_lock_marker(
            self.lock_path,
            {
                "version": 1,
                "batch_id": str(journal.get("batch_id") or ""),
                "status": "recovered",
                "journal_path": str(self.journal_path.resolve()),
            },
        )
        _durable_unlink(self.journal_path)
        _clear_lock_marker(self.lock_path)
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
        marker = _read_lock_marker(self.lock_path)
        batch_id = str((marker or {}).get("batch_id") or "")
        _write_lock_marker(
            self.lock_path,
            {
                "version": 1,
                "batch_id": batch_id,
                "status": "committing",
                "journal_path": str(self.journal_path.resolve()),
            },
        )
        _durable_unlink(self.journal_path)
        _clear_lock_marker(self.lock_path)
