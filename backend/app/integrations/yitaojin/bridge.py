"""Subprocess client for the local Swift Accessibility bridge."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any, Mapping

from app.integrations.yitaojin.models import (
    AccessibilityPermissionError,
    AppNotLoggedInError,
    BridgeCommand,
    BridgeUnavailableError,
    UnsafeUiTargetError,
)


_ERROR_TYPES = {
    "accessibility_permission_missing": AccessibilityPermissionError,
    "not_logged_in": AppNotLoggedInError,
    "unsafe_ui_target": UnsafeUiTargetError,
}
_MAX_RESPONSE_BYTES = 5 * 1024 * 1024


class YitaojinBridge:
    def __init__(self, executable: str | Path) -> None:
        self.executable = Path(executable).expanduser()
        self._validate_executable()

    def _validate_executable(self) -> None:
        if self.executable.is_symlink():
            raise BridgeUnavailableError("bridge executable must not be a symlink")
        if not self.executable.exists() or not self.executable.is_file():
            raise BridgeUnavailableError("bridge executable is missing")
        if not os.access(self.executable, os.X_OK):
            raise BridgeUnavailableError("bridge executable is not executable")
        if ".app/Contents" in str(self.executable.resolve()):
            raise BridgeUnavailableError("bridge executable cannot live inside an app bundle")

    def run(
        self,
        command: BridgeCommand,
        payload: Mapping[str, object] | None = None,
        *,
        timeout: float = 15.0,
    ) -> Mapping[str, Any]:
        request = {
            "schemaVersion": 1,
            "command": command.value,
            "payload": dict(payload or {}),
        }
        try:
            completed = subprocess.run(
                [str(self.executable)],
                input=json.dumps(
                    request,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    allow_nan=False,
                )
                + "\n",
                text=True,
                capture_output=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise BridgeUnavailableError(
                f"yitaojin bridge timed out after {timeout:.2f}s"
            ) from None
        except OSError as exc:
            raise BridgeUnavailableError(
                f"yitaojin bridge could not start: {exc.__class__.__name__}"
            ) from None

        stdout = completed.stdout or ""
        if len(stdout.encode("utf-8")) > _MAX_RESPONSE_BYTES:
            raise BridgeUnavailableError("yitaojin bridge response is too large")
        try:
            envelope = json.loads(stdout)
        except (json.JSONDecodeError, TypeError):
            raise BridgeUnavailableError(
                f"yitaojin bridge returned invalid output (exit={completed.returncode})"
            ) from None
        if not isinstance(envelope, dict):
            raise BridgeUnavailableError("yitaojin bridge envelope must be an object")
        if envelope.get("schemaVersion") != 1:
            raise BridgeUnavailableError("yitaojin bridge schema is unsupported")
        if envelope.get("command") != command.value:
            raise BridgeUnavailableError("yitaojin bridge command does not match request")
        if envelope.get("ok") is not True or completed.returncode != 0:
            self._raise_bridge_error(envelope, completed.returncode)
        data = envelope.get("data")
        if not isinstance(data, dict):
            raise BridgeUnavailableError("yitaojin bridge data must be an object")
        return data

    @staticmethod
    def _raise_bridge_error(envelope: Mapping[str, Any], returncode: int) -> None:
        error = envelope.get("error")
        if not isinstance(error, Mapping):
            raise BridgeUnavailableError(
                f"yitaojin bridge failed without a safe error (exit={returncode})"
            )
        code = str(error.get("code") or "bridge_failed")
        error_type = _ERROR_TYPES.get(code, BridgeUnavailableError)
        raise error_type(f"yitaojin bridge blocked: {code}")
