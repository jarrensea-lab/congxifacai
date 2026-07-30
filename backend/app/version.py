"""Single source of truth for product and strategy runtime identity."""
from __future__ import annotations

import os
import subprocess
from datetime import datetime
from pathlib import Path


PRODUCT_VERSION = "v9.0.0-dev"
PIPELINE_VERSION = "actionable_pipeline_v1"
SCORE_VERSION = "composite_score_v1"


def _resolve_commit() -> str:
    configured = os.getenv("CONGXI_BUILD_COMMIT", "").strip()
    if configured:
        return configured
    try:
        return (
            subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=Path(__file__).resolve().parents[2],
                check=True,
                capture_output=True,
                text=True,
                timeout=2,
            ).stdout.strip()
            or "unknown"
        )
    except Exception:
        return "unknown"


def build_runtime_identity(*, started_at: str | None = None) -> dict[str, str]:
    """Return the immutable identity attached to one running process."""
    return {
        "product_version": PRODUCT_VERSION,
        "pipeline_version": PIPELINE_VERSION,
        "score_version": SCORE_VERSION,
        "commit": _resolve_commit(),
        "started_at": started_at or datetime.now().astimezone().isoformat(),
    }
