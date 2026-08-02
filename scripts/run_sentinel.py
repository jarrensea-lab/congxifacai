#!/usr/bin/env python3
"""Run Sentinel news package and role-performance review jobs."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
from datetime import date, datetime
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
from app.ai.serenity_financial_evidence import (
    fetch_financial_evidence as default_financial_fetcher,
)
from app.data_sources.tencent_client import TencentDataSource
from app.data_sources.akshare_news import AKShareNewsClient
from app.data_sources.horizon_news_importer import (
    import_default_tushare_news_events,
    write_sentinel_news_events,
)
from app.services.long_horizon_pipeline import materialize_serenity_long_horizon
from app.services.recommendation_review import (
    build_recommendation_review,
    render_recommendation_review_markdown,
)

DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "data" / "sentinel"
SERENITY_LEARNING_ARCHIVE_DIR = os.environ.get(
    "CONGXI_REPORT_ARCHIVE_DIR",
    str(Path(os.environ.get("SIKU_VAULT_DIR", str(Path.home() / "AI/projects/司库")))
        / "01-资料采集/量化投资/恭喜发财报告"),
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


def _safe_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _load_account_scale() -> dict[str, Any]:
    path = Path(
        os.environ.get(
            "CONGXI_PORTFOLIO_PATH",
            str(PROJECT_ROOT / "data" / "user_portfolio.json"),
        )
    )
    if not path.exists():
        return {
            "status": "missing",
            "available_cash": 0.0,
            "total_assets": 0.0,
            "path": str(path),
        }
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "status": "invalid",
            "available_cash": 0.0,
            "total_assets": 0.0,
            "path": str(path),
            "error": f"{type(exc).__name__}: {str(exc)[:160]}",
        }
    if not isinstance(payload, dict):
        return {
            "status": "invalid",
            "available_cash": 0.0,
            "total_assets": 0.0,
            "path": str(path),
            "error": "portfolio root must be an object",
        }
    available_cash = _safe_float(
        payload.get("available_cash", payload.get("cash", 0))
    )
    total_assets = _safe_float(payload.get("total_assets"))
    if total_assets <= 0:
        total_assets = available_cash + _safe_float(payload.get("total_value"))
    return {
        "status": "loaded",
        "available_cash": round(available_cash, 2),
        "total_assets": round(total_assets, 2),
        "path": str(path),
    }


def _fallback_news_themes(content: str) -> list[str]:
    theme_keywords = (
        (("人工智能", "AI", "算力"), "AI算力"),
        (("半导体", "芯片", "集成电路"), "AI半导体"),
        (("机器人", "具身智能"), "机器人"),
        (("电网", "特高压"), "电网设备"),
        (("电力", "储能"), "AI电力"),
        (("光通信", "光模块", "CPO"), "CPO光通信"),
        (("稀土", "有色", "黄金"), "资源材料"),
        (("券商", "银行", "保险"), "金融"),
        (("医药", "创新药"), "医药"),
        (("光伏", "新能源", "锂电"), "新能源"),
    )
    return [
        theme
        for keywords, theme in theme_keywords
        if any(keyword in content for keyword in keywords)
    ]


def _akshare_fallback_news_events(report_date: str) -> list[dict[str, Any]]:
    """Fetch a second public news source when the primary archive is empty."""

    async def fetch() -> list[dict[str, Any]]:
        client = AKShareNewsClient()
        breakfast, global_news = await asyncio.gather(
            client.fetch_cjzc(),
            client.fetch_global_news(),
        )
        return [
            *(
                {"channel": "finance_breakfast", **item}
                for item in (breakfast or [])
            ),
            *(
                {"channel": "global_market", **item}
                for item in (global_news or [])
            ),
        ]

    try:
        raw_items = asyncio.run(fetch())
    except Exception:
        return []

    events: list[dict[str, Any]] = []
    seen: set[str] = set()
    try:
        report_day = datetime.strptime(report_date, "%Y-%m-%d").date()
    except ValueError:
        report_day = date.today()
    for index, item in enumerate(raw_items):
        title = str(item.get("title") or "").strip()
        summary = str(item.get("summary") or "").strip()
        content = " — ".join(part for part in (title, summary) if part)
        if not content:
            continue
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        if digest in seen:
            continue
        seen.add(digest)
        published_at = str(item.get("time") or f"{report_date}T20:00:00+08:00")
        try:
            published_day = datetime.strptime(
                published_at[:10],
                "%Y-%m-%d",
            ).date()
        except ValueError:
            published_day = report_day
        if not 0 <= (report_day - published_day).days <= 2:
            continue
        themes = _fallback_news_themes(content)
        events.append({
            "id": f"akshare-{report_date}-{index}-{digest[:12]}",
            "source": "eastmoney_akshare",
            "channel": item.get("channel") or "global_market",
            "published_at": published_at,
            "fetched_at": datetime.now().astimezone().isoformat(),
            "content": content,
            "is_key": bool(themes),
            "symbols": [],
            "themes": themes,
            "dedupe_key": digest,
            "raw_hash": digest,
            "evidence_status": "fallback_enriched",
        })
    return events


def run_news_job(
    report_date: str,
    output_root: str | Path = DEFAULT_OUTPUT_ROOT,
    *,
    quote_fetcher=None,
    financial_fetcher=None,
    fallback_news_fetcher=None,
) -> dict[str, Any]:
    """Import Tushare high-frequency news and persist a Sentinel research package."""
    root = Path(output_root)
    events = import_default_tushare_news_events(report_date)
    primary_event_count = len(events)
    fallback_attempted = not events
    if fallback_attempted:
        fallback_news_fetcher = (
            fallback_news_fetcher or _akshare_fallback_news_events
        )
        try:
            events = list(fallback_news_fetcher(report_date) or [])
        except Exception:
            events = []
    fallback_used = bool(events) and fallback_attempted
    news_path = root / "news_events" / f"{report_date}.jsonl"
    write_sentinel_news_events(events, news_path)
    package = build_news_research_package(events, report_date=report_date)
    account_status = _load_account_scale()
    if quote_fetcher is None:
        quote_fetcher = TencentDataSource().fetch_batch
    if financial_fetcher is None:
        financial_fetcher = default_financial_fetcher
    dives = build_serenity_deep_dives(
        package.get("top_themes", []),
        report_date=report_date,
        limit=3,
        available_cash=account_status["available_cash"],
        total_assets=account_status["total_assets"],
        quote_fetcher=quote_fetcher,
        financial_fetcher=financial_fetcher,
    )
    package["serenity_deep_dives"] = persist_serenity_deep_dive_reports(
        dives,
        report_date=report_date,
        archive_dir=SERENITY_LEARNING_ARCHIVE_DIR,
    )
    try:
        long_horizon_summary = materialize_serenity_long_horizon(
            package,
            report_date,
        )
    except Exception as exc:
        long_horizon_summary = {
            "status": "failed",
            "boundary": "shadow_only",
            "thesis_count": 0,
            "evidence_count": 0,
            "target_count": 0,
            "forming_count": 0,
            "verified_count": 0,
            "diagnostics": [{
                "reason": "long_horizon_materialization_failed",
                "error": f"{type(exc).__name__}: {str(exc)[:160]}",
            }],
        }
    package["long_horizon_summary"] = long_horizon_summary
    paths = persist_research_package(package, output_root=root)
    return {
        "mode": "news",
        "date": report_date,
        "event_count": len(events),
        "primary_event_count": primary_event_count,
        "fallback_attempted": fallback_attempted,
        "fallback_used": fallback_used,
        "serenity_deep_dive_count": len(package["serenity_deep_dives"]),
        "account_status": account_status,
        "long_horizon_summary": long_horizon_summary,
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
    execution_review = asyncio.run(build_recommendation_review())
    review_json_path = root / "reports" / f"{report_date}_recommendation_execution_review.json"
    review_md_path = root / "reports" / f"{report_date}_recommendation_execution_review.md"
    review_json_path.parent.mkdir(parents=True, exist_ok=True)
    review_json_path.write_text(json.dumps(execution_review, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    review_md_path.write_text(render_recommendation_review_markdown(execution_review), encoding="utf-8")
    return {
        "mode": "review",
        "date": report_date,
        "outcome_count": len(outcomes),
        "execution_review_count": execution_review.get("executed", {}).get("count", 0),
        "execution_review": str(review_json_path),
        "execution_review_report": str(review_md_path),
        **paths,
    }


def run_all(report_date: str, output_root: str | Path = DEFAULT_OUTPUT_ROOT) -> dict[str, Any]:
    """Run both Sentinel news and review jobs."""
    return {
        "news": run_news_job(report_date, output_root=output_root),
        "review": run_review_job(report_date, output_root=output_root),
    }


def _sentinel_result_exit_code(result: Any, *, mode: str | None = None) -> int:
    """Map mode-aware long-horizon materialization truth to the CLI contract."""
    resolved_mode = str(mode or "").strip().lower()
    if not resolved_mode and isinstance(result, dict):
        declared_mode = str(result.get("mode") or "").strip().lower()
        if declared_mode:
            resolved_mode = declared_mode
        elif "news" in result:
            resolved_mode = "all"
        elif "long_horizon_summary" in result:
            resolved_mode = "news"

    if resolved_mode == "review":
        return 0
    if resolved_mode == "news":
        news_result = result
    elif resolved_mode == "all":
        if not isinstance(result, dict):
            return 1
        news_result = result.get("news")
    else:
        return 1

    if not isinstance(news_result, dict):
        return 1
    if not isinstance(news_result.get("long_horizon_summary"), dict):
        return 1
    if (
        "event_count" in news_result
        and int(news_result.get("event_count") or 0) <= 0
    ):
        return 2

    statuses: list[str] = []

    def collect(value: Any) -> None:
        if isinstance(value, dict):
            if "long_horizon_summary" in value:
                summary = value["long_horizon_summary"]
                if isinstance(summary, dict):
                    statuses.append(
                        str(summary.get("status") or "unknown").strip().lower()
                    )
                else:
                    statuses.append("unknown")
            for key, nested in value.items():
                if key != "long_horizon_summary":
                    collect(nested)
        elif isinstance(value, list):
            for nested in value:
                collect(nested)

    collect(result)
    if not statuses:
        return 1
    if any(status not in {"success", "partial", "degraded"} for status in statuses):
        return 1
    if any(status in {"partial", "degraded"} for status in statuses):
        return 2
    return 0


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
    return _sentinel_result_exit_code(result, mode=args.mode)


if __name__ == "__main__":
    raise SystemExit(main())
