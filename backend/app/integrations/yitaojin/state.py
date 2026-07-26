"""Atomic local state and append-only audit storage for Yitaojin."""
from __future__ import annotations

import fcntl
import json
import os
import re
import tempfile
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping

from app.integrations.yitaojin.models import normalize_stock_code


class StateStoreError(RuntimeError):
    """The ownership state or audit payload is unsafe."""


@dataclass(frozen=True)
class WatchlistState:
    schema_version: int = 1
    account_fingerprint: str | None = None
    manual_protected_codes: tuple[str, ...] = ()
    managed_codes: tuple[str, ...] = ()
    pending_removal_counts: dict[str, int] | None = None
    successful_apply_count: int = 0
    last_success_at: str | None = None
    last_snapshot_hash: str | None = None

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise StateStoreError("unsupported watchlist state schema")
        if self.account_fingerprint is not None and not re.fullmatch(
            r"sha256:[0-9a-f]{64}",
            self.account_fingerprint,
        ):
            raise StateStoreError("account_fingerprint must be a SHA-256 digest")
        if self.successful_apply_count < 0:
            raise StateStoreError("successful_apply_count must be nonnegative")
        manual = _codes(self.manual_protected_codes, "manual_protected_codes")
        managed = _codes(self.managed_codes, "managed_codes")
        raw_counts = self.pending_removal_counts or {}
        if not isinstance(raw_counts, Mapping):
            raise StateStoreError("pending_removal_counts must be an object")
        counts: dict[str, int] = {}
        for raw_code, raw_count in raw_counts.items():
            code = normalize_stock_code(raw_code)
            if isinstance(raw_count, bool):
                raise StateStoreError("removal confirmation count must be an integer")
            try:
                count = int(raw_count)
            except (TypeError, ValueError):
                raise StateStoreError(
                    "removal confirmation count must be an integer"
                ) from None
            if count < 0 or str(count) != str(raw_count).strip():
                raise StateStoreError(
                    "removal confirmation count must be nonnegative"
                )
            counts[code] = count
        object.__setattr__(self, "manual_protected_codes", manual)
        object.__setattr__(self, "managed_codes", managed)
        object.__setattr__(self, "pending_removal_counts", counts)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> WatchlistState:
        if not isinstance(payload, Mapping):
            raise StateStoreError("watchlist state must be an object")
        try:
            return cls(
                schema_version=payload.get("schema_version", 0),
                account_fingerprint=payload.get("account_fingerprint"),
                manual_protected_codes=tuple(
                    payload.get("manual_protected_codes") or ()
                ),
                managed_codes=tuple(payload.get("managed_codes") or ()),
                pending_removal_counts=dict(
                    payload.get("pending_removal_counts") or {}
                ),
                successful_apply_count=payload.get("successful_apply_count", 0),
                last_success_at=payload.get("last_success_at"),
                last_snapshot_hash=payload.get("last_snapshot_hash"),
            )
        except (TypeError, ValueError, StateStoreError) as exc:
            if isinstance(exc, StateStoreError):
                raise
            raise StateStoreError("watchlist state is malformed") from exc


def _codes(values: Any, field: str) -> tuple[str, ...]:
    if not isinstance(values, (list, tuple, set, frozenset)):
        raise StateStoreError(f"{field} must be a code collection")
    try:
        return tuple(sorted({normalize_stock_code(value) for value in values}))
    except Exception as exc:
        raise StateStoreError(f"{field} contains an invalid code") from exc


@contextmanager
def _exclusive_lock(lock_path: Path) -> Iterator[None]:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _atomic_json_write(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise StateStoreError("state payload is not JSON serializable") from exc
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as temp_file:
            temp_file.write(encoded)
            temp_file.write("\n")
            temp_file.flush()
            os.fsync(temp_file.fileno())
        os.replace(temp_path, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


_SENSITIVE_AUDIT_KEYS = frozenset(
    {
        "accountnumber",
        "fullaccount",
        "mobile",
        "phone",
        "phonenumber",
        "shareholderaccount",
        "password",
        "tradepassword",
        "verificationcode",
        "smscode",
    }
)


def _assert_safe_audit_payload(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            normalized = re.sub(r"[^a-z0-9]", "", str(key).lower())
            if normalized in _SENSITIVE_AUDIT_KEYS:
                raise StateStoreError("sensitive account field is forbidden in audit")
            _assert_safe_audit_payload(nested)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            _assert_safe_audit_payload(nested)


class YitaojinStateStore:
    def __init__(
        self,
        path: str | Path,
        *,
        audit_path: str | Path | None = None,
    ) -> None:
        self.path = Path(path)
        self.audit_path = Path(audit_path) if audit_path is not None else None

    @contextmanager
    def sync_lock(self) -> Iterator[None]:
        """Serialize one complete watchlist read-plan-write cycle."""
        with _exclusive_lock(Path(f"{self.path}.sync.lock")):
            yield

    def load(self) -> WatchlistState:
        if not self.path.exists():
            return WatchlistState()
        with _exclusive_lock(Path(f"{self.path}.lock")):
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise StateStoreError("watchlist state is unreadable") from exc
            return WatchlistState.from_dict(payload)

    def save(self, state: WatchlistState) -> None:
        with _exclusive_lock(Path(f"{self.path}.lock")):
            _atomic_json_write(self.path, state.to_dict())

    def append_audit(self, event: Mapping[str, Any]) -> None:
        if self.audit_path is None:
            raise StateStoreError("audit path is not configured")
        required = {"run_id", "mode", "input_hash", "plan", "result"}
        if not isinstance(event, Mapping) or not required.issubset(event):
            raise StateStoreError("audit event is missing required fields")
        _assert_safe_audit_payload(event)
        record = dict(event)
        record.setdefault(
            "recorded_at",
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
        try:
            encoded = json.dumps(
                record,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        except (TypeError, ValueError) as exc:
            raise StateStoreError("audit event is not JSON serializable") from exc
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)
        with _exclusive_lock(Path(f"{self.audit_path}.lock")):
            with self.audit_path.open("a", encoding="utf-8") as audit_file:
                audit_file.write(encoded)
                audit_file.write("\n")
                audit_file.flush()
                os.fsync(audit_file.fileno())
