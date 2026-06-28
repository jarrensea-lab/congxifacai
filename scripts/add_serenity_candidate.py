#!/usr/bin/env python3
"""Safely add a Serenity theme alias or candidate to the JSON pool."""

import argparse
import json
import os
import sys
from pathlib import Path

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, os.path.join(PROJECT_ROOT, "backend"))

from app.ai.serenity_analyst import DEFAULT_CANDIDATE_POOL_PATH, validate_theme_candidate_pool


def _load_pool(path: Path) -> dict:
    if not path.exists():
        return {"aliases": {}, "candidates": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def _write_pool_safely(path: Path, raw: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp_errors = validate_theme_candidate_pool(json.loads(tmp_path.read_text(encoding="utf-8")))
    if tmp_errors:
        tmp_path.unlink(missing_ok=True)
        raise ValueError("写入后校验失败: " + "; ".join(tmp_errors))
    tmp_path.replace(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pool", default=str(DEFAULT_CANDIDATE_POOL_PATH), help="候选池 JSON 路径")
    parser.add_argument("--theme", required=True, help="标准主题名，例如 AI基建/电力")
    parser.add_argument("--alias", action="append", default=[], help="可重复传入的主题别名")
    parser.add_argument("--candidate-json", default="", help="候选标的 JSON object")
    args = parser.parse_args(argv)

    path = Path(args.pool)
    try:
        raw = _load_pool(path)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"读取候选池失败: {exc}")
        return 1

    errors = validate_theme_candidate_pool(raw)
    if errors:
        print("现有候选池校验失败，未写入。")
        for error in errors:
            print(f"- {error}")
        return 1

    raw.setdefault("aliases", {})
    raw.setdefault("candidates", {})
    for alias in args.alias:
        raw["aliases"][alias] = args.theme

    if args.candidate_json:
        try:
            candidate = json.loads(args.candidate_json)
        except json.JSONDecodeError as exc:
            print(f"候选 JSON 解析失败: {exc}")
            return 1
        if not isinstance(candidate, dict):
            print("候选 JSON 必须是 object")
            return 1
        theme_candidates = raw["candidates"].setdefault(args.theme, [])
        code = candidate.get("code")
        if any(item.get("code") == code for item in theme_candidates if isinstance(item, dict)):
            print(f"候选已存在，未重复写入: {args.theme} {code}")
            return 2
        theme_candidates.append(candidate)

    errors = validate_theme_candidate_pool(raw)
    if errors:
        print("新增内容校验失败，未写入。")
        for error in errors:
            print(f"- {error}")
        return 1

    try:
        _write_pool_safely(path, raw)
    except (OSError, ValueError) as exc:
        print(f"写入候选池失败: {exc}")
        return 1

    print(f"候选池已更新: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
