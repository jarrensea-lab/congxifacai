"""Markdown report archive helpers for 恭喜发财."""
from __future__ import annotations

import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any


DEFAULT_ARCHIVE_DIR = "/Volumes/Aino Kishi/AI/projects/司库/01-资料采集/量化投资/恭喜发财报告"
INDEX_FILENAME = "日报索引.md"


def day_archive_dir(archive_dir: str | Path, report_date: str) -> Path:
    """Return YYYY/MM/YYYY-MM-DD archive directory for one trading day."""
    parsed = datetime.strptime(report_date, "%Y-%m-%d")
    return Path(archive_dir) / f"{parsed.year:04d}" / f"{parsed.month:02d}" / report_date


def safe_report_title(title: str) -> str:
    cleaned = re.sub(r"[\\/:*?\"<>|#\[\]]+", "-", title.strip())
    return cleaned or "未命名报告"


def save_markdown_report(
    md_content: str,
    *,
    report_date: str,
    archive_dir: str | Path = DEFAULT_ARCHIVE_DIR,
    title: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Persist one Markdown report into the trading-day folder and update day index."""
    day_dir = day_archive_dir(archive_dir, report_date)
    day_dir.mkdir(parents=True, exist_ok=True)

    filename = f"{report_date}_{safe_report_title(title)}.md"
    report_path = day_dir / filename
    report_path.write_text(md_content, encoding="utf-8")

    index_path = day_dir / INDEX_FILENAME
    index_line = f"- {report_date}: [[{filename[:-3]}]] ({filename})"
    existing = index_path.read_text(encoding="utf-8") if index_path.exists() else ""
    if index_line not in existing:
        prefix = "" if existing else f"# 恭喜发财 {report_date} 日报索引\n\n"
        separator = "" if not existing or existing.endswith("\n") else "\n"
        index_path.write_text(existing + separator + prefix + index_line + "\n", encoding="utf-8")

    return {
        "report_path": str(report_path),
        "index_path": str(index_path),
        "day_dir": str(day_dir),
    }


def archive_legacy_serenity_reports(
    archive_dir: str | Path = DEFAULT_ARCHIVE_DIR,
    history_dir_name: str = "历史数据",
) -> dict[str, list[str]]:
    """Move root-level legacy Serenity markdown reports into the history bucket."""
    root = Path(archive_dir)
    history_dir = root / history_dir_name
    moved: list[str] = []

    if not root.exists():
        return {"moved": moved}

    for path in sorted(root.glob("*Serenity*.md")):
        if not path.is_file():
            continue
        history_dir.mkdir(parents=True, exist_ok=True)
        target = history_dir / path.name
        if target.exists():
            stem = target.stem
            suffix = target.suffix
            counter = 1
            while target.exists():
                target = history_dir / f"{stem}-{counter}{suffix}"
                counter += 1
        shutil.move(str(path), str(target))
        moved.append(str(target))

    return {"moved": moved}
