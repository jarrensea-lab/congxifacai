#!/usr/bin/env python3
"""Save a Codex market consultation summary into the Obsidian report directory."""
import argparse
import os
import sys
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, PROJECT_ROOT)

from daily_report import ARCHIVE_DIR, save_report_to_obsidian


def build_consultation_note(summary: str, title: str, created_at: datetime | None = None) -> str:
    """Format a mobile/Codex consultation summary as a durable Obsidian note."""
    created_at = created_at or datetime.now()
    return "\n".join([
        f"# {title}",
        "",
        f"> 生成时间: {created_at.strftime('%Y-%m-%d %H:%M:%S')}",
        "> 来源: Codex 项目内咨询",
        "",
        "## 讨论摘要",
        "",
        summary.strip() or "本次咨询暂无可归档内容。",
        "",
        "---",
        "*仅为研究讨论记录，不构成投资建议*",
    ])


def save_consultation(summary: str, report_date: str | None = None,
                      archive_dir: str = ARCHIVE_DIR, title: str = "Codex盘中讨论纪要") -> dict:
    """Save a consultation summary through the same Obsidian delivery path as daily reports."""
    report_date = report_date or datetime.now().strftime("%Y-%m-%d")
    note = build_consultation_note(summary, title)
    return save_report_to_obsidian(
        note,
        report_date=report_date,
        archive_dir=archive_dir,
        title=title,
        push_status={"feishu_webhook": None, "error": "consultation archive only"},
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=None, help="Report date, YYYY-MM-DD")
    parser.add_argument("--title", default="Codex盘中讨论纪要")
    parser.add_argument("--archive-dir", default=ARCHIVE_DIR)
    parser.add_argument("summary", nargs="*", help="Summary text. If omitted, stdin is used.")
    args = parser.parse_args()

    summary = " ".join(args.summary).strip()
    if not summary and not sys.stdin.isatty():
        summary = sys.stdin.read().strip()

    result = save_consultation(
        summary,
        report_date=args.date,
        archive_dir=args.archive_dir,
        title=args.title,
    )
    print(result["report_path"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
