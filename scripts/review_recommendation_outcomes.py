#!/usr/bin/env python3
"""Generate a portfolio-backed recommendation outcome review."""
from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime
from pathlib import Path

from app.config import PROJECT_ROOT
from app.services.recommendation_review import (
    build_recommendation_review,
    render_recommendation_review_markdown,
)


async def _main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--portfolio-path", default=None)
    parser.add_argument("--output-dir", default=str(Path(PROJECT_ROOT).parent / "data" / "sentinel" / "reports"))
    args = parser.parse_args()

    review = await build_recommendation_review(portfolio_path=args.portfolio_path)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    date_key = datetime.now().strftime("%Y-%m-%d")
    json_path = output_dir / f"{date_key}_recommendation_execution_review.json"
    md_path = output_dir / f"{date_key}_recommendation_execution_review.md"
    json_path.write_text(json.dumps(review, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(render_recommendation_review_markdown(review), encoding="utf-8")
    print(json.dumps({
        "ok": True,
        "json": str(json_path),
        "markdown": str(md_path),
        "executed": review.get("executed", {}),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
