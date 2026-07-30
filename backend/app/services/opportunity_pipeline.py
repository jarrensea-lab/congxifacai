"""Durable account-executable opportunity discovery and report orchestration."""
from __future__ import annotations

import asyncio
import fcntl
import hashlib
import inspect
import json
import os
from datetime import date, datetime
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Awaitable, Callable

from app.services.small_account_discovery import (
    build_account_budget_snapshot,
    discover_affordable_market_candidates,
)
from app.version import SCORE_VERSION


SnapshotBuilder = Callable[[dict[str, Any]], dict[str, Any] | Awaitable[dict[str, Any]]]
Scorer = Callable[..., dict[str, Any] | Awaitable[dict[str, Any]]]
STAGES = (
    "account_snapshot",
    "market_discovery",
    "affordability_filter",
    "data_enrichment",
    "scoring",
    "lifecycle_apply",
    "report_render",
    "report_validate",
    "delivery",
    "delivery_verify",
)


async def _resolve(value):
    return await value if inspect.isawaitable(value) else value


def _digest_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _stored_score(item: dict[str, Any] | None) -> float:
    payload = item if isinstance(item, dict) else {}
    scoring = payload.get("scoring_decision")
    promotion = payload.get("promotion_evidence")
    try:
        return float(
            payload.get("score")
            or (scoring.get("score") if isinstance(scoring, dict) else 0)
            or (promotion.get("score") if isinstance(promotion, dict) else 0)
            or 0
        )
    except (TypeError, ValueError):
        return 0.0


def _lifecycle_only_scorecards(discovery: dict[str, Any]) -> list[dict[str, Any]]:
    rejected = discovery.get("rejected")
    rejected = rejected if isinstance(rejected, dict) else {}
    rows: list[dict[str, Any]] = []
    for item in rejected.get("priority_budget_blocked") or []:
        rows.append({
            "code": str(item.get("code") or "").strip(),
            "name": str(item.get("name") or item.get("code") or "").strip(),
            "score": 0,
            "grade": "C",
            "action": "watch",
            "block_reason": "lot_size_exceeded",
            "decision_reason": "最小交易单位金额超过当前单票预算",
            "current_price": item.get("price"),
            "lifecycle_only": True,
            "discovery_source": "target_pool_daily_refresh",
        })
    for item in rejected.get("priority_missing") or []:
        rows.append({
            "code": str(item.get("code") or "").strip(),
            "name": str(item.get("name") or item.get("code") or "").strip(),
            "score": 0,
            "grade": "C",
            "action": "watch",
            "block_reason": "missing_required_data",
            "decision_reason": "今日全市场数据中未找到该标的，不能维持原操作级别",
            "lifecycle_only": True,
            "discovery_source": "target_pool_daily_refresh",
        })
    return rows


def apply_scorecard_lifecycle(
    store,
    scorecards: list[dict[str, Any]],
    *,
    available_cash: float,
    total_assets: float,
) -> list[dict[str, Any]]:
    """Apply deterministic entry, downgrade, and removal rules in one store write."""
    from app.services.target_lifecycle_events import classify_lifecycle_event

    payload = store.load()
    before = payload.get("items", {})
    before = before if isinstance(before, dict) else {}
    targets: list[dict[str, Any]] = []
    applied: list[tuple[dict[str, Any], dict[str, Any] | None]] = []
    for raw in scorecards:
        scorecard = dict(raw)
        code = str(scorecard.get("code") or "").strip()
        if not code:
            continue
        previous = before.get(code)
        previous = previous if isinstance(previous, dict) else None
        score = float(scorecard.get("score") or 0)
        block_reason = str(scorecard.get("block_reason") or "").strip()
        previous_decision = (
            previous.get("scoring_decision")
            if isinstance(previous, dict)
            and isinstance(previous.get("scoring_decision"), dict)
            else {}
        )
        previous_streak = int(previous_decision.get("below_retention_streak") or 0)
        if previous is None and (
            score < 65 or block_reason == "long_thesis_broken"
        ):
            continue
        if block_reason == "long_thesis_broken":
            status = "removed"
            streak = max(1, previous_streak + 1)
        elif score < 65:
            if previous is None:
                continue
            streak = previous_streak + 1
            status = "removed" if streak >= 2 else "watching"
            if not block_reason:
                scorecard["block_reason"] = "score_below_retention_threshold"
        else:
            streak = 0
            action = str(scorecard.get("action") or "").strip().lower()
            status = (
                "executable"
                if action in {"buy", "add", "actionable", "executable"}
                else "watching"
            )
        scorecard["below_retention_streak"] = streak
        name = str(scorecard.get("name") or "").strip()
        if not name or name == code:
            name = str((previous or {}).get("name") or code)
        source = str(
            (previous or {}).get("source")
            if scorecard.get("lifecycle_only") and previous
            else scorecard.get("discovery_source")
            or (previous or {}).get("source")
            or "dynamic_market_discovery"
        )
        targets.append({
            "code": code,
            "name": name,
            "status": status,
            "source": source,
            "evidence": {
                "decision_reason": scorecard.get("decision_reason", ""),
                "next_signal": scorecard.get("next_signal", ""),
            },
            "promotion_evidence": scorecard.get("promotion_evidence") or {},
            "scoring_decision": scorecard,
            "current_price": scorecard.get("current_price")
            or scorecard.get("entry_price"),
            "available_cash": available_cash,
            "total_assets": total_assets,
        })
        applied.append((scorecard, previous))
    if targets:
        store.upsert_targets(targets, payload=payload)
    after = payload.get("items", {})
    events = []
    for scorecard, previous in applied:
        code = str(scorecard.get("code") or "").strip()
        current = dict(scorecard)
        current["status"] = (after.get(code) or {}).get(
            "status",
            scorecard.get("status") or scorecard.get("action"),
        )
        previous_event = dict(previous) if isinstance(previous, dict) else None
        if previous_event is not None:
            previous_event["score"] = _stored_score(previous)
        events.append(
            classify_lifecycle_event(
                previous=previous_event,
                current=current,
            )
        )
    return events


def build_deduplicating_deliverer(
    receipt_dir: str | Path,
    deliverer,
):
    """Avoid repeating an already completed delivery for one run and report."""
    root = Path(receipt_dir)

    async def deliver(content: str, result: dict[str, Any]) -> dict[str, Any]:
        run_id = str(result.get("run_id") or "")
        content_digest = _digest_bytes(content.encode("utf-8"))
        key = _digest_bytes(f"{run_id}:{content_digest}".encode("utf-8"))
        receipt_path = root / f"{key}.json"
        if receipt_path.is_file():
            try:
                receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
                report_path = Path(str(receipt.get("report_path") or ""))
                if (
                    report_path.is_file()
                    and _digest_bytes(report_path.read_bytes())
                    == str(receipt.get("report_digest") or "")
                ):
                    return receipt
            except (OSError, ValueError, TypeError):
                pass
        delivered = await _resolve(deliverer(content, result))
        if not isinstance(delivered, dict):
            raise RuntimeError("invalid_delivery_receipt")
        report_path = Path(str(delivered.get("report_path") or ""))
        report_digest = str(delivered.get("report_digest") or "")
        if (
            not report_path.is_file()
            or not report_digest
            or _digest_bytes(report_path.read_bytes()) != report_digest
        ):
            raise RuntimeError("delivery_receipt_mismatch")
        receipt = {
            **delivered,
            "run_id": run_id,
            "pipeline_report_digest": content_digest,
        }
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = receipt_path.with_name(
            f".{receipt_path.name}.{os.getpid()}.tmp"
        )
        temporary.write_text(
            json.dumps(receipt, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        temporary.replace(receipt_path)
        return receipt

    return deliver


class OpportunityPipeline:
    """Discover, score, report, and recover from the first incomplete stage."""

    def __init__(
        self,
        *,
        discovery_source,
        snapshot_builder: SnapshotBuilder,
        scorer: Scorer | None = None,
        max_candidates: int = 30,
        fallback_source=None,
        run_store=None,
        artifact_dir: str | Path | None = None,
        lifecycle_applier=None,
        renderer=None,
        validator=None,
        deliverer=None,
        delivery_verifier=None,
        sleeper=asyncio.sleep,
        max_fetch_attempts: int = 3,
        max_enrichment_concurrency: int = 4,
    ):
        self.discovery_source = discovery_source
        self.fallback_source = fallback_source
        self.snapshot_builder = snapshot_builder
        self.scorer = scorer
        self.max_candidates = max(1, int(max_candidates))
        self.run_store = run_store
        self.artifact_dir = Path(artifact_dir or "data/pipeline_artifacts")
        self.lifecycle_applier = lifecycle_applier
        self.renderer = renderer
        self.validator = validator
        self.deliverer = deliverer
        self.delivery_verifier = delivery_verifier
        self.sleeper = sleeper
        self.max_fetch_attempts = max(1, int(max_fetch_attempts))
        self.max_enrichment_concurrency = max(
            1,
            int(max_enrichment_concurrency),
        )

    @staticmethod
    def _promotion_evidence(
        scorecard: dict[str, Any],
        snapshot: dict[str, Any],
    ) -> dict[str, Any]:
        action = str(scorecard.get("action") or "").strip().lower()
        block_reason = str(scorecard.get("block_reason") or "").strip()
        return {
            "score_version": str(scorecard.get("score_version") or SCORE_VERSION),
            "score": float(scorecard.get("score") or 0),
            "data_cutoff_at": str(
                snapshot.get("generated_at")
                or datetime.now().astimezone().isoformat()
            ),
            "playbook": str(scorecard.get("playbook") or ""),
            "hard_gates": {
                "affordable": True,
                "data_complete": not bool(scorecard.get("missing_data")),
                "risk_ok": block_reason
                not in {"risk_budget_too_small", "lot_size_exceeded"},
                "tradeable": True,
                "playbook_triggered": action
                in {"buy", "add", "actionable", "executable"},
            },
        }

    async def _score_snapshots(
        self,
        snapshots: list[dict[str, Any]],
        *,
        available_cash: float,
        total_assets: float,
    ) -> tuple[list[dict[str, Any]], int]:
        scorer = self.scorer
        if scorer is None:
            from app.services.target_scoring import score_target

            scorer = score_target
        scorecards: list[dict[str, Any]] = []
        failed = 0
        for snapshot in snapshots:
            try:
                scorecard = await _resolve(
                    scorer(
                        snapshot,
                        available_cash=available_cash,
                        total_assets=total_assets,
                    )
                )
                if not isinstance(scorecard, dict) or not scorecard:
                    failed += 1
                    continue
                scorecard.setdefault("code", snapshot.get("code", ""))
                scorecard.setdefault("name", snapshot.get("name", ""))
                scorecard["promotion_evidence"] = self._promotion_evidence(
                    scorecard,
                    snapshot,
                )
                scorecards.append(scorecard)
            except Exception:
                failed += 1
        return scorecards, failed

    async def _enrich_candidates(
        self,
        candidates: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], int]:
        semaphore = asyncio.Semaphore(self.max_enrichment_concurrency)

        async def enrich(candidate: dict[str, Any]):
            async with semaphore:
                try:
                    snapshot = await _resolve(self.snapshot_builder(candidate))
                except Exception:
                    return None
                if not isinstance(snapshot, dict) or not snapshot:
                    return None
                snapshot.setdefault("code", candidate["code"])
                snapshot.setdefault("name", candidate["name"])
                snapshot.setdefault(
                    "market_evidence",
                    candidate["market_evidence"],
                )
                snapshot["discovery_source"] = candidate["source"]
                return snapshot

        resolved = await asyncio.gather(
            *(enrich(candidate) for candidate in candidates)
        )
        snapshots = [
            snapshot for snapshot in resolved if isinstance(snapshot, dict)
        ]
        return snapshots, len(candidates) - len(snapshots)

    async def evaluate(
        self,
        *,
        trade_date: str,
        available_cash: float,
        total_assets: float,
        existing_codes: set[str] | None = None,
        priority_codes: set[str] | None = None,
    ) -> dict[str, Any]:
        """Non-persistent evaluation retained for focused callers and tests."""
        market_rows = (
            await self.discovery_source.fetch_fund_flow_individual()
        ) or []
        discovery = discover_affordable_market_candidates(
            market_rows=market_rows,
            available_cash=available_cash,
            total_assets=total_assets,
            existing_codes=existing_codes,
            priority_codes=priority_codes,
            max_candidates=self.max_candidates,
        )
        metrics = {**discovery["metrics"], "enriched_count": 0, "scored_count": 0}
        if metrics["scanned_count"] == 0:
            return {
                "trade_date": trade_date,
                "metrics": metrics,
                "scorecards": [],
                "rejected": discovery["rejected"],
                "health": {"status": "failed", "error_code": "empty_market_universe"},
            }
        snapshots, enrichment_failed = await self._enrich_candidates(
            discovery["candidates"]
        )
        metrics["enriched_count"] = len(snapshots)
        scorecards, scoring_failed = await self._score_snapshots(
            snapshots,
            available_cash=available_cash,
            total_assets=total_assets,
        )
        scorecards.extend(_lifecycle_only_scorecards(discovery))
        for scorecard in scorecards:
            scorecard["discovery_source"] = next(
                (
                    item.get("discovery_source")
                    for item in snapshots
                    if item.get("code") == scorecard.get("code")
                ),
                "dynamic_market_discovery",
            )
        metrics["scored_count"] = sum(
            not bool(item.get("lifecycle_only")) for item in scorecards
        )
        rejected = {
            **discovery["rejected"],
            "enrichment_failed": enrichment_failed,
            "scoring_failed": scoring_failed,
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

    def _artifact_path(self, run_id: str, stage: str) -> Path:
        safe_run_id = run_id.replace(":", "_").replace("/", "_")
        return self.artifact_dir / safe_run_id / f"{stage}.json"

    def _write_artifact(self, run_id: str, stage: str, value: Any) -> tuple[str, str]:
        path = self._artifact_path(run_id, stage)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        ).encode("utf-8")
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        temporary.write_bytes(payload)
        temporary.replace(path)
        return str(path), _digest_bytes(payload)

    @contextmanager
    def _run_lock(self, run_id: str):
        lock_path = self._artifact_path(run_id, "pipeline_run").with_suffix(
            ".lock"
        )
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = lock_path.open("a+")
        acquired = False
        try:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except BlockingIOError:
                pass
            yield acquired
        finally:
            if acquired:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()

    def _busy_result(self, run_id: str) -> dict[str, Any]:
        return {
            "run_id": run_id,
            "status": "running",
            "health": {
                "status": "degraded",
                "error_code": "pipeline_run_already_active",
            },
            "stages": self.run_store.stages_for_run(run_id),
        }

    def _read_artifact(self, stage: dict[str, Any]) -> Any:
        path = Path(str(stage.get("artifact_path") or ""))
        payload = path.read_bytes()
        digest = _digest_bytes(payload)
        if digest != str(stage.get("artifact_digest") or ""):
            raise RuntimeError("artifact_digest_mismatch")
        return json.loads(payload.decode("utf-8"))

    def _start_stage(self, run_id: str, stage_name: str, input_count: int = 0):
        stages = self.run_store.stages_for_run(run_id)
        current = stages.get(stage_name)
        if current is None:
            return self.run_store.start_stage(
                run_id,
                stage_name,
                input_count=input_count,
            )
        return self.run_store.resume_stage(run_id, stage_name)

    def _finish(
        self,
        run_id: str,
        stage_name: str,
        attempt: int,
        value: Any,
        *,
        status: str = "succeeded",
        error_code: str = "",
        recovery_action: str = "",
        output_count: int = 1,
    ) -> None:
        path, digest = self._write_artifact(run_id, stage_name, value)
        self.run_store.finish_stage(
            run_id,
            stage_name,
            attempt=attempt,
            status=status,
            output_count=output_count,
            data_cutoff_at=datetime.now().astimezone().isoformat(),
            artifact_path=path,
            artifact_digest=digest,
            error_code=error_code,
            recovery_action=recovery_action,
        )

    def _result(self, run_id: str, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "run_id": run_id,
            "trade_date": context.get("trade_date", ""),
            "account": context.get("account_snapshot", {}),
            "metrics": context.get("metrics", {}),
            "scorecards": context.get("scoring", []),
            "lifecycle_events": context.get("lifecycle_apply", []),
            "health": context.get("health", {"status": "degraded"}),
            "report_validation": context.get("report_validate", {}),
            "delivery": context.get("delivery", {}),
            "stages": self.run_store.stages_for_run(run_id),
        }

    async def _market_discovery(
        self,
        run_id: str,
    ) -> tuple[list[dict[str, Any]], str]:
        rows: list[dict[str, Any]] = []
        stage = None
        for attempt_index in range(self.max_fetch_attempts):
            stage = self._start_stage(run_id, "market_discovery")
            try:
                rows = (
                    await self.discovery_source.fetch_fund_flow_individual()
                ) or []
            except Exception:
                rows = []
            if rows:
                self._finish(
                    run_id,
                    "market_discovery",
                    stage["attempt"],
                    rows,
                    output_count=len(rows),
                )
                return rows, "succeeded"
            if attempt_index < self.max_fetch_attempts - 1:
                self.run_store.finish_stage(
                    run_id,
                    "market_discovery",
                    attempt=stage["attempt"],
                    status="failed",
                    error_code="empty_market_universe",
                    recovery_action="bounded_retry",
                )
                await _resolve(self.sleeper(2**attempt_index))

        if self.fallback_source is not None:
            try:
                rows = (
                    await self.fallback_source.fetch_fund_flow_individual()
                ) or []
            except Exception:
                rows = []
        if rows and stage is not None:
            self._finish(
                run_id,
                "market_discovery",
                stage["attempt"],
                rows,
                status="degraded",
                error_code="primary_empty_market_universe",
                recovery_action="fallback_source",
                output_count=len(rows),
            )
            return rows, "degraded"
        if stage is not None:
            self.run_store.finish_stage(
                run_id,
                "market_discovery",
                attempt=stage["attempt"],
                status="failed",
                error_code="empty_market_universe",
                recovery_action="manual_data_source_check",
            )
        return [], "failed"

    async def _execute_from(
        self,
        run_id: str,
        start_index: int,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        available_cash = float(context["account_snapshot"]["available_cash"])
        total_assets = float(context["account_snapshot"]["total_assets"])
        existing_codes = set(context["account_snapshot"].get("existing_codes") or [])
        priority_codes = set(context["account_snapshot"].get("priority_codes") or [])

        for stage_name in STAGES[start_index:]:
            if stage_name == "account_snapshot":
                continue
            if stage_name == "market_discovery":
                rows, status = await self._market_discovery(run_id)
                if status == "failed":
                    context["health"] = {
                        "status": "failed",
                        "error_code": "empty_market_universe",
                    }
                    break
                context[stage_name] = rows
                if status == "degraded":
                    context["health"] = {
                        "status": "degraded",
                        "error_code": "primary_empty_market_universe",
                    }
                continue

            input_value: Any = None
            stage = self._start_stage(run_id, stage_name)
            try:
                if stage_name == "affordability_filter":
                    input_value = context["market_discovery"]
                    value = discover_affordable_market_candidates(
                        market_rows=input_value,
                        available_cash=available_cash,
                        total_assets=total_assets,
                        existing_codes=existing_codes,
                        priority_codes=priority_codes,
                        max_candidates=self.max_candidates,
                    )
                    context["metrics"] = {
                        **value["metrics"],
                        "enriched_count": 0,
                        "scored_count": 0,
                    }
                elif stage_name == "data_enrichment":
                    input_value = context["affordability_filter"]["candidates"]
                    value, enrichment_failed = (
                        await self._enrich_candidates(input_value)
                    )
                    context["metrics"]["enriched_count"] = len(value)
                    context["rejected"] = {
                        **context["affordability_filter"].get("rejected", {}),
                        "enrichment_failed": enrichment_failed,
                    }
                elif stage_name == "scoring":
                    input_value = context["data_enrichment"]
                    value, failed = await self._score_snapshots(
                        input_value,
                        available_cash=available_cash,
                        total_assets=total_assets,
                    )
                    value.extend(
                        _lifecycle_only_scorecards(
                            context["affordability_filter"]
                        )
                    )
                    context["metrics"]["scored_count"] = sum(
                        not bool(item.get("lifecycle_only")) for item in value
                    )
                    context["rejected"] = {
                        **context["affordability_filter"].get("rejected", {}),
                        "scoring_failed": failed,
                    }
                    if not value:
                        context["health"] = {
                            "status": "degraded",
                            "error_code": "no_scored_candidates",
                        }
                elif stage_name == "lifecycle_apply":
                    input_value = context["scoring"]
                    if self.lifecycle_applier is None:
                        from app.services.target_lifecycle_events import (
                            classify_lifecycle_event,
                        )

                        value = [
                            classify_lifecycle_event(previous=None, current=item)
                            for item in input_value
                        ]
                    else:
                        value = await _resolve(self.lifecycle_applier(input_value))
                elif stage_name == "report_render":
                    input_value = self._result(run_id, context)
                    if self.renderer is None:
                        from scripts.daily_report import build_v9_opportunity_section

                        value = "\n".join(build_v9_opportunity_section(input_value))
                    else:
                        value = await _resolve(self.renderer(input_value))
                elif stage_name == "report_validate":
                    input_value = context["report_render"]
                    if self.validator is None:
                        from app.services.report_validation import validate_report

                        value = validate_report(
                            input_value,
                            self._result(run_id, context),
                        )
                    else:
                        value = await _resolve(
                            self.validator(input_value, self._result(run_id, context))
                        )
                    if not value.get("ok"):
                        raise RuntimeError("report_semantic_validation_failed")
                elif stage_name == "delivery":
                    input_value = context["report_render"]
                    if self.deliverer is None:
                        report_path = self._artifact_path(run_id, "final_report").with_suffix(
                            ".md"
                        )
                        report_path.parent.mkdir(parents=True, exist_ok=True)
                        temporary = report_path.with_name(
                            f".{report_path.name}.{os.getpid()}.tmp"
                        )
                        temporary.write_text(input_value, encoding="utf-8")
                        temporary.replace(report_path)
                        value = {
                            "report_path": str(report_path),
                            "report_digest": _digest_bytes(
                                input_value.encode("utf-8")
                            ),
                        }
                    else:
                        value = await _resolve(
                            self.deliverer(input_value, self._result(run_id, context))
                        )
                elif stage_name == "delivery_verify":
                    input_value = context["delivery"]
                    if self.delivery_verifier is None:
                        report_path = Path(str(input_value.get("report_path") or ""))
                        ok = report_path.is_file()
                        if ok:
                            ok = _digest_bytes(report_path.read_bytes()) == str(
                                input_value.get("report_digest") or ""
                            )
                        value = {"ok": ok}
                    else:
                        value = await _resolve(self.delivery_verifier(input_value))
                    if not value.get("ok"):
                        raise RuntimeError("delivery_verification_failed")
                else:  # pragma: no cover
                    raise RuntimeError(f"unknown_stage:{stage_name}")

                context[stage_name] = value
                output_count = len(value) if isinstance(value, (list, dict)) else 1
                self._finish(
                    run_id,
                    stage_name,
                    stage["attempt"],
                    value,
                    output_count=output_count,
                )
            except Exception as exc:
                self.run_store.finish_stage(
                    run_id,
                    stage_name,
                    attempt=stage["attempt"],
                    status="failed",
                    error_code=exc.__class__.__name__,
                    recovery_action=f"resume_from:{stage_name}",
                )
                context["health"] = {
                    "status": "failed",
                    "error_code": f"{stage_name}_failed",
                }
                break
        else:
            context.setdefault("health", {"status": "succeeded", "error_code": ""})
        return self._result(run_id, context)

    async def run(
        self,
        trade_date: str,
        *,
        available_cash: float,
        total_assets: float,
        existing_codes: set[str] | None = None,
        priority_codes: set[str] | None = None,
    ) -> dict[str, Any]:
        if self.run_store is None:
            raise RuntimeError("pipeline_run_store_required")
        run = self.run_store.begin_run(trade_date)
        run_id = str(run["run_id"])
        stages = self.run_store.stages_for_run(run_id)
        if stages:
            return await self.recover(run_id)
        with self._run_lock(run_id) as acquired:
            if not acquired:
                return self._busy_result(run_id)
            stages = self.run_store.stages_for_run(run_id)
            if stages:
                return await self._recover_unlocked(run_id)
            account = {
                **build_account_budget_snapshot(
                    available_cash=available_cash,
                    total_assets=total_assets,
                ),
                "existing_codes": sorted(existing_codes or set()),
                "priority_codes": sorted(priority_codes or set()),
            }
            stage = self.run_store.start_stage(run_id, "account_snapshot")
            self._finish(
                run_id,
                "account_snapshot",
                stage["attempt"],
                account,
                output_count=1,
            )
            context = {"trade_date": trade_date, "account_snapshot": account}
            return await self._execute_from(run_id, 1, context)

    async def recover(self, run_id: str) -> dict[str, Any]:
        if self.run_store is None:
            raise RuntimeError("pipeline_run_store_required")
        with self._run_lock(run_id) as acquired:
            if not acquired:
                return self._busy_result(run_id)
            return await self._recover_unlocked(run_id)

    async def _recover_unlocked(self, run_id: str) -> dict[str, Any]:
        stages = self.run_store.stages_for_run(run_id)
        context: dict[str, Any] = {
            "trade_date": run_id.rsplit(":", 1)[-1],
        }
        start_index = len(STAGES)
        for index, stage_name in enumerate(STAGES):
            stage = stages.get(stage_name)
            if stage and stage.get("status") in {"succeeded", "degraded"}:
                context[stage_name] = self._read_artifact(stage)
                if stage_name == "affordability_filter":
                    value = context[stage_name]
                    context["metrics"] = {
                        **value.get("metrics", {}),
                        "enriched_count": 0,
                        "scored_count": 0,
                    }
                elif stage_name == "data_enrichment":
                    context["metrics"]["enriched_count"] = len(context[stage_name])
                elif stage_name == "scoring":
                    context["metrics"]["scored_count"] = sum(
                        not bool(item.get("lifecycle_only"))
                        for item in context[stage_name]
                    )
                if stage.get("status") == "degraded":
                    context["health"] = {
                        "status": "degraded",
                        "error_code": stage.get("error_code", ""),
                    }
                continue
            start_index = index
            break
        if "account_snapshot" not in context:
            account = self._load_service_account()
            stage = self._start_stage(run_id, "account_snapshot")
            self._finish(
                run_id,
                "account_snapshot",
                stage["attempt"],
                account,
                recovery_action="reload_portfolio_snapshot",
            )
            context["account_snapshot"] = account
            start_index = 1
        return await self._execute_from(run_id, start_index, context)

    def _load_service_account(self) -> dict[str, Any]:
        from app.services.quant_lifecycle import TargetPoolStore

        portfolio_path = Path(
            os.getenv("CONGXI_PORTFOLIO_PATH", "data/user_portfolio.json")
        )
        portfolio = json.loads(portfolio_path.read_text(encoding="utf-8"))
        cash = float(
            portfolio.get("available_cash")
            or portfolio.get("cash")
            or 0
        )
        positions = [
            item
            for item in portfolio.get("positions") or []
            if isinstance(item, dict)
        ]
        market_value = sum(
            float(item.get("current_value") or item.get("market_value") or 0)
            for item in positions
        )
        reported_assets = float(portfolio.get("total_assets") or 0)
        holdings_value = float(
            portfolio.get("total_value")
            if portfolio.get("total_value") is not None
            else market_value
        )
        total_assets = reported_assets or cash + holdings_value
        pool_items = TargetPoolStore().load().get("items", {})
        priority_codes = {
            str(code).strip()
            for code, item in pool_items.items()
            if isinstance(item, dict)
            and str(item.get("status") or "").strip().lower()
            not in {"removed", "expired", "position"}
        }
        protected_codes = {
            str(code).strip()
            for code, item in pool_items.items()
            if isinstance(item, dict)
            and str(item.get("status") or "").strip().lower()
            in {"removed", "expired"}
        }
        return {
            **build_account_budget_snapshot(
                available_cash=cash,
                total_assets=total_assets,
            ),
            "existing_codes": sorted({
                str(item.get("code") or "").strip()
                for item in positions
                if str(item.get("code") or "").strip()
            } | protected_codes),
            "priority_codes": sorted(priority_codes),
        }

    async def run_for_service_date(self) -> dict[str, Any]:
        account = self._load_service_account()
        return await self.run(
            os.getenv("CONGXI_REPORT_DATE") or date.today().isoformat(),
            available_cash=account["available_cash"],
            total_assets=account["total_assets"],
            existing_codes=set(account["existing_codes"]),
            priority_codes=set(account["priority_codes"]),
        )

    async def recover_due_run(self) -> dict[str, Any]:
        run_id = self.run_store.run_id_for_trade_date(
            os.getenv("CONGXI_REPORT_DATE") or date.today().isoformat()
        )
        if not run_id:
            return {"status": "not_due"}
        return await self.recover(run_id)

    async def verify_due_delivery(self) -> dict[str, Any]:
        result = await self.recover_due_run()
        if result.get("status") == "not_due":
            return result
        return {
            "status": "verified"
            if result.get("stages", {}).get("delivery_verify", {}).get("status")
            == "succeeded"
            else "failed",
            **result,
        }


def build_default_pipeline() -> OpportunityPipeline:
    """Build the production v9 pipeline using existing read-only data adapters."""
    from app.ai.serenity_financial_evidence import fetch_financial_evidence
    from app.config import resolve_runtime_pipeline_db_path, resolve_runtime_state_dir
    from app.data_sources.akshare_market import AKShareMarketClient
    from app.data_sources.akshare_news import AKShareNewsClient
    from app.data_sources.realtime_market_data import FastRealtimeMarketDataSource
    from app.services.pipeline_run_store import PipelineRunStore
    from app.services.target_snapshot import build_target_snapshot

    class CachedMarketSource:
        def __init__(self):
            self.client = AKShareMarketClient()
            self.fund_flows = None
            self.northbound = None
            self.fund_flow_lock = asyncio.Lock()
            self.northbound_lock = asyncio.Lock()

        async def fetch_fund_flow_individual(self):
            if not self.fund_flows:
                async with self.fund_flow_lock:
                    if not self.fund_flows:
                        self.fund_flows = (
                            await self.client.fetch_fund_flow_individual()
                        )
            return self.fund_flows

        async def fetch_hsgt_flow(self):
            if self.northbound is None:
                async with self.northbound_lock:
                    if self.northbound is None:
                        self.northbound = await self.client.fetch_hsgt_flow()
            return self.northbound

    market_source = CachedMarketSource()
    quote_source = FastRealtimeMarketDataSource()
    news_source = AKShareNewsClient()
    market_regime_cache = None
    market_regime_lock = asyncio.Lock()

    async def current_market_regime() -> dict[str, Any]:
        nonlocal market_regime_cache
        if market_regime_cache is not None:
            return market_regime_cache
        async with market_regime_lock:
            if market_regime_cache is not None:
                return market_regime_cache
            codes = ("sh000001", "sz399001", "sz399006")
            try:
                quotes = await quote_source.fetch_batch(list(codes))
            except Exception:
                quotes = {}
            changes = [
                float((quotes.get(code) or {}).get("change_pct") or 0)
                for code in codes
                if (quotes.get(code) or {}).get("price")
            ]
            if not changes:
                market_regime_cache = {
                    "status": "missing",
                    "label": "data_unavailable",
                    "reason": "主要指数行情缺失，新开仓安全阻断。",
                }
            else:
                average_change = sum(changes) / len(changes)
                market_regime_cache = {
                    "status": "ok",
                    "label": (
                        "panic"
                        if min(changes) <= -2.0
                        else "downtrend"
                        if average_change <= -1.5
                        else "neutral"
                    ),
                    "index_change_pct": round(average_change, 2),
                }
            return market_regime_cache

    async def snapshot_builder(candidate: dict[str, Any]) -> dict[str, Any]:
        snapshot = await build_target_snapshot(
            candidate["code"],
            name=candidate.get("name", ""),
            quote_source=quote_source,
            market_source=market_source,
            news_source=news_source,
            financial_fetcher=fetch_financial_evidence,
        )
        snapshot["market_regime"] = dict(await current_market_regime())
        return snapshot

    async def deliver_once(_content: str, result: dict[str, Any]) -> dict[str, Any]:
        from scripts import daily_report

        report_path = await daily_report.main(v9_pipeline_result=result)
        if not report_path:
            raise RuntimeError("daily_report_missing")
        path = Path(report_path)
        return {
            "report_path": str(path),
            "report_digest": _digest_bytes(path.read_bytes()),
        }

    def apply_lifecycle(scorecards: list[dict[str, Any]]) -> list[dict[str, Any]]:
        from app.services.quant_lifecycle import TargetPoolStore

        store = TargetPoolStore()
        portfolio_path = Path(
            os.getenv("CONGXI_PORTFOLIO_PATH", "data/user_portfolio.json")
        )
        portfolio = json.loads(portfolio_path.read_text(encoding="utf-8"))
        available_cash = float(
            portfolio.get("available_cash") or portfolio.get("cash") or 0
        )
        reported_assets = float(portfolio.get("total_assets") or 0)
        holdings_value = float(portfolio.get("total_value") or 0)
        total_assets = reported_assets or available_cash + holdings_value
        return apply_scorecard_lifecycle(
            store,
            scorecards,
            available_cash=available_cash,
            total_assets=total_assets,
        )

    state_dir = resolve_runtime_state_dir()
    deliver = build_deduplicating_deliverer(
        state_dir / "delivery_receipts",
        deliver_once,
    )
    return OpportunityPipeline(
        discovery_source=market_source,
        snapshot_builder=snapshot_builder,
        max_candidates=int(
            os.getenv("CONGXI_OPPORTUNITY_MAX_CANDIDATES", "30")
        ),
        max_enrichment_concurrency=int(
            os.getenv("CONGXI_ENRICHMENT_CONCURRENCY", "4")
        ),
        run_store=PipelineRunStore(resolve_runtime_pipeline_db_path()),
        artifact_dir=state_dir / "pipeline_artifacts",
        lifecycle_applier=apply_lifecycle,
        deliverer=deliver,
    )
