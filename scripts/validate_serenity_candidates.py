#!/usr/bin/env python3
"""Validate Serenity theme candidate pool JSON."""
import argparse
import json
import os
import sys
from pathlib import Path

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, os.path.join(PROJECT_ROOT, "backend"))

from app.ai.serenity_analyst import DEFAULT_CANDIDATE_POOL_PATH, validate_theme_candidate_pool


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "path",
        nargs="?",
        default=str(DEFAULT_CANDIDATE_POOL_PATH),
        help="Serenity 候选池 JSON 路径",
    )
    args = parser.parse_args(argv)

    path = Path(args.path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"候选池校验失败: {path}")
        print(f"- 无法读取或解析 JSON: {exc}")
        return 1

    errors = validate_theme_candidate_pool(raw)
    if errors:
        print(f"候选池校验失败: {path}")
        for error in errors:
            print(f"- {error}")
        return 1

    print(f"候选池校验通过: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
