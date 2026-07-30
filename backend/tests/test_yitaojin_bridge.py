from __future__ import annotations

import json
from pathlib import Path

import pytest


def _write_executable(path: Path, source: str) -> Path:
    path.write_text(source, encoding="utf-8")
    path.chmod(0o755)
    return path


def test_bridge_runs_typed_json_request_and_returns_data(tmp_path):
    """Catches shell interpolation or envelope fields bypassing validation."""
    from app.integrations.yitaojin.bridge import YitaojinBridge
    from app.integrations.yitaojin.models import BridgeCommand

    executable = _write_executable(
        tmp_path / "fake-bridge",
        """#!/bin/sh
read request
command=$(printf '%s' "$request" | python3 -c 'import json,sys; print(json.load(sys.stdin)["command"])')
printf '{"schemaVersion":1,"ok":true,"command":"%s","capturedAt":"2026-07-26T09:30:05+08:00","data":{"codes":["000001"]}}\\n' "$command"
""",
    )

    result = YitaojinBridge(executable).run(BridgeCommand.READ_WATCHLIST)

    assert result == {"codes": ["000001"]}


def test_bridge_timeout_is_fail_closed(tmp_path):
    """Catches a hung UI helper blocking the service indefinitely."""
    from app.integrations.yitaojin.bridge import YitaojinBridge
    from app.integrations.yitaojin.models import (
        BridgeCommand,
        BridgeUnavailableError,
    )

    executable = _write_executable(
        tmp_path / "slow-bridge",
        "#!/bin/sh\nsleep 2\n",
    )

    with pytest.raises(BridgeUnavailableError, match="timed out"):
        YitaojinBridge(executable).run(BridgeCommand.PROBE, timeout=0.05)


@pytest.mark.parametrize(
    ("error_code", "expected_exception"),
    [
        ("accessibility_permission_missing", "AccessibilityPermissionError"),
        ("not_logged_in", "AppNotLoggedInError"),
        ("unsafe_ui_target", "UnsafeUiTargetError"),
    ],
)
def test_bridge_maps_structured_safety_errors(
    tmp_path,
    error_code,
    expected_exception,
):
    """Catches safety failures being treated as retryable empty data."""
    from app.integrations.yitaojin import models
    from app.integrations.yitaojin.bridge import YitaojinBridge
    from app.integrations.yitaojin.models import BridgeCommand

    executable = _write_executable(
        tmp_path / f"error-{error_code}",
        f"""#!/bin/sh
printf '%s\\n' '{{"schemaVersion":1,"ok":false,"command":"probe","capturedAt":"2026-07-26T09:30:05+08:00","error":{{"code":"{error_code}","message":"blocked"}}}}'
exit 2
""",
    )

    error_type = getattr(models, expected_exception)
    with pytest.raises(error_type):
        YitaojinBridge(executable).run(BridgeCommand.PROBE)


def test_bridge_never_includes_stderr_contents_in_exception(tmp_path):
    """Catches raw application text leaking through subprocess diagnostics."""
    from app.integrations.yitaojin.bridge import YitaojinBridge
    from app.integrations.yitaojin.models import (
        BridgeCommand,
        BridgeUnavailableError,
    )

    executable = _write_executable(
        tmp_path / "stderr-bridge",
        "#!/bin/sh\nprintf '%s\\n' 'SENSITIVE_ACCOUNT_TEXT' >&2\nexit 3\n",
    )

    with pytest.raises(BridgeUnavailableError) as captured:
        YitaojinBridge(executable).run(BridgeCommand.PROBE)

    assert "SENSITIVE_ACCOUNT_TEXT" not in str(captured.value)


@pytest.mark.parametrize(
    "stdout",
    [
        "",
        "not-json",
        json.dumps({"schemaVersion": 99, "ok": True, "command": "probe", "data": {}}),
        json.dumps({"schemaVersion": 1, "ok": True, "command": "read_account", "data": {}}),
    ],
)
def test_bridge_rejects_empty_malformed_or_mismatched_envelopes(tmp_path, stdout):
    """Catches untrusted subprocess output crossing into domain models."""
    from app.integrations.yitaojin.bridge import YitaojinBridge
    from app.integrations.yitaojin.models import (
        BridgeCommand,
        BridgeUnavailableError,
    )

    executable = _write_executable(
        tmp_path / "invalid-bridge",
        f"#!/bin/sh\nprintf '%s' {json.dumps(stdout)}\n",
    )

    with pytest.raises(BridgeUnavailableError):
        YitaojinBridge(executable).run(BridgeCommand.PROBE)


def test_bridge_rejects_symlink_executable(tmp_path):
    """Catches a swapped helper binary bypassing the configured target."""
    from app.integrations.yitaojin.bridge import YitaojinBridge
    from app.integrations.yitaojin.models import BridgeUnavailableError

    target = _write_executable(tmp_path / "target", "#!/bin/sh\n")
    link = tmp_path / "bridge-link"
    link.symlink_to(target)

    with pytest.raises(BridgeUnavailableError, match="symlink"):
        YitaojinBridge(link)
