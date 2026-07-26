# Profit Truth Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Repair the production truth chain so runtime identity, real positions, risk plans, closed losses, decision snapshots, gates, and the next-trading-day report agree.

**Architecture:** Add two focused services: `runtime_identity.py` for immutable process metadata and `profit_truth.py` for deterministic reconciliation. Extend the existing target-pool store to retain a bounded decision snapshot, then invoke reconciliation before daily scoring and intraday entry evaluation. New market-regime and two-bar observations remain shadow-only.

**Tech Stack:** Python 3.12, FastAPI, pytest/pytest-asyncio, APScheduler, atomic JSON stores.

---

### Task 1: Runtime identity

**Files:**
- Create: `backend/app/services/runtime_identity.py`
- Modify: `backend/app/routers/market.py`
- Test: `backend/tests/test_runtime_regressions.py`

- [ ] **Step 1: Write the failing runtime identity tests**

```python
def test_runtime_identity_exposes_commit_started_at_and_strategy_version(monkeypatch):
    monkeypatch.setenv("CONGXI_BUILD_COMMIT", "abc1234")
    from app.services.runtime_identity import build_runtime_identity

    identity = build_runtime_identity(started_at="2026-07-24T01:00:00+08:00")

    assert identity["commit"] == "abc1234"
    assert identity["started_at"] == "2026-07-24T01:00:00+08:00"
    assert identity["strategy_version"] == "v8.2.0-dev"
```

```python
@pytest.mark.asyncio
async def test_health_check_includes_runtime_identity(monkeypatch):
    from app.routers import market

    monkeypatch.setattr(market, "runtime_identity", {
        "commit": "abc1234",
        "started_at": "2026-07-24T01:00:00+08:00",
        "strategy_version": "v8.2.0-dev",
    })
    payload = await market.health_check()
    assert payload["runtime"]["commit"] == "abc1234"
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
PYTHONPATH=.:backend .venv/bin/python -m pytest \
  backend/tests/test_runtime_regressions.py::test_runtime_identity_exposes_commit_started_at_and_strategy_version \
  backend/tests/test_runtime_regressions.py::test_health_check_includes_runtime_identity -q
```

Expected: FAIL because `runtime_identity.py` and the health payload do not exist.

- [ ] **Step 3: Implement immutable process metadata**

`build_runtime_identity()` resolves commit from `CONGXI_BUILD_COMMIT`, otherwise `git rev-parse --short HEAD`, with `"unknown"` as the fail-safe value. It returns `commit`, `started_at`, and `strategy_version`. `market.py` creates the identity once at import time and returns it under `runtime`.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the command from Step 2. Expected: both tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/runtime_identity.py backend/app/routers/market.py backend/tests/test_runtime_regressions.py
git commit -m "feat: expose production runtime identity"
```

### Task 2: Position-risk and closed-loss reconciliation

**Files:**
- Create: `backend/app/services/profit_truth.py`
- Modify: `backend/app/services/quant_lifecycle.py`
- Test: `backend/tests/test_profit_truth.py`

- [ ] **Step 1: Write failing reconciliation tests**

```python
def test_reconcile_position_watch_adds_missing_real_holding(tmp_path):
    from app.services.profit_truth import reconcile_position_watch
    from app.services.quant_lifecycle import PositionWatchStore

    store = PositionWatchStore(tmp_path / "position_watch.json")
    result = reconcile_position_watch(
        {"positions": [{"code": "002131", "name": "利欧股份", "shares": 200, "avg_cost": 3.995}]},
        store,
    )

    plan = store.get("002131")
    assert result["healthy"] is True
    assert plan["source"] == "portfolio_reconciliation"
    assert plan["stop_loss_price"] == 3.6
    assert plan["target_price"] == 4.79
```

```python
def test_reconcile_closed_loss_moves_watching_target_to_cooldown(tmp_path):
    from app.services.profit_truth import reconcile_closed_loss_cooldowns
    from app.services.quant_lifecycle import TargetPoolStore

    store = TargetPoolStore(tmp_path / "candidate_pool.json")
    store.upsert_target(code="000629", name="钒钛股份", status="watching", source="manual")
    result = reconcile_closed_loss_cooldowns(
        {"closed_positions": [{
            "code": "000629", "name": "钒钛股份", "close_date": "2026-07-08",
            "realized_pnl": -71.0, "realized_pnl_pct": -9.99,
        }]},
        store,
    )

    item = store.get("000629")
    assert result["cooled_codes"] == ["000629"]
    assert item["status"] == "cooldown_after_loss"
    assert item["loss_exit"]["realized_pnl_pct"] == -9.99
```

- [ ] **Step 2: Run focused tests and verify RED**

```bash
PYTHONPATH=.:backend .venv/bin/python -m pytest backend/tests/test_profit_truth.py -q
```

Expected: FAIL because `profit_truth.py` does not exist.

- [ ] **Step 3: Implement deterministic reconciliation**

`reconcile_position_watch()` preserves complete manual plans and only fills missing/invalid plans using the active strategy profile against `avg_cost`. It returns `healthy`, `added_codes`, and `unresolved_codes`.

`reconcile_closed_loss_cooldowns()` processes only rows with negative `realized_pnl` or `realized_pnl_pct`, uses a locked target-store mutation, records `loss_exit`, and never reactivates a cooled target.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the command from Step 2. Expected: both tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/profit_truth.py backend/app/services/quant_lifecycle.py backend/tests/test_profit_truth.py
git commit -m "feat: reconcile portfolio risk and loss cooldowns"
```

### Task 3: Persist bounded decision snapshots

**Files:**
- Modify: `backend/app/services/quant_lifecycle.py`
- Modify: `scripts/daily_report.py`
- Test: `backend/tests/test_quant_lifecycle.py`
- Test: `backend/tests/test_daily_report_delivery.py`

- [ ] **Step 1: Write failing persistence tests**

Add a target-store test that calls `upsert_target(decision_snapshot=...)` and asserts that only `captured_at`, `quote`, `kline`, `fund_flow`, `market_regime`, and `shadow_observations` are retained.

Add a daily-report scoring test whose fake store captures `decision_snapshot` and asserts:

```python
assert saved["captured_at"] == snapshot["generated_at"]
assert saved["kline"]["status"] == "ok"
assert saved["fund_flow"]["status"] == "ok"
assert saved["market_regime"]["status"] in {"ok", "missing"}
```

- [ ] **Step 2: Run focused tests and verify RED**

```bash
PYTHONPATH=.:backend .venv/bin/python -m pytest \
  backend/tests/test_quant_lifecycle.py -k decision_snapshot \
  backend/tests/test_daily_report_delivery.py -k decision_snapshot -q
```

Expected: FAIL because `upsert_target` does not accept or retain `decision_snapshot`.

- [ ] **Step 3: Implement bounded snapshot storage**

Extend `TargetPoolStore.upsert_target()` with `decision_snapshot`. Store the bounded snapshot under `item["decision_snapshot"]` and mirror `kline`, `fund_flow`, and `market_regime` at the existing top-level read boundary used by `_candidate_alert()`.

In `build_target_scores_for_report()`, derive a shadow market-regime observation from available index context when present, otherwise store an explicit `{"status": "missing", "reason": "market_regime_not_available"}`. Record two-bar confirmation as `not_evaluated` when fewer than two completed bars exist. Neither shadow field may change `score["action"]`.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the command from Step 2. Expected: selected tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/quant_lifecycle.py scripts/daily_report.py \
  backend/tests/test_quant_lifecycle.py backend/tests/test_daily_report_delivery.py
git commit -m "feat: persist auditable decision snapshots"
```

### Task 4: Wire reconciliation into report and intraday safety gate

**Files:**
- Modify: `scripts/daily_report.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_daily_report_delivery.py`
- Test: `backend/tests/test_runtime_regressions.py`

- [ ] **Step 1: Write failing integration tests**

Add a daily-report test that supplies a temporary portfolio, position-watch path, and candidate-pool path and asserts reconciliation occurs before `build_visible_decision_gate()`.

Add an intraday test that forces reconciliation to return:

```python
{"healthy": False, "unresolved_codes": ["002131"]}
```

and asserts the candidate scan receives:

```python
{"entry_allowed": False, "reasons": ["position_watch_unresolved"]}
```

while `evaluate_position_watch()` still runs.

- [ ] **Step 2: Run focused tests and verify RED**

```bash
PYTHONPATH=.:backend .venv/bin/python -m pytest \
  backend/tests/test_daily_report_delivery.py -k profit_truth \
  backend/tests/test_runtime_regressions.py -k position_watch_unresolved -q
```

Expected: FAIL because reconciliation is not wired into either runtime path.

- [ ] **Step 3: Wire fail-closed entry behavior**

The daily report loads portfolio truth, reconciles watch plans and closed losses, then reloads the stores before scoring and gate creation. The intraday task reconciles immediately after portfolio sync. If reconciliation is unhealthy, build a runtime blocked gate with `position_watch_unresolved`; continue evaluating and delivering sell/stop/take-profit actions.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the command from Step 2. Expected: selected tests PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/daily_report.py backend/app/main.py \
  backend/tests/test_daily_report_delivery.py backend/tests/test_runtime_regressions.py
git commit -m "fix: fail closed when portfolio risk truth diverges"
```

### Task 5: Full verification and isolated next-day report

**Files:**
- Modify only if a verification defect is found in the files above.
- Output: temporary report directory outside the repository.

- [ ] **Step 1: Run focused regression suite**

```bash
PYTHONPATH=.:backend .venv/bin/python -m pytest \
  backend/tests/test_profit_truth.py \
  backend/tests/test_quant_lifecycle.py \
  backend/tests/test_daily_report_delivery.py \
  backend/tests/test_runtime_regressions.py \
  backend/tests/test_visible_decision_gate.py -q
```

- [ ] **Step 2: Run full tests and static checks**

```bash
PYTHONPATH=.:backend .venv/bin/python -m pytest backend/tests -q
.venv/bin/python -m ruff check backend scripts
bash -n scripts/congxicai-v7-service.sh scripts/guardian.sh scripts/install-congxicai-v7-launchd.sh
plutil -lint scripts/com.zhuchenyuan.congxicai-v7.plist
git diff --check
```

- [ ] **Step 3: Generate an isolated next-trading-day report**

Create a temporary directory with `mktemp -d`, copy `data/user_portfolio.json`, `data/candidate_pool.json`, and `data/position_watch.json`, and run:

```bash
CONGXI_PORTFOLIO_PATH="$TMP_ROOT/user_portfolio.json" \
CONGXI_CANDIDATE_POOL_PATH="$TMP_ROOT/candidate_pool.json" \
CONGXI_POSITION_WATCH_PATH="$TMP_ROOT/position_watch.json" \
CONGXI_VISIBLE_DECISION_GATE_PATH="$TMP_ROOT/visible_decision_gate.json" \
CONGXI_REPORT_ARCHIVE_DIR="$TMP_ROOT/reports" \
FEISHU_WEBHOOK_URL= \
FEISHU_APP_ID= \
FEISHU_APP_SECRET= \
FEISHU_CHAT_ID= \
PYTHONPATH=.:backend .venv/bin/python scripts/daily_report.py
```

Expected: report exits zero, no Feishu delivery is attempted, and report/portfolio/watch/pool/gate target dates and holdings agree.

- [ ] **Step 4: Inspect the strategy artifact**

Read the generated Markdown and extract only:

- current-position action;
- new-position action;
- trigger price and amount;
- stop and target;
- invalidation condition;
- data freshness and unresolved risks.

- [ ] **Step 5: Commit final verification fixes if needed**

```bash
git status --short
git diff --check
```

Do not commit runtime data, temporary reports, credentials, or real portfolio mutations.
