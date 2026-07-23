"""Immutable identity for the currently running application process."""
from __future__ import annotations

import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any


STRATEGY_VERSION = "v8.2.0-dev"


def _git_commit() -> str:
    configured = str(os.getenv("CONGXI_BUILD_COMMIT") or "").strip()
    if configured:
        return configured
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=Path(__file__).resolve().parents[3],
            text=True,
            capture_output=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return result.stdout.strip() if result.returncode == 0 and result.stdout.strip() else "unknown"


def build_runtime_identity(*, started_at: str | None = None) -> dict[str, Any]:
    """Build process metadata once so health and scheduler logs share one truth."""
    return {
        "commit": _git_commit(),
        "started_at": started_at or datetime.now().astimezone().isoformat(timespec="seconds"),
        "strategy_version": STRATEGY_VERSION,
    }


runtime_identity = build_runtime_identity()
