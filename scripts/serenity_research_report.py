#!/usr/bin/env python3
"""Generate a standalone Serenity bottleneck research report."""
import argparse
import os
import re
import sys
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, os.path.join(PROJECT_ROOT, "backend"))
sys.path.insert(0, PROJECT_ROOT)

from app.ai.serenity_analyst import build_serenity_research_report, run_serenity_pipeline
from app.ai.serenity_financial_evidence import fetch_financial_evidence
from app.data_sources.tencent_client import TencentDataSource
from daily_report import save_report_to_obsidian

DEFAULT_SERENITY_ARCHIVE_DIR = (
    "/Volumes/Aino Kishi/AI/projects/司库/01-资料采集/量化投资/Serenity研究"
)
SERENITY_ARCHIVE_DIR = os.getenv("SERENITY_SIKU_ARCHIVE_DIR", DEFAULT_SERENITY_ARCHIVE_DIR)


def _safe_title_theme(theme: str) -> str:
    cleaned = re.sub(r"[\\/:*?\"<>|#\[\]]+", "-", theme.strip())
    return cleaned[:40] or "未命名主题"


def save_serenity_report(
    theme: str,
    report_date: str | None = None,
    archive_dir: str = SERENITY_ARCHIVE_DIR,
    available_cash: float = 0,
    total_assets: float = 0,
    context: str = "",
    quote_fetcher=None,
    financial_fetcher=None,
) -> dict:
    """Run the Serenity pipeline and archive the markdown report."""
    report_date = report_date or datetime.now().strftime("%Y-%m-%d")
    pipeline = run_serenity_pipeline(
        theme,
        available_cash=available_cash,
        total_assets=total_assets,
        report_date=report_date,
        context=context,
        quote_fetcher=quote_fetcher,
        financial_fetcher=financial_fetcher,
    )
    markdown = build_serenity_research_report(pipeline)
    title = f"Serenity瓶颈选股报告-{_safe_title_theme(theme)}"
    return save_report_to_obsidian(
        markdown,
        report_date=report_date,
        archive_dir=archive_dir,
        title=title,
        push_status={"feishu_webhook": None, "error": "serenity research archive only"},
    )


async def fetch_tencent_quotes(codes: list[str]) -> dict:
    """Fetch realtime quote snapshots for Serenity market-data verification."""
    return await TencentDataSource().fetch_batch(codes)


async def fetch_serenity_financials(codes: list[str]) -> dict:
    """Fetch real financial evidence with local cache fallback."""
    return await fetch_financial_evidence(codes)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("theme", help="研究主题，例如：电网设备、AI半导体、CPO光通信、机器人")
    parser.add_argument("--date", default=None, help="报告日期，格式 YYYY-MM-DD")
    parser.add_argument("--archive-dir", default=SERENITY_ARCHIVE_DIR)
    parser.add_argument("--cash", type=float, default=0, help="账户现金，用于记录小账户约束")
    parser.add_argument("--total-assets", type=float, default=0, help="账户总资产，用于记录小账户约束")
    parser.add_argument("--context", default="", help="额外研究背景，会写入 pipeline 元数据")
    parser.add_argument("--with-quotes", action="store_true", help="联网拉取腾讯实时行情，补充一手金额/成交额/估值核验")
    parser.add_argument("--with-financials", action="store_true", help="联网拉取财报证据，补充收入/毛利率/存货/应收/现金流核验")
    args = parser.parse_args()

    result = save_serenity_report(
        args.theme,
        report_date=args.date,
        archive_dir=args.archive_dir,
        available_cash=args.cash,
        total_assets=args.total_assets,
        context=args.context,
        quote_fetcher=fetch_tencent_quotes if args.with_quotes else None,
        financial_fetcher=fetch_serenity_financials if args.with_financials else None,
    )
    print(result["report_path"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
