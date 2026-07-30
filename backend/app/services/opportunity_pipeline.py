"""Account-executable market discovery and same-run scoring orchestration."""
from __future__ import annotations

import inspect
from typing import Any, Awaitable, Callable

from app.services.small_account_discovery import (
    discover_affordable_market_candidates,
)


SnapshotBuilder = Callable[[dict[str, Any]], dict[str, Any] | Awaitable[dict[str, Any]]]
Scorer = Callable[..., dict[str, Any] | Awaitable[dict[str, Any]]]


async def _resolve(value):
    return await value if inspect.isawaitable(value) else value


class OpportunityPipeline:
    """Discover broadly, filter by account reality, then score in the same run."""

    def __init__(
        self,
        *,
        discovery_source,
        snapshot_builder: SnapshotBuilder,
        scorer: Scorer | None = None,
        max_candidates: int = 30,
    ):
        self.discovery_source = discovery_source
        self.snapshot_builder = snapshot_builder
        self.scorer = scorer
        self.max_candidates = max(1, int(max_candidates))

    async def evaluate(
        self,
        *,
        trade_date: str,
        available_cash: float,
        total_assets: float,
        existing_codes: set[str] | None = None,
    ) -> dict[str, Any]:
        market_rows = (
            await self.discovery_source.fetch_fund_flow_individual()
        ) or []
        discovery = discover_affordable_market_candidates(
            market_rows=market_rows,
            available_cash=available_cash,
            total_assets=total_assets,
            existing_codes=existing_codes,
            max_candidates=self.max_candidates,
        )
        metrics = {
            **discovery["metrics"],
            "enriched_count": 0,
            "scored_count": 0,
        }
        if metrics["scanned_count"] == 0:
            return {
                "trade_date": trade_date,
                "metrics": metrics,
                "scorecards": [],
                "rejected": discovery["rejected"],
                "health": {
                    "status": "failed",
                    "error_code": "empty_market_universe",
                },
            }

        scorer = self.scorer
        if scorer is None:
            from app.services.target_scoring import score_target

            scorer = score_target

        scorecards: list[dict[str, Any]] = []
        enrichment_failed = 0
        for candidate in discovery["candidates"]:
            try:
                snapshot = await _resolve(self.snapshot_builder(candidate))
                if not isinstance(snapshot, dict) or not snapshot:
                    enrichment_failed += 1
                    continue
                metrics["enriched_count"] += 1
                snapshot.setdefault("code", candidate["code"])
                snapshot.setdefault("name", candidate["name"])
                snapshot.setdefault("market_evidence", candidate["market_evidence"])
                scorecard = await _resolve(
                    scorer(
                        snapshot,
                        available_cash=available_cash,
                        total_assets=total_assets,
                    )
                )
                if not isinstance(scorecard, dict) or not scorecard:
                    enrichment_failed += 1
                    continue
                scorecard.setdefault("code", candidate["code"])
                scorecard.setdefault("name", candidate["name"])
                scorecard["discovery_source"] = candidate["source"]
                scorecards.append(scorecard)
            except Exception:
                enrichment_failed += 1
        metrics["scored_count"] = len(scorecards)
        rejected = {
            **discovery["rejected"],
            "enrichment_failed": enrichment_failed,
        }
        return {
            "trade_date": trade_date,
            "metrics": metrics,
            "scorecards": scorecards,
            "rejected": rejected,
            "health": {
                "status": "succeeded" if scorecards else "degraded",
                "error_code": "" if scorecards else "no_scored_candidates",
            },
        }
