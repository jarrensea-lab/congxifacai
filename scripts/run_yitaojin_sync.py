#!/usr/bin/env python3
"""Manual entrypoint for the fail-closed Yitaojin integration."""

from __future__ import annotations

import os
import sys


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "backend"))

from app.integrations.yitaojin.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
