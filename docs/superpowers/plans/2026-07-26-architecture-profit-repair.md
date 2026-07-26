# Architecture And Profit Loop Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复已审计确认的数据真实性、策略约束、中长线链路、报告效率、离线历史数据和盈利归因缺口，使生产候选、研究对象、执行建议和复盘证据保持一致。

**Architecture:** 保留 Sentinel research-only、Long Horizon shadow-only、易淘金禁止自动交易三条安全边界。将共享策略约束、数据源健康、中长期研究物化、报告渲染和历史数据读取拆成明确服务；日报只消费结构化结果，不再自行猜测研究状态或重复实现账户规则。

**Tech Stack:** Python 3.14、FastAPI、APScheduler、pytest、JSON/JSONL 文件存储、腾讯/东方财富/Tushare/AKShare 数据源、Swift Package 易淘金安全桥。

---

## Scope And Safety

- 本计划修复 2026-07-26 架构审查中已确认的问题。
- 不开启自动下单，不写入券商持仓，不改变易淘金安全策略默认值。
- Sentinel 继续只产生研究证据；Long Horizon 继续 `shadow_only`，不能绕过战术触发直接生成 `buy`。
- 真实报告验证必须使用临时 `CONGXI_*_PATH`，不得污染 `data/user_portfolio.json`、真实候选池、真实报告归档和生产 execution ledger。
- 主目录现有未提交离线数据改动只作为明确来源移植，不能覆盖或删除。

### Task 1: 统一策略约束并让数据源失败关闭

**Files:**
- Modify: `backend/app/engine/workshop.py`
- Modify: `backend/app/ai/debate.py`
- Modify: `backend/app/ai/cloud_client.py`
- Modify: `backend/app/main.py`
- Modify: `scripts/daily_report.py`
- Test: `backend/tests/test_portfolio_state.py`
- Test: `backend/tests/test_runtime_regressions.py`
- Test: `backend/tests/test_daily_report_delivery.py`

- [ ] **Step 1: Write failing strategy-profile tests**

```python
def test_growth_sprint_account_constraints_use_profile_and_board_lot_size():
    constrained = _apply_account_constraints(
        decision_with_star_market_candidate(price=3.0),
        available_cash=1304.25,
        total_assets=5972.25,
        strategy_profile=get_strategy_profile("growth_sprint"),
    )
    assert constrained["account_constraints"]["reserve_cash"] == 597.23
    assert constrained["account_constraints"]["lot_size_by_code"]["688001"] == 200
    assert constrained["account_constraints"]["executable_cash"] > 0
```

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```bash
PYTHONPATH=.:backend /Users/zhuchenyuan/AI/workflows/恭喜发财/.venv/bin/python -m pytest backend/tests/test_portfolio_state.py -q
```

Expected: FAIL because `_apply_account_constraints` still hard-codes 30% and 100 shares.

- [ ] **Step 3: Implement one shared profile path**

Pass `strategy_profile` into `_apply_account_constraints`, calculate reserve and ticket caps from the profile, and use `lot_size_for_code(code)`. Remove static 30%/10%/20% prompt rules and state that the injected strategy profile is authoritative.

```python
profile = strategy_profile or get_strategy_profile()
reserve_cash = round(total_assets * float(profile["cash_reserve_pct"]) / 100, 2)
lot_size = lot_size_for_code(code)
min_lot_amount = round(price * lot_size, 2) if price else None
```

- [ ] **Step 4: Write failing truth-health tests**

```python
async def test_cloud_health_is_false_on_non_200():
    client = CloudClient()
    client._client = StubHTTPClient(status_code=400)
    assert await client.is_available() is False

async def test_market_collection_does_not_invent_index_prices():
    result = await collect_market_data_with_all_sources_failed()
    assert result["indices"] == {}
    assert result["market_source_status"]["status"] == "failed"
```

- [ ] **Step 5: Run health tests and verify RED**

Run:

```bash
PYTHONPATH=.:backend /Users/zhuchenyuan/AI/workflows/恭喜发财/.venv/bin/python -m pytest backend/tests/test_runtime_regressions.py backend/tests/test_daily_report_delivery.py -q
```

Expected: FAIL because HTTP 400 is converted to a successful-looking payload and indices use fixed fallback values.

- [ ] **Step 6: Implement fail-closed health**

Make provider calls return an explicit `ok: False` contract or raise a provider error that `is_available()` catches. Remove hard-coded index prices. Add `market_source_status` with `status`, `provider`, `data_cutoff`, and `error`; report `ok` only when the value and freshness are real.

- [ ] **Step 7: Verify Task 1**

Run:

```bash
PYTHONPATH=.:backend /Users/zhuchenyuan/AI/workflows/恭喜发财/.venv/bin/python -m pytest backend/tests/test_portfolio_state.py backend/tests/test_runtime_regressions.py backend/tests/test_daily_report_delivery.py -q
```

Expected: PASS with no coroutine warnings and no fabricated index fallback.

- [ ] **Step 8: Commit**

```bash
git add backend/app/engine/workshop.py backend/app/ai/debate.py backend/app/ai/cloud_client.py backend/app/main.py scripts/daily_report.py backend/tests
git commit -m "fix: unify strategy constraints and fail closed"
```

### Task 2: 接通 Serenity 到 Long Thesis 的研究物化链路

**Files:**
- Create: `backend/app/services/long_horizon_pipeline.py`
- Modify: `backend/app/ai/sentinel_research.py`
- Modify: `scripts/run_sentinel.py`
- Modify: `backend/app/services/evidence_ledger.py`
- Test: `backend/tests/test_long_horizon_pipeline.py`
- Test: `backend/tests/test_sentinel_research.py`

- [ ] **Step 1: Write failing materialization test**

```python
def test_materialize_serenity_candidates_writes_thesis_evidence_and_long_state(tmp_path):
    result = materialize_serenity_long_horizon(
        serenity_package_with_verified_candidate(),
        report_date="2026-07-24",
        thesis_store=LongThesisStore(tmp_path / "long.json"),
        ledger=EvidenceLedgerStore(tmp_path / "evidence.jsonl"),
        target_pool=TargetPoolStore(tmp_path / "pool.json"),
    )
    assert result == {"candidate_count": 1, "thesis_count": 1, "evidence_count": 5}
    assert LongThesisStore(tmp_path / "long.json").get("002371")["thesis_status"] == "healthy"
    assert TargetPoolStore(tmp_path / "pool.json").get("002371")["status"] == "long_research"
```

- [ ] **Step 2: Run the new test and verify RED**

Run:

```bash
PYTHONPATH=.:backend /Users/zhuchenyuan/AI/workflows/恭喜发财/.venv/bin/python -m pytest backend/tests/test_long_horizon_pipeline.py -q
```

Expected: FAIL because `long_horizon_pipeline` does not exist.

- [ ] **Step 3: Preserve complete long fields in compact packages**

Extend `_compact_candidate()` to keep `long_assumptions`, `red_lines`, `valuation_questions`, `quarterly_verification_tasks`, `bottleneck_duration`, `bottleneck_map`, `financial_evidence`, and `quote_evidence`. Keep `boundary="research_only"`.

- [ ] **Step 4: Implement the materializer**

Convert each verified Serenity candidate into the existing Long Thesis schema, upsert it, append `build_long_horizon_evidence()` records, and upsert `long_research` without production approval.

```python
thesis = {
    "symbol": code,
    "name": candidate["name"],
    "core_thesis": candidate.get("chokepoint", ""),
    "quality_score": candidate.get("score", 0),
    "assumptions": candidate.get("long_assumptions", []),
    "red_lines": candidate.get("red_lines", []),
    "valuation_anchor": candidate.get("valuation_anchor", {}),
    "source_report_path": dive.get("learning_report_path", ""),
    "data_cutoff_date": report_date,
    "boundary": "shadow_only",
}
```

Candidates with both quote and financial status unavailable remain persisted as `forming` research, but must carry explicit missing verification fields and cannot become `long_watch`.

- [ ] **Step 5: Connect real account, quote, and financial fetchers**

Load account totals without mutating the portfolio. Pass a synchronous quote adapter backed by `TencentDataSource.fetch_batch` and `fetch_financial_evidence` into `build_serenity_deep_dives`. After Markdown paths are persisted, run the materializer before persisting the package JSON.

- [ ] **Step 6: Verify Sentinel integration**

Run:

```bash
PYTHONPATH=.:backend /Users/zhuchenyuan/AI/workflows/恭喜发财/.venv/bin/python -m pytest backend/tests/test_long_horizon_pipeline.py backend/tests/test_sentinel_research.py backend/tests/test_serenity_analyst.py backend/tests/test_serenity_financial_evidence.py -q
```

Expected: PASS; temporary outputs contain long thesis, normalized evidence, target long state, and explicit verification status.

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/long_horizon_pipeline.py backend/app/ai/sentinel_research.py scripts/run_sentinel.py backend/app/services/evidence_ledger.py backend/tests
git commit -m "feat: materialize serenity long horizon research"
```

### Task 3: 让 Target Scoring 消费长期论文并保护生命周期

**Files:**
- Modify: `scripts/daily_report.py`
- Modify: `backend/app/services/target_scoring.py`
- Modify: `backend/app/services/quant_lifecycle.py`
- Test: `backend/tests/test_target_scoring.py`
- Test: `backend/tests/test_daily_report_delivery.py`
- Test: `backend/tests/test_quant_lifecycle.py`

- [ ] **Step 1: Write failing end-to-end scoring test**

```python
async def test_daily_target_scoring_loads_long_thesis_and_preserves_long_state(tmp_path):
    scores = await build_target_scores_for_report(
        available_cash=1304.25,
        total_assets=5972.25,
        store=long_target_store(tmp_path),
        long_thesis_store=healthy_long_thesis_store(tmp_path),
        snapshot_builder=verified_snapshot_builder(),
    )
    assert scores[0]["long_quality_score"] > 0
    assert long_target_store(tmp_path).get("002371")["status"] in {"long_research", "long_watch"}
```

- [ ] **Step 2: Run and verify RED**

Run:

```bash
PYTHONPATH=.:backend /Users/zhuchenyuan/AI/workflows/恭喜发财/.venv/bin/python -m pytest backend/tests/test_target_scoring.py backend/tests/test_quant_lifecycle.py -q
```

Expected: FAIL because the daily scorer does not load or pass long thesis.

- [ ] **Step 3: Inject LongThesisStore**

Add an optional `long_thesis_store` dependency, load by code, and pass `long_thesis` into `score_target()`. Persist long-quality fields in `scoring_decision`.

- [ ] **Step 4: Preserve long status deterministically**

Add a pure transition helper:

```python
def next_target_status(current_status: str, action: str, long_view: dict) -> str:
    if current_status in LONG_HORIZON_STATUSES and action not in {"buy", "add", "remove"}:
        return current_status
    if long_view.get("thesis_status") == "broken":
        return "thesis_review"
    return ACTION_STATUS_MAP.get(action, "watching")
```

Long quality never grants execution authorization and never bypasses required tactical sources.

- [ ] **Step 5: Verify Task 3**

Run:

```bash
PYTHONPATH=.:backend /Users/zhuchenyuan/AI/workflows/恭喜发财/.venv/bin/python -m pytest backend/tests/test_target_scoring.py backend/tests/test_quant_lifecycle.py backend/tests/test_daily_report_delivery.py -q
```

Expected: PASS, including “long good cannot bypass tactical trigger”.

- [ ] **Step 6: Commit**

```bash
git add scripts/daily_report.py backend/app/services/target_scoring.py backend/app/services/quant_lifecycle.py backend/tests
git commit -m "fix: connect long thesis to target scoring"
```

### Task 4: 重构次日报告为动作优先且语义真实

**Files:**
- Create: `backend/app/report_engine/templates/next_day.py`
- Modify: `scripts/daily_report.py`
- Modify: `backend/tests/test_daily_report_delivery.py`
- Modify: `backend/tests/test_report_engine.py`

- [ ] **Step 1: Write failing visible-report tests**

```python
def test_next_day_first_screen_starts_with_actions_and_exact_shares():
    report = render_next_day_strategy(fixture)
    assert report.index("明日持仓动作") < report.index("系统与数据审计")
    assert "卖出100股" in report

def test_research_only_rows_are_never_marked_key_focus():
    report = render_next_day_strategy(fixture_with_unscored_research_rows)
    assert "研究候选（未晋级，不买）" in report
    assert "研究候选（未晋级，不买）" not in key_focus_rows(report)

def test_budget_blocked_is_not_long_horizon():
    report = render_next_day_strategy(fixture_with_only_budget_blocked_rows)
    assert "中长线研究不是没有" not in report
    assert "暂无通过长期论文核验的标的" in report
```

- [ ] **Step 2: Run and verify RED**

Run:

```bash
PYTHONPATH=.:backend /Users/zhuchenyuan/AI/workflows/恭喜发财/.venv/bin/python -m pytest backend/tests/test_daily_report_delivery.py backend/tests/test_report_engine.py -q
```

Expected: FAIL on current ordering, focus labels, holdings quantity, and budget/long conflation.

- [ ] **Step 3: Extract the next-day renderer**

Move pure formatting and section builders into `report_engine/templates/next_day.py`. Keep compatibility imports in `scripts/daily_report.py` so CLI and existing tests continue to work. The renderer receives already-scored data and performs no network or storage writes.

- [ ] **Step 4: Implement the visible hierarchy**

Render:

1. `明日持仓动作`: shares, exact exit quantity, stop, target, trigger.
2. `明日主机会`: at most one executable/trigger-waiting candidate.
3. `备选机会`: at most two fully scored candidates.
4. `中长线跟踪`: only rows with a real thesis status or long-quality evidence.
5. `系统与数据审计`: provider, freshness, verification gaps, model probe status.
6. `研究附录`: unscored/research-only candidates, never labeled “重点关注”.

Remove the false statement that post-debate budget filtering prevented candidates from entering AI input.

- [ ] **Step 5: Verify summary efficiency**

Assert the Feishu-visible portion remains under the existing size limit and contains no raw internal enums, no contradictory middle/long wording, and no research-only “重点关注”.

- [ ] **Step 6: Run report tests**

Run:

```bash
PYTHONPATH=.:backend /Users/zhuchenyuan/AI/workflows/恭喜发财/.venv/bin/python -m pytest backend/tests/test_daily_report_delivery.py backend/tests/test_report_engine.py backend/tests/test_feishu_pusher.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/app/report_engine/templates/next_day.py scripts/daily_report.py backend/tests
git commit -m "refactor: make next day report action first"
```

### Task 5: 移植离线大数据并闭合盈利归因状态

**Files:**
- Create: `backend/app/data_sources/offline_market_data.py`
- Create: `backend/tests/test_offline_market_data.py`
- Create: `backend/tests/test_backtest_offline_source.py`
- Create: `data/examples/external_market_data_sources.example.json`
- Modify: `backend/app/engine/backtest.py`
- Modify: `scripts/run_prediction_lab.py`
- Modify: `backend/app/services/recommendation_review.py`
- Modify: `README.md`
- Test: `backend/tests/test_prediction_lab_due.py`
- Test: `backend/tests/test_recommendation_review.py`

- [ ] **Step 1: Port the existing uncommitted offline adapter exactly**

Copy the reviewed main-worktree versions of the four new files and the relevant changes in `backtest.py`, `run_prediction_lab.py`, `README.md`, and tests. Do not copy unrelated user changes.

- [ ] **Step 2: Run ported tests**

Run:

```bash
PYTHONPATH=.:backend /Users/zhuchenyuan/AI/workflows/恭喜发财/.venv/bin/python -m pytest backend/tests/test_offline_market_data.py backend/tests/test_backtest_offline_source.py backend/tests/test_prediction_lab_due.py -q
```

Expected: `28 passed` or more.

- [ ] **Step 3: Write failing truthful-attribution test**

```python
async def test_review_reports_execution_chain_missing_not_sentinel_empty(tmp_path):
    review = await build_recommendation_review(
        portfolio_path=portfolio_with_unattributed_fill(tmp_path),
        execution_ledger_path=str(tmp_path / "ledger.jsonl"),
        quote_source=StubQuoteSource(),
    )
    assert review["system_gap"] == "execution_chain_missing"
```

- [ ] **Step 4: Run and verify RED**

Run:

```bash
PYTHONPATH=.:backend /Users/zhuchenyuan/AI/workflows/恭喜发财/.venv/bin/python -m pytest backend/tests/test_recommendation_review.py -q
```

Expected: FAIL because `system_gap` is always `sentinel_advice_performance_has_no_executed_samples`.

- [ ] **Step 5: Implement truthful attribution states**

Set `system_gap` from actual evidence:

- no executions: `no_executed_samples`;
- executions but no complete chain: `execution_chain_missing`;
- complete chains exist: empty string;
- invalid ledger: `execution_ledger_history_invalid`.

Keep portfolio behavior metrics separate from strategy attribution.

- [ ] **Step 6: Verify Task 5**

Run:

```bash
PYTHONPATH=.:backend /Users/zhuchenyuan/AI/workflows/恭喜发财/.venv/bin/python -m pytest backend/tests/test_offline_market_data.py backend/tests/test_backtest_offline_source.py backend/tests/test_prediction_lab_due.py backend/tests/test_execution_ledger.py backend/tests/test_recommendation_review.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/app/data_sources/offline_market_data.py backend/app/engine/backtest.py scripts/run_prediction_lab.py backend/app/services/recommendation_review.py backend/tests data/examples README.md
git commit -m "feat: add offline history and truthful attribution"
```

### Task 6: 收敛入口、文档并执行隔离端到端验收

**Files:**
- Create: `backend/app/services/scheduler_service.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/services/quant_lifecycle.py`
- Modify: `ROADMAP.md`
- Modify: `CHANGELOG.md`
- Modify: `README.md`
- Test: `backend/tests/test_scheduler_service.py`
- Test: `backend/tests/test_sentinel_research.py`
- Test: `backend/tests/test_daily_report_delivery.py`

- [ ] **Step 1: Write failing scheduler registration test**

```python
def test_scheduler_service_registers_research_before_report():
    specs = build_scheduler_specs()
    ids = [item.id for item in specs]
    assert ids.index("sentinel_research") < ids.index("daily_report")
    assert len(ids) == len(set(ids))
```

- [ ] **Step 2: Run and verify RED**

Run:

```bash
PYTHONPATH=.:backend /Users/zhuchenyuan/AI/workflows/恭喜发财/.venv/bin/python -m pytest backend/tests/test_scheduler_service.py -q
```

Expected: FAIL because scheduler registration is embedded in `main.py`.

- [ ] **Step 3: Extract scheduler specifications**

Move only schedule construction/registration into `scheduler_service.py`; keep task implementations callable and preserve all existing job IDs, times, coalescing and trading-day gates. Switch `main.py` to `TargetPoolStore` for target lifecycle semantics.

- [ ] **Step 4: Align documentation with production truth**

Update version surfaces and roadmap:

- distinguish implemented component from connected production path;
- mark Long Horizon connection complete only after Task 2/3 tests;
- mark next-day renderer and scheduler extraction complete;
- document local offline data as shadow historical data;
- document DeepSeek/Qwen health as probe truth, not key-presence truth;
- document execution attribution states.

- [ ] **Step 5: Run full static and unit verification**

Run:

```bash
PYTHONPATH=.:backend /Users/zhuchenyuan/AI/workflows/恭喜发财/.venv/bin/python -m pytest backend/tests -q
PYTHONPATH=.:backend /Users/zhuchenyuan/AI/workflows/恭喜发财/.venv/bin/python -m ruff check backend scripts
git diff --check
swift test --package-path tools/yitaojin-bridge
```

Expected: all commands exit 0.

- [ ] **Step 6: Run isolated end-to-end shadow workflow**

Use a temporary directory and fixture account:

```bash
CONGXI_PORTFOLIO_PATH="$TMP/portfolio.json" \
CONGXI_CANDIDATE_POOL_PATH="$TMP/candidate_pool.json" \
CONGXI_LONG_THESIS_PATH="$TMP/long_thesis.json" \
CONGXI_EVIDENCE_LEDGER_PATH="$TMP/evidence.jsonl" \
CONGXI_EXECUTION_LEDGER_PATH="$TMP/execution.jsonl" \
CONGXI_REPORT_ARCHIVE_DIR="$TMP/reports" \
CONGXI_SENTINEL_OUTPUT_ROOT="$TMP/sentinel" \
PYTHONPATH=.:backend \
/Users/zhuchenyuan/AI/workflows/恭喜发财/.venv/bin/python scripts/run_sentinel.py --mode news --date 2026-07-24
```

Then run `scripts/daily_report.py` against the same temporary paths with network fetchers stubbed or cached fixtures. Verify:

- long thesis and long evidence files exist;
- long target state survives scoring;
- first screen starts with account actions;
- research-only rows are not key focus;
- no hard-coded market index appears;
- no broker write or auto order occurs.

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/scheduler_service.py backend/app/main.py backend/app/services/quant_lifecycle.py backend/tests README.md ROADMAP.md CHANGELOG.md
git commit -m "refactor: align scheduler and architecture truth"
```

## Final Verification Gate

- [ ] Re-run the full Python suite from a clean process.
- [ ] Re-run Ruff and `git diff --check`.
- [ ] Re-run Swift tests for the易淘金安全桥.
- [ ] Inspect `git status`, commit history, and diff against `codex/yitaojin-safe-bridge`.
- [ ] Confirm real portfolio, real candidate pool, real report archive, real execution ledger, and live launchd service were not modified.
- [ ] Request final spec compliance and code quality review.
