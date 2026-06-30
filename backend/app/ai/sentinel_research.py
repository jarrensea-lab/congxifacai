"""Sentinel research package builder.

Sentinel packages are research inputs for debate and reporting. They summarize
news density, themes, symbols, and risk signals without emitting trade actions.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

DEFAULT_OUTPUT_ROOT = Path("data/sentinel")
DEFAULT_SERENITY_LEARNING_ARCHIVE_DIR = Path(
    "/Volumes/Aino Kishi/AI/projects/司库/01-资料采集/量化投资/恭喜发财报告"
)
RISK_KEYWORDS = ("风险", "监管", "下跌", "亏损", "减持", "处罚", "退市", "暴雷")


def _today_iso() -> str:
    return date.today().isoformat()


def _now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def _top_items(counter: Counter[str], limit: int = 10) -> list[dict[str, Any]]:
    return [{"name": name, "count": count} for name, count in counter.most_common(limit)]


def _event_excerpt(event: dict[str, Any], limit: int = 180) -> str:
    content = str(event.get("content", "")).replace("\n", " ").strip()
    return content[:limit]


def build_news_research_package(
    events: Iterable[dict[str, Any]],
    *,
    report_date: str | None = None,
) -> dict[str, Any]:
    """Build a Sentinel research package from normalized news events."""
    day = report_date or _today_iso()
    event_list = list(events)
    theme_counts: Counter[str] = Counter()
    symbol_counts: Counter[str] = Counter()
    source_counts: Counter[str] = Counter()
    risk_events: list[dict[str, Any]] = []

    for event in event_list:
        source_counts.update([str(event.get("source") or "unknown")])
        theme_counts.update(str(theme) for theme in event.get("themes", []) if theme)
        symbol_counts.update(str(symbol) for symbol in event.get("symbols", []) if symbol)
        content = str(event.get("content") or "")
        matched = [keyword for keyword in RISK_KEYWORDS if keyword in content]
        if matched:
            risk_events.append({
                "id": str(event.get("id", "")),
                "source": str(event.get("source", "")),
                "published_at": str(event.get("published_at", "")),
                "matched_keywords": matched,
                "excerpt": _event_excerpt(event),
            })

    status = "ok" if event_list else "empty"
    return {
        "date": day,
        "generated_at": _now_iso(),
        "event_count": len(event_list),
        "key_event_count": sum(1 for event in event_list if event.get("is_key") is True),
        "top_themes": _top_items(theme_counts),
        "top_symbols": _top_items(symbol_counts),
        "risk_events": risk_events[:20],
        "source_status": {
            "status": status,
            "source_counts": dict(sorted(source_counts.items())),
            "risk_keyword_count": len(risk_events),
        },
        "boundary": "research_only",
    }


def _compact_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": candidate.get("name", ""),
        "code": candidate.get("code", ""),
        "score": candidate.get("score", 0),
        "chokepoint": candidate.get("chokepoint", ""),
        "chain_position": candidate.get("chain_position", ""),
        "verify_next": candidate.get("verify_next", ""),
        "research_priority": candidate.get("research_priority", ""),
    }


def _safe_title(value: str) -> str:
    cleaned = re.sub(r"[\\/:*?\"<>|#\[\]]+", "-", value.strip())
    return cleaned[:60] or "未命名主题"


def build_serenity_deep_dives(
    top_themes: Iterable[dict[str, Any]],
    *,
    report_date: str | None = None,
    limit: int = 3,
    available_cash: float = 0,
    total_assets: float = 0,
    pipeline_runner=None,
) -> list[dict[str, Any]]:
    """Build Serenity bottleneck deep dives as Sentinel research-only inputs."""
    if pipeline_runner is None:
        from app.ai.serenity_analyst import run_serenity_pipeline

        pipeline_runner = run_serenity_pipeline
    from app.ai.serenity_analyst import build_serenity_research_report

    dives: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in top_themes:
        theme = str(item.get("name") or "").strip()
        if not theme or theme in seen:
            continue
        seen.add(theme)
        try:
            pipeline = pipeline_runner(
                theme,
                report_date=report_date,
                available_cash=available_cash,
                total_assets=total_assets,
                context="Sentinel 一周实验：热点主题进入 Serenity 产业链瓶颈深挖，作为研究输入。",
            )
            dives.append({
                "module": "serenity_bottleneck_deep_dive",
                "theme": theme,
                "theme_event_count": item.get("count", 0),
                "normalized_theme": pipeline.get("normalized_theme", theme),
                "chokepoints": (pipeline.get("chokepoints") or [])[:5],
                "top_candidates": [
                    _compact_candidate(candidate)
                    for candidate in (pipeline.get("top_candidates") or [])[:5]
                ],
                "verification_tasks": (pipeline.get("verification_tasks") or [])[:8],
                "learning_report_markdown": build_serenity_research_report(pipeline),
                "quote_status": pipeline.get("quote_status", {}),
                "financial_status": pipeline.get("financial_status", {}),
                "account_constraint": pipeline.get("account_constraint", ""),
                "boundary": "research_only",
                "experiment": {
                    "name": "sentinel_serenity_deep_dive_week_1",
                    "status": "active",
                    "note": "保留 Serenity 深度研究能力，但不单独产出选股报告或交易指令。",
                },
            })
        except Exception as exc:
            dives.append({
                "module": "serenity_bottleneck_deep_dive",
                "theme": theme,
                "theme_event_count": item.get("count", 0),
                "boundary": "research_only",
                "error": str(exc),
            })
        if len(dives) >= limit:
            break
    return dives


def persist_serenity_deep_dive_reports(
    dives: list[dict[str, Any]],
    *,
    report_date: str,
    archive_dir: str | Path = DEFAULT_SERENITY_LEARNING_ARCHIVE_DIR,
) -> list[dict[str, Any]]:
    """Persist full Serenity deep-dive Markdown for learning and keep paths in Sentinel."""
    base_dir = Path(archive_dir) / "历史数据" / "Serenity深挖" / report_date
    persisted: list[dict[str, Any]] = []
    for dive in dives:
        item = dict(dive)
        markdown = str(item.get("learning_report_markdown") or "")
        if markdown:
            base_dir.mkdir(parents=True, exist_ok=True)
            filename = f"{report_date}_Serenity深挖-{_safe_title(str(item.get('theme', '未知主题')))}.md"
            path = base_dir / filename
            path.write_text(markdown, encoding="utf-8")
            item["learning_report_path"] = str(path)
            item["learning_report_markdown"] = ""
        persisted.append(item)
    return persisted


def render_research_package_markdown(package: dict[str, Any]) -> str:
    """Render a human-readable Sentinel research package."""
    lines = [
        f"# Sentinel 研究包 - {package.get('date', _today_iso())}",
        "",
        "> 本文件是研究输入，不构成投资建议，不触发自动交易。",
        "",
        "## 数据概览",
        "",
        f"- 新闻总数：{package.get('event_count', 0)}",
        f"- 关键新闻：{package.get('key_event_count', 0)}",
        f"- 风险事件：{len(package.get('risk_events') or [])}",
        f"- 状态：{(package.get('source_status') or {}).get('status', 'unknown')}",
        "",
        "## 主题热度",
        "",
    ]
    top_themes = package.get("top_themes") or []
    if top_themes:
        for item in top_themes[:10]:
            lines.append(f"- {item.get('name')}: {item.get('count')} 条")
    else:
        lines.append("- 暂无主题聚合。")

    lines.extend(["", "## 标的提及", ""])
    top_symbols = package.get("top_symbols") or []
    if top_symbols:
        for item in top_symbols[:10]:
            lines.append(f"- {item.get('name')}: {item.get('count')} 条")
    else:
        lines.append("- 暂无标的提及。")

    lines.extend(["", "## 风险线索", ""])
    risk_events = package.get("risk_events") or []
    if risk_events:
        for item in risk_events[:10]:
            keywords = "、".join(item.get("matched_keywords") or [])
            lines.append(f"- {item.get('published_at', '')} [{keywords}] {item.get('excerpt', '')}")
    else:
        lines.append("- 暂无明显风险关键词。")

    lines.extend(["", "## Serenity 产业链瓶颈深挖", ""])
    dives = package.get("serenity_deep_dives") or []
    if dives:
        lines.append("> 本节是 Sentinel 的深度研究输入，不生成交易指令，不替代裁判和风控。")
        lines.append("")
        for dive in dives:
            lines.append(f"### {dive.get('theme', '未知主题')}")
            if dive.get("error"):
                lines.append(f"- 状态：降级，{dive.get('error')}")
                continue
            lines.append(f"- 主题热度：{dive.get('theme_event_count', 0)} 条")
            chokepoints = dive.get("chokepoints") or []
            if chokepoints:
                summary = []
                for item in chokepoints[:3]:
                    if isinstance(item, dict):
                        summary.append(str(item.get("bottleneck") or item.get("sector") or item)[:80])
                    else:
                        summary.append(str(item)[:80])
                lines.append("- 关键瓶颈：" + "；".join(summary))
            candidates = dive.get("top_candidates") or []
            if candidates:
                lines.append("- 研究候选：" + "；".join(
                    f"{item.get('name')}({item.get('code')})/{item.get('score')}"
                    for item in candidates[:5]
                ))
            if dive.get("learning_report_path"):
                lines.append(f"- 学习报告：{dive.get('learning_report_path')}")
            tasks = dive.get("verification_tasks") or []
            if tasks:
                first = tasks[0]
                task_text = first.get("task", first) if isinstance(first, dict) else first
                lines.append(f"- 下一步核验：{str(task_text)[:120]}")
    else:
        lines.append("- 暂无 Serenity 深挖输入。")

    lines.extend([
        "",
        "## 边界",
        "",
        "- Sentinel 只提供研究输入。",
        "- 交易动作必须由四人辩论、裁判和账户约束共同过滤。",
        "- 本报告不得直接转化为买卖清仓指令。",
    ])
    return "\n".join(lines) + "\n"


def persist_research_package(
    package: dict[str, Any],
    *,
    output_root: str | Path = DEFAULT_OUTPUT_ROOT,
) -> dict[str, str]:
    """Persist package JSON and Markdown report."""
    root = Path(output_root)
    day = str(package.get("date") or _today_iso())
    json_path = root / "research_packages" / f"{day}.json"
    md_path = root / "reports" / f"{day}_sentinel_research_package.md"
    json_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(package, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    md_path.write_text(render_research_package_markdown(package), encoding="utf-8")
    return {
        "research_package": str(json_path),
        "research_report": str(md_path),
    }


def load_research_package(
    report_date: str,
    *,
    output_root: str | Path = DEFAULT_OUTPUT_ROOT,
) -> dict[str, Any] | None:
    """Load a persisted Sentinel research package if present."""
    path = Path(output_root) / "research_packages" / f"{report_date}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


__all__ = [
    "build_news_research_package",
    "build_serenity_deep_dives",
    "persist_serenity_deep_dive_reports",
    "load_research_package",
    "persist_research_package",
    "render_research_package_markdown",
]
