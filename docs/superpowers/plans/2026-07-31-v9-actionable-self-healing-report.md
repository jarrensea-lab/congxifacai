# 恭喜发财 v9.0 可执行机会与自愈报告 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立一条以真实现金可执行性为前置条件、每天主动发现并解释标的、能够记录阶段状态和自动恢复的 v9.0 日报管线。

**Architecture:** 新增版本真值、阶段账本、综合评分卡、生命周期事件和报告校验五个小型服务，由 `opportunity_pipeline.py` 统一编排。现有 `daily_report.py` 保留账户、持仓、归档和飞书适配能力，但不再自行拼接“先评分旧池、再发现新股”的流程；APScheduler 只调用幂等主管线和守望补跑入口。

**Tech Stack:** Python 3.12、FastAPI、APScheduler、SQLite、pytest、现有 Tencent/AKShare/Tushare/Sentinel/Serenity 数据适配器、飞书 OpenAPI/Webhook。

---

## 文件结构

- Create `backend/app/version.py`：产品、管线、评分和运行身份的单一真值。
- Create `backend/app/services/pipeline_run_store.py`：SQLite 阶段账本和幂等运行状态。
- Create `backend/app/services/composite_score.py`：100 分综合评分和用户可读理由。
- Create `backend/app/services/target_lifecycle_events.py`：新增、保留、降级、剔除事件判定。
- Create `backend/app/services/report_validation.py`：报告内容和交付产物语义校验。
- Create `backend/app/services/opportunity_pipeline.py`：发现、过滤、补齐、评分、生命周期和恢复编排。
- Modify `backend/app/services/small_account_discovery.py`：返回全市场扫描统计和可负担宽候选，不再永久写入 `research_only`。
- Modify `backend/app/services/target_scoring.py`：接入综合评分卡，保留硬门和交易剧本。
- Modify `backend/app/services/target_snapshot.py`：空研究对象不得标记为健康。
- Modify `backend/app/services/quant_lifecycle.py`：接受确定性 `promotion_evidence`，移除动态候选永久晋级死路。
- Modify `scripts/daily_report.py`：调用主管线结果并渲染最多三只可操作标的及完整变化清单。
- Modify `backend/app/main.py`：注册主管线、20:45 恢复检查和 21:15 交付验真。
- Modify `backend/app/routers/market.py`：健康接口返回统一 v9 运行身份和最近管线状态。
- Modify `README.md`、`CHANGELOG.md`：记录 v9 能力、运行边界和验证证据。
- Create/Modify tests under `backend/tests/`：覆盖每个新服务和端到端恢复。

### Task 1: 统一 v9 版本真值

**Files:**
- Create: `backend/app/version.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/routers/market.py`
- Test: `backend/tests/test_runtime_regressions.py`

- [ ] **Step 1: Write the failing version identity tests**

```python
def test_v9_runtime_identity_is_single_source(monkeypatch):
    monkeypatch.setenv("CONGXI_BUILD_COMMIT", "abc1234")
    from app.version import build_runtime_identity

    identity = build_runtime_identity(started_at="2026-07-31T08:00:00+08:00")
    assert identity == {
        "product_version": "v9.0.0-dev",
        "pipeline_version": "actionable_pipeline_v1",
        "score_version": "composite_score_v1",
        "commit": "abc1234",
        "started_at": "2026-07-31T08:00:00+08:00",
    }


@pytest.mark.asyncio
async def test_health_endpoint_uses_runtime_identity():
    from app.routers.market import health_check

    result = await health_check()
    assert result["runtime"]["product_version"] == "v9.0.0-dev"
    assert result["version"] == result["runtime"]["product_version"]
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
PYTHONPATH=.:backend .venv/bin/python -m pytest \
  backend/tests/test_runtime_regressions.py::test_v9_runtime_identity_is_single_source \
  backend/tests/test_runtime_regressions.py::test_health_endpoint_uses_runtime_identity -q
```

Expected: FAIL because `app.version` and the `runtime` health payload do not exist.

- [ ] **Step 3: Implement the version module and replace hard-coded user-visible versions**

```python
# backend/app/version.py
from __future__ import annotations

import os
import subprocess
from datetime import datetime

PRODUCT_VERSION = "v9.0.0-dev"
PIPELINE_VERSION = "actionable_pipeline_v1"
SCORE_VERSION = "composite_score_v1"


def _commit() -> str:
    configured = os.getenv("CONGXI_BUILD_COMMIT", "").strip()
    if configured:
        return configured
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        ).stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def build_runtime_identity(*, started_at: str | None = None) -> dict[str, str]:
    return {
        "product_version": PRODUCT_VERSION,
        "pipeline_version": PIPELINE_VERSION,
        "score_version": SCORE_VERSION,
        "commit": _commit(),
        "started_at": started_at or datetime.now().astimezone().isoformat(),
    }
```

`backend/app/main.py` 的 FastAPI `version`、启动日志和所有“旺财V7/V7.5”标题改为读取 `PRODUCT_VERSION`；`backend/app/routers/market.py` 返回同一个进程启动时创建的 runtime identity。

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the Step 2 command. Expected: `2 passed`.

- [ ] **Step 5: Commit**

```bash
git add backend/app/version.py backend/app/main.py backend/app/routers/market.py backend/tests/test_runtime_regressions.py
git commit -m "feat: unify congxi v9 runtime identity"
```

### Task 2: 建立原子化阶段账本

**Files:**
- Create: `backend/app/services/pipeline_run_store.py`
- Create: `backend/tests/test_pipeline_run_store.py`
- Modify: `backend/app/config.py`

- [ ] **Step 1: Write failing persistence and resume tests**

```python
def test_pipeline_store_resumes_failed_stage_without_duplicate_attempt(tmp_path):
    from app.services.pipeline_run_store import PipelineRunStore

    store = PipelineRunStore(tmp_path / "pipeline.db")
    run = store.begin_run("2026-07-31")
    store.start_stage(run["run_id"], "market_discovery", input_count=5200)
    store.finish_stage(
        run["run_id"],
        "market_discovery",
        status="failed",
        output_count=0,
        error_code="empty_universe",
    )

    resumed = store.resume_stage(run["run_id"], "market_discovery")
    assert resumed["attempt"] == 2
    assert resumed["status"] == "running"
    assert store.begin_run("2026-07-31")["run_id"] == run["run_id"]


def test_older_failure_cannot_overwrite_newer_success(tmp_path):
    from app.services.pipeline_run_store import PipelineRunStore

    store = PipelineRunStore(tmp_path / "pipeline.db")
    run_id = store.begin_run("2026-07-31")["run_id"]
    first = store.start_stage(run_id, "delivery")
    second = store.resume_stage(run_id, "delivery")
    store.finish_stage(run_id, "delivery", attempt=second["attempt"], status="succeeded")
    store.finish_stage(run_id, "delivery", attempt=first["attempt"], status="failed")
    assert store.get_stage(run_id, "delivery")["status"] == "succeeded"
```

- [ ] **Step 2: Run tests and verify RED**

Run: `PYTHONPATH=.:backend .venv/bin/python -m pytest backend/tests/test_pipeline_run_store.py -q`

Expected: FAIL because `PipelineRunStore` does not exist.

- [ ] **Step 3: Implement SQLite schema and compare-and-set writes**

Create tables `pipeline_runs` and `pipeline_stages` with a unique constraint on `(trade_date, pipeline_version)` and `(run_id, stage_name)`. Use `BEGIN IMMEDIATE`, WAL mode and attempt-number comparison so an old completion cannot overwrite a newer attempt:

```sql
CREATE TABLE pipeline_runs (
    run_id TEXT PRIMARY KEY,
    trade_date TEXT NOT NULL,
    pipeline_version TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(trade_date, pipeline_version)
);

CREATE TABLE pipeline_stages (
    run_id TEXT NOT NULL,
    stage_name TEXT NOT NULL,
    status TEXT NOT NULL,
    attempt INTEGER NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL DEFAULT '',
    input_count INTEGER NOT NULL DEFAULT 0,
    output_count INTEGER NOT NULL DEFAULT 0,
    data_cutoff_at TEXT NOT NULL DEFAULT '',
    artifact_path TEXT NOT NULL DEFAULT '',
    artifact_digest TEXT NOT NULL DEFAULT '',
    error_code TEXT NOT NULL DEFAULT '',
    recovery_action TEXT NOT NULL DEFAULT '',
    PRIMARY KEY(run_id, stage_name)
);
```

Public methods are `begin_run(trade_date)`, `start_stage(run_id, stage_name, input_count=0)`,
`resume_stage(run_id, stage_name)`, `finish_stage(...)`, `get_stage(run_id, stage_name)`,
and `incomplete_stages(run_id, expected: Sequence[str])`. `finish_stage` executes:

```sql
UPDATE pipeline_stages
SET status = ?, finished_at = ?, output_count = ?, data_cutoff_at = ?,
    artifact_path = ?, artifact_digest = ?, error_code = ?, recovery_action = ?
WHERE run_id = ? AND stage_name = ? AND attempt = ?;
```

It raises `StaleStageAttempt` when `rowcount != 1`, except when the current stored state is already
`succeeded`, in which case it returns that successful row unchanged.

Add `resolve_runtime_pipeline_db_path()` to `backend/app/config.py`, defaulting to `CONGXI_STATE_DIR/pipeline_runs.db`.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run: `PYTHONPATH=.:backend .venv/bin/python -m pytest backend/tests/test_pipeline_run_store.py backend/tests/test_runtime_database_paths.py -q`

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/config.py backend/app/services/pipeline_run_store.py backend/tests/test_pipeline_run_store.py backend/tests/test_runtime_database_paths.py
git commit -m "feat: add durable opportunity pipeline ledger"
```

### Task 3: 先发现再按现金过滤并完成同轮评分

**Files:**
- Modify: `backend/app/services/small_account_discovery.py`
- Create: `backend/app/services/opportunity_pipeline.py`
- Create: `backend/tests/test_opportunity_pipeline.py`
- Modify: `backend/tests/test_daily_report_delivery.py`

- [ ] **Step 1: Write failing affordability-first and ordering tests**

```python
@pytest.mark.asyncio
async def test_pipeline_filters_lot_cost_before_enrichment_and_scores_new_names_same_run():
    from app.services.opportunity_pipeline import OpportunityPipeline

    source = FakeDiscoverySource([
        {"code": "000001", "name": "可买", "price": 6.0, "amount": 2e8, "net_amount": 2e7},
        {"code": "600000", "name": "买不起", "price": 12.0, "amount": 3e8, "net_amount": 3e7},
    ])
    enriched = []

    async def enrich(row):
        enriched.append(row["code"])
        return complete_snapshot(row["code"], row["name"], row["price"])

    result = await OpportunityPipeline(discovery_source=source, snapshot_builder=enrich).evaluate(
        trade_date="2026-07-31",
        available_cash=800,
        total_assets=800,
    )

    assert result["metrics"]["scanned_count"] == 2
    assert result["metrics"]["affordable_count"] == 1
    assert enriched == ["000001"]
    assert [row["code"] for row in result["scorecards"]] == ["000001"]
```

Add a regression asserting that `scripts.daily_report.main()` no longer calls `build_target_scores_for_report()` before `build_refreshed_outside_pool_scan_for_report()`.

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
PYTHONPATH=.:backend .venv/bin/python -m pytest \
  backend/tests/test_opportunity_pipeline.py \
  backend/tests/test_daily_report_delivery.py::test_daily_report_discovers_before_scoring -q
```

Expected: FAIL because the orchestrator does not exist and the current report scores before discovery.

- [ ] **Step 3: Implement broad discovery result and affordability hard gate**

`small_account_discovery.py` returns:

```python
{
    "candidates": candidate_rows,
    "metrics": {
        "scanned_count": len(all_rows),
        "tradeable_count": tradeable_count,
        "affordable_count": affordable_count,
        "ranked_count": len(candidates),
        "data_cutoff_at": cutoff,
    },
}
```

The hard gate uses `lot_size_for_code(code) * price <= executable_budget`. It removes the unconditional `research_only=True`; rejected rows are counted with `budget_blocked` but not persisted into the formal pool.

`OpportunityPipeline.evaluate()` performs discovery → affordability → snapshot → scoring in that order and returns metrics, scorecards and rejected summaries. It limits expensive enrichment to the top 30 affordable coarse candidates while retaining the all-market scan count.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the Step 2 command plus:

```bash
PYTHONPATH=.:backend .venv/bin/python -m pytest \
  backend/tests/test_small_account_discovery.py \
  backend/tests/test_target_scoring.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/small_account_discovery.py backend/app/services/opportunity_pipeline.py backend/tests/test_opportunity_pipeline.py backend/tests/test_daily_report_delivery.py backend/tests/test_small_account_discovery.py
git commit -m "feat: discover and score affordable targets in one run"
```

### Task 4: 输出可解释的综合评分卡

**Files:**
- Create: `backend/app/services/composite_score.py`
- Modify: `backend/app/services/target_scoring.py`
- Create: `backend/tests/test_composite_score.py`
- Modify: `backend/tests/test_target_scoring.py`

- [ ] **Step 1: Write failing score breakdown tests**

```python
def test_composite_score_has_six_bounded_components_and_plain_reasons():
    from app.services.composite_score import build_composite_score

    score = build_composite_score(complete_snapshot("000001", "平安银行", 6.0))
    assert score["score_version"] == "composite_score_v1"
    assert set(score["components"]) == {
        "trend_volume",
        "fund_flow",
        "industry_catalyst",
        "fundamental_valuation",
        "relative_strength",
        "risk_reward",
    }
    assert sum(score["components"].values()) == score["total"]
    assert len(score["top_reasons"]) == 3
    assert score["grade"] in {"S", "A", "B", "C"}
```

Add tests proving missing evidence contributes zero for that component and is listed in `missing_inputs`, never silently treated as a healthy score.

- [ ] **Step 2: Run tests and verify RED**

Run: `PYTHONPATH=.:backend .venv/bin/python -m pytest backend/tests/test_composite_score.py backend/tests/test_target_scoring.py -q`

Expected: FAIL because `build_composite_score` and component fields do not exist.

- [ ] **Step 3: Implement the six-component scorecard**

Implement maximum points `30/20/15/15/10/10`. Every component returns `{points, reason, source_status}`; the public payload flattens points into `components`, sorts positive reasons, chooses three concise reasons, and assigns grades:

```python
def grade(total: float, *, all_hard_gates_passed: bool) -> str:
    if total >= 85 and all_hard_gates_passed:
        return "S"
    if total >= 75:
        return "A"
    if total >= 65:
        return "B"
    return "C"
```

`target_scoring.score_target()` keeps affordability, data, market-regime, chasing and risk-budget gates. It replaces the opaque `technical * 0.7 + research * 0.3` total with the composite payload and adds `score_components`, `grade`, `top_reasons`, `primary_risk` and `score_version`.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the Step 2 command. Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/composite_score.py backend/app/services/target_scoring.py backend/tests/test_composite_score.py backend/tests/test_target_scoring.py
git commit -m "feat: add explainable v9 composite scorecard"
```

### Task 5: 打通确定性晋级和生命周期变更

**Files:**
- Create: `backend/app/services/target_lifecycle_events.py`
- Modify: `backend/app/services/quant_lifecycle.py`
- Modify: `backend/app/services/opportunity_pipeline.py`
- Create: `backend/tests/test_target_lifecycle_events.py`
- Modify: `backend/tests/test_quant_lifecycle.py`

- [ ] **Step 1: Write failing promotion and change-event tests**

```python
def test_dynamic_candidate_can_promote_with_deterministic_evidence():
    from app.services.quant_lifecycle import target_production_eligibility

    current = {
        "source": "dynamic_market_discovery",
        "status": "executable",
        "promotion_evidence": {
            "score_version": "composite_score_v1",
            "score": 88,
            "data_cutoff_at": "2026-07-31T15:00:00+08:00",
            "playbook": "breakout_entry",
            "hard_gates": {"affordable": True, "data_complete": True, "risk_ok": True},
        },
    }
    assert target_production_eligibility(current)["eligible"] is True


def test_lifecycle_event_explains_downgrade_and_removal():
    from app.services.target_lifecycle_events import classify_lifecycle_event

    event = classify_lifecycle_event(
        previous={"status": "watching", "score": 76},
        current={"status": "removed", "score": 52, "block_reason": "fund_flow_reversed"},
    )
    assert event["event"] == "removed"
    assert "资金" in event["reason"]
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
PYTHONPATH=.:backend .venv/bin/python -m pytest \
  backend/tests/test_target_lifecycle_events.py \
  backend/tests/test_quant_lifecycle.py::test_dynamic_candidate_can_promote_with_deterministic_evidence -q
```

Expected: FAIL because promotion evidence and lifecycle event service do not exist.

- [ ] **Step 3: Implement deterministic promotion evidence and four event types**

`promotion_evidence` is accepted only when:

```python
required_gates = {"affordable", "data_complete", "risk_ok", "tradeable", "playbook_triggered"}
valid = (
    evidence.get("score_version") == SCORE_VERSION
    and float(evidence.get("score", 0)) >= 70
    and required_gates <= {
        key for key, value in evidence.get("hard_gates", {}).items() if value is True
    }
    and bool(evidence.get("data_cutoff_at"))
)
```

Caller-provided human identity strings remain untrusted. The opportunity pipeline creates the evidence only from scorecard and hard-gate results. `target_lifecycle_events.py` maps state changes to `added/retained/downgraded/removed`, includes old/new score and a humanized reason.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run:

```bash
PYTHONPATH=.:backend .venv/bin/python -m pytest \
  backend/tests/test_target_lifecycle_events.py \
  backend/tests/test_quant_lifecycle.py \
  backend/tests/test_target_pool_daily_refresh.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/target_lifecycle_events.py backend/app/services/quant_lifecycle.py backend/app/services/opportunity_pipeline.py backend/tests/test_target_lifecycle_events.py backend/tests/test_quant_lifecycle.py
git commit -m "feat: add auditable target promotion and change events"
```

### Task 6: 渲染简洁主报告并做语义校验

**Files:**
- Create: `backend/app/services/report_validation.py`
- Modify: `scripts/daily_report.py`
- Modify: `backend/tests/test_daily_report_delivery.py`
- Create: `backend/tests/test_report_validation.py`

- [ ] **Step 1: Write failing report contract tests**

```python
def test_actionable_report_shows_three_targets_and_all_changes():
    from scripts.daily_report import build_v9_opportunity_section

    lines = build_v9_opportunity_section(v9_pipeline_result())
    text = "\n".join(lines)
    assert text.count("最大计划亏损") == 3
    assert "今日新增" in text
    assert "今日降级" in text
    assert "今日剔除" in text
    assert "买不起" not in text


def test_report_validation_rejects_hidden_candidates():
    from app.services.report_validation import validate_report

    result = validate_report(
        "今日结论：不买\n短线池：暂无",
        pipeline_result={"metrics": {"scored_count": 5}, "lifecycle_events": [{"event": "added"}]},
    )
    assert result["ok"] is False
    assert "lifecycle_change_missing" in result["errors"]
```

- [ ] **Step 2: Run tests and verify RED**

Run: `PYTHONPATH=.:backend .venv/bin/python -m pytest backend/tests/test_report_validation.py backend/tests/test_daily_report_delivery.py -q`

Expected: FAIL because the v9 renderer and validator do not exist.

- [ ] **Step 3: Implement action cards, change list and report invariants**

Add `build_v9_opportunity_section(result)` with:

- account cash/reserve/executable budget;
- at most three `actionable` scorecards;
- score/grade, three reasons, primary risk;
- entry, shares, lot value, stop, target, max planned loss;
- all added/downgraded/removed events;
- pipeline metrics and degraded stage names.

`validate_report()` requires non-empty content, core headings, counts reconciling with `pipeline_result`, lifecycle events present when recorded, and either actionable cards or a specific no-action explanation.

`daily_report.main()` renders from the same `OpportunityPipelineResult` used to persist lifecycle state; it removes the separate “score old pool, then scan outside pool” branch.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the Step 2 command. Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/report_validation.py scripts/daily_report.py backend/tests/test_report_validation.py backend/tests/test_daily_report_delivery.py
git commit -m "feat: render and validate actionable v9 reports"
```

### Task 7: 编排自愈、补跑和交付验真

**Files:**
- Modify: `backend/app/services/opportunity_pipeline.py`
- Modify: `backend/app/main.py`
- Create: `backend/tests/test_opportunity_pipeline_recovery.py`
- Modify: `backend/tests/test_schedule_policy.py`
- Modify: `backend/tests/test_runtime_regressions.py`

- [ ] **Step 1: Write failing semantic failure and resume tests**

```python
@pytest.mark.asyncio
async def test_zero_market_rows_retries_fallback_and_never_reports_success(tmp_path):
    primary = SequenceSource([[], [], []])
    fallback = SequenceSource([[affordable_row("000001")]])
    pipeline = recovery_pipeline(tmp_path, primary=primary, fallback=fallback)

    result = await pipeline.run("2026-07-31", available_cash=800, total_assets=800)
    assert result["stages"]["market_discovery"]["status"] == "degraded"
    assert result["stages"]["market_discovery"]["attempt"] == 3
    assert result["stages"]["market_discovery"]["recovery_action"] == "fallback_source"


@pytest.mark.asyncio
async def test_watchdog_resumes_render_stage_without_reapplying_lifecycle(tmp_path):
    pipeline = pipeline_with_render_failure_once(tmp_path)
    first = await pipeline.run("2026-07-31", available_cash=800, total_assets=800)
    second = await pipeline.recover(first["run_id"])
    assert second["stages"]["report_render"]["status"] == "succeeded"
    assert pipeline.lifecycle_apply_count == 1
```

Add scheduler tests for `opportunity_pipeline`, `opportunity_recovery` at 20:45 and `opportunity_delivery_verify` at 21:15, all with `max_instances=1` and `coalesce=True`.

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
PYTHONPATH=.:backend .venv/bin/python -m pytest \
  backend/tests/test_opportunity_pipeline_recovery.py \
  backend/tests/test_schedule_policy.py \
  backend/tests/test_runtime_regressions.py -q
```

Expected: FAIL because recovery entry points and jobs do not exist.

- [ ] **Step 3: Implement bounded recovery and scheduler entry points**

Use the stage order:

```python
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
```

Each stage writes its artifact before marking success. Recovery finds the first failed/incomplete stage, verifies the previous artifact digest and resumes there. External fetches retry at most three times with bounded exponential backoff; tests inject a zero-delay sleeper. Delivery deduplicates on `run_id + report_digest`.

Add:

```python
async def _run_opportunity_pipeline_with_status():
    from app.services.opportunity_pipeline import build_default_pipeline

    return await build_default_pipeline().run_for_service_date()


async def _recover_opportunity_pipeline_with_status():
    from app.services.opportunity_pipeline import build_default_pipeline

    return await build_default_pipeline().recover_due_run()


async def _verify_opportunity_delivery_with_status():
    from app.services.opportunity_pipeline import build_default_pipeline

    return await build_default_pipeline().verify_due_delivery()
```

Startup health checks call recovery only when the current trading-day run is due and incomplete.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the Step 2 command. Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/opportunity_pipeline.py backend/app/main.py backend/tests/test_opportunity_pipeline_recovery.py backend/tests/test_schedule_policy.py backend/tests/test_runtime_regressions.py
git commit -m "feat: self-heal and verify the v9 report pipeline"
```

### Task 8: 修复数据健康、完成端到端验收和发布文档

**Files:**
- Modify: `backend/app/services/target_snapshot.py`
- Modify: `backend/tests/test_target_snapshot.py`
- Create: `backend/tests/test_v9_pipeline_e2e.py`
- Modify: `README.md`
- Modify: `CHANGELOG.md`
- Modify: `docs/superpowers/specs/2026-07-31-v9-actionable-self-healing-report-design.md`

- [ ] **Step 1: Write failing empty-evidence and end-to-end tests**

```python
def test_empty_research_payload_is_missing_not_ok():
    from app.services.target_snapshot import normalize_optional_evidence

    assert normalize_optional_evidence({})["status"] == "missing"


@pytest.mark.asyncio
async def test_v9_pipeline_generates_actionable_report_and_delivery_truth(tmp_path):
    result = await run_temp_v9_pipeline(
        tmp_path,
        cash=800,
        market_rows=[affordable_row("000001"), unaffordable_row("600000")],
    )
    assert result["metrics"]["scanned_count"] == 2
    assert result["metrics"]["affordable_count"] == 1
    assert result["report_validation"]["ok"] is True
    assert result["delivery"]["report_path"]
    assert result["delivery"]["report_digest"]
    assert "000001" in Path(result["delivery"]["report_path"]).read_text(encoding="utf-8")
    assert "600000" not in Path(result["delivery"]["report_path"]).read_text(encoding="utf-8")
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
PYTHONPATH=.:backend .venv/bin/python -m pytest \
  backend/tests/test_target_snapshot.py \
  backend/tests/test_v9_pipeline_e2e.py -q
```

Expected: FAIL because empty research is currently marked `ok` and the v9 test harness is incomplete.

- [ ] **Step 3: Implement evidence normalization and release documentation**

Empty dictionaries and lists become `{status: "missing", reason: "empty_payload"}`. Preserve explicit `failed` and `stale` states. Update README and CHANGELOG with:

- `v9.0.0-dev`;
- actionable-first report contract;
- stage ledger and semantic health;
- recovery schedules;
- no-auto-trade boundary;
- exact verification commands and observed results.

Change the design document status to `已实现，待生产验收` only after all checks succeed.

- [ ] **Step 4: Run full verification**

Run:

```bash
PYTHONPATH=.:backend .venv/bin/python -m pytest backend/tests -q
PYTHONPATH=.:backend .venv/bin/python -m ruff check backend scripts
git diff --check
```

Then run the report with temporary paths:

```bash
CONGXI_PORTFOLIO_PATH=/tmp/congxi-v9-portfolio.json \
CONGXI_CANDIDATE_POOL_PATH=/tmp/congxi-v9-candidate-pool.json \
CONGXI_REPORT_ARCHIVE_DIR=/tmp/congxi-v9-reports \
CONGXI_PIPELINE_DATABASE_PATH=/tmp/congxi-v9-pipeline.db \
PYTHONPATH=.:backend .venv/bin/python scripts/daily_report.py
```

Expected:

- complete backend suite passes;
- ruff and diff check pass;
- a dated report exists under `/tmp/congxi-v9-reports`;
- report contains account budget, at most three actionable cards, lifecycle changes and pipeline status;
- pipeline SQLite records all expected stages;
- real portfolio, candidate pool and report archive remain unchanged.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/target_snapshot.py backend/tests/test_target_snapshot.py backend/tests/test_v9_pipeline_e2e.py README.md CHANGELOG.md docs/superpowers/specs/2026-07-31-v9-actionable-self-healing-report-design.md
git commit -m "docs: finalize congxi v9 feature validation"
```

### Task 9: PR readiness and publication

**Files:**
- Modify only files required by final review findings.

- [ ] **Step 1: Review the complete branch against `origin/main`**

Run:

```bash
git diff --stat origin/main...HEAD
git diff --check origin/main...HEAD
git status --short
```

Expected: only v9 scope files changed and the worktree is clean.

- [ ] **Step 2: Run pre-PR focused smoke**

```bash
PYTHONPATH=.:backend .venv/bin/python -m pytest \
  backend/tests/test_v9_pipeline_e2e.py \
  backend/tests/test_opportunity_pipeline_recovery.py \
  backend/tests/test_daily_report_delivery.py \
  backend/tests/test_runtime_regressions.py -q
```

Expected: all tests pass.

- [ ] **Step 3: Push the feature branch**

```bash
git push -u origin codex/v9-actionable-self-healing-report
```

- [ ] **Step 4: Create the pull request**

Create a draft PR titled:

```text
feat: ship v9 actionable self-healing report pipeline
```

The PR body must include:

- user outcome;
- architecture and safety boundaries;
- before/after pipeline;
- test commands and exact pass counts;
- temporary-path live report evidence;
- known production prerequisites such as broker Accessibility permission;
- explicit statement that no auto-trading capability was added.

- [ ] **Step 5: Verify PR state**

Confirm the PR head SHA matches local `HEAD`, CI status is visible, mergeability is reported, and no unrelated dirty-worktree files entered the diff.
