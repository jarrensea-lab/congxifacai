#!/usr/bin/env python3
"""Run Sentinel news package and role-performance review jobs."""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT / "backend"))
sys.path.insert(0, str(PROJECT_ROOT))

from app.ai.sentinel_research import (
    build_news_research_package,
    build_serenity_deep_dives,
    persist_research_package,
    persist_serenity_deep_dive_reports,
)
from app.ai.sentinel_role_performance import (
    persist_review_outputs,
    score_roles,
    summarize_advice_performance,
    suggest_role_adjustments,
)
from app.data_sources.horizon_news_importer import (
    import_default_tushare_news_events,
    write_sentinel_news_events,
)

DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "data" / "sentinel"
SERENITY_LEARNING_ARCHIVE_DIR = os.environ.get(
    "CONGXI_REPORT_ARCHIVE_DIR",
    "/Volumes/Aino Kishi/AI/projects/司库/01-资料采集/量化投资/恭喜发财报告",
)


def _today_iso() -> str:
    return date.today().isoformat()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    items: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            items.append(payload)
    return items


def run_news_job(report_date: str, output_root: str | Path = DEFAULT_OUTPUT_ROOT) -> dict[str, Any]:
    """Import Tushare high-frequency news and persist a Sentinel research package."""
    root = Path(output_root)
    events = import_default_tushare_news_events(report_date)
    news_path = root / "news_events" / f"{report_date}.jsonl"
    write_sentinel_news_events(events, news_path)
    package = build_news_research_package(events, report_date=report_date)
    dives = build_serenity_deep_dives(
        package.get("top_themes", []),
        report_date=report_date,
        limit=3,
    )
    package["serenity_deep_dives"] = persist_serenity_deep_dive_reports(
        dives,
        report_date=report_date,
        archive_dir=SERENITY_LEARNING_ARCHIVE_DIR,
    )
    paths = persist_research_package(package, output_root=root)
    return {
        "mode": "news",
        "date": report_date,
        "event_count": len(events),
        "serenity_deep_dive_count": len(package["serenity_deep_dives"]),
        "news_events": str(news_path),
        **paths,
    }


def run_review_job(report_date: str, output_root: str | Path = DEFAULT_OUTPUT_ROOT) -> dict[str, Any]:
    """Generate Sentinel role score and advice performance artifacts."""
    root = Path(output_root)
    outcomes = _read_jsonl(root / "role_outcomes" / f"{report_date}.jsonl")
    scorecard = score_roles(outcomes, score_date=report_date)
    advice_performance = summarize_advice_performance(outcomes, performance_date=report_date)
    suggestions = suggest_role_adjustments(scorecard)
    paths = persist_review_outputs(
        scorecard=scorecard,
        advice_performance=advice_performance,
        suggestions=suggestions,
        output_root=root,
    )
    return {
        "mode": "review",
        "date": report_date,
        "outcome_count": len(outcomes),
        **paths,
    }


def run_all(report_date: str, output_root: str | Path = DEFAULT_OUTPUT_ROOT) -> dict[str, Any]:
    """Run both Sentinel news and review jobs."""
    return {
        "news": run_news_job(report_date, output_root=output_root),
        "review": run_review_job(report_date, output_root=output_root),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=_today_iso(), help="Report date in YYYY-MM-DD format")
    parser.add_argument("--mode", choices=("news", "review", "all"), default="all")
    parser.add_argument("--output-root", default=os.environ.get("CONGXI_SENTINEL_OUTPUT_ROOT", str(DEFAULT_OUTPUT_ROOT)))
    args = parser.parse_args()

    if args.mode == "news":
        result = run_news_job(args.date, output_root=args.output_root)
    elif args.mode == "review":
        result = run_review_job(args.date, output_root=args.output_root)
    else:
        result = run_all(args.date, output_root=args.output_root)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
