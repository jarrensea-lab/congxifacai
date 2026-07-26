# Profit Accuracy Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将恭喜发财从“能产生信号”优化为“只有结构、数据、账户和授权同时成立才允许给出动作，并能把真实成交归因到建议和结果”的可证伪交易决策系统。

**Architecture:** 保留现有 `target_snapshot → target_scoring → quant_lifecycle → report/push` 主链路，在入口处增加 fail-closed 结构门，在输出处增加单次授权状态机，在用户确认成交时写入带审计标识的持仓与执行记录。预测模型继续运行在 shadow，只有成本后双基准结果和样本门槛同时通过才允许晋级。

**Tech Stack:** Python 3.12、FastAPI service modules、JSON/JSONL append-only ledgers、pytest、现有 `TargetPoolStore` / `PredictionLedger` / `strategy_evidence_lifecycle`。

---

## Worklist 总览

| # | 任务 | 预期产物 | 验证 | 状态 |
|---|---|---|---|---|
| 1 | 严格突破结构门 | K线不足、未越过预设触发价、非真正突破时一律不产生 `breakout_entry` | playbook/scoring/lifecycle 回归测试 | completed |
| 2 | 单次信号与授权 | `signal_id`、确认次数、有效期、撤销原因；同一信号只通知一次 | 重复扫描与失效测试 | completed |
| 3 | 持仓事实与成交审计 | 每笔交易拥有 `fill_id`、来源、时间、价格、数量、费用和可选建议关联 | portfolio store 单元测试 | completed |
| 4 | 补录利欧真实持仓 | `002131` 200股、成本3.995进入真实持仓和执行审计 | 临时副本演练后核对真实JSON | completed |
| 5 | 建议—成交—结果闭环 | `recommendation_id/signal_id/fill_id/outcome` 可追溯，未关联成交不归因 | execution/recommendation review 测试 | completed |
| 6 | 主报告硬否决 | 持仓未同步或报告明确“未触发不买”时，盘中买入/加仓展示为0 | daily report delivery 测试 | completed |
| 7 | 预测与盈利晋级门 | 结果覆盖、双基准、真实成本、walk-forward、回撤和置信区间全部 fail-closed | prediction/evidence lifecycle 测试 | completed |
| 8 | 全量验证与交付 | 测试、lint、diff检查、最新基线指标和未验证风险 | 完整验证命令 | completed |

## 已知边界与暂停条件

- 不连接券商、不自动下单、不修改密钥、Webhook、Cookie或登录态。
- 真实报告验证必须使用临时持仓/候选池/归档路径，只有用户已经明确确认的利欧成交允许写入真实持仓。
- 当前分支已有大量未提交改动；本计划只做增量补丁，不回滚、不批量格式化、不提交其他人的修改。
- 若发现利欧成交数量、成本或买卖方向与用户确认冲突，暂停真实持仓写入；本轮依据为“买入002131利欧股份200股，成本3.995”。
- 若需要真实券商成交编号、手续费或卖出价格才能继续，保留 `pending` 字段，不猜测。

### Task 1: Fail-closed breakout contract

**Files:**
- Modify: `backend/app/services/playbook_engine.py`
- Modify: `backend/app/services/quant_lifecycle.py`
- Modify: `scripts/daily_report.py`
- Test: `backend/tests/test_playbook_engine.py`
- Test: `backend/tests/test_target_scoring.py`
- Test: `backend/tests/test_quant_lifecycle.py`

- [x] **Step 1: Write failing tests for missing K-line and below-trigger prices**

```python
def test_breakout_fails_closed_without_twenty_bars():
    result = select_playbook({
        "quote": {"price": 3.97, "change_pct": 3.39, "vol_ratio": 3.33, "amount_wan": 32688},
        "kline": {"bars": []},
        "trigger_price": 4.52,
    })
    assert result["triggered"] is False
    assert result["block_reason"] == "breakout_kline_insufficient"

def test_breakout_fails_closed_below_configured_trigger():
    result = select_playbook({
        "quote": {"price": 3.97, "change_pct": 3.39, "vol_ratio": 3.33, "amount_wan": 32688},
        "kline": {"bars": twenty_structural_bars()},
        "trigger_price": 4.52,
    })
    assert result["triggered"] is False
    assert result["block_reason"] == "breakout_trigger_not_crossed"
```

- [x] **Step 2: Run RED verification**

Run: `./.venv/bin/python -m pytest -q backend/tests/test_playbook_engine.py -k "fails_closed"`

Expected: FAIL because current `select_playbook()` allows a volume breakout without K-line or trigger-price confirmation.

- [x] **Step 3: Implement the minimal structural gate**

```python
if change_pct >= 3 and vol_ratio >= 2 and amount_wan >= 10000:
    if len(bars) < 20:
        return {**base, "playbook": "breakout_watch", "block_reason": "breakout_kline_insufficient", ...}
    trigger_price = _to_float(snapshot.get("trigger_price"))
    if trigger_price > 0 and price < trigger_price:
        return {**base, "playbook": "breakout_watch", "block_reason": "breakout_trigger_not_crossed", ...}
```

Pass `trigger_price` from candidate evidence into both `score_target()` snapshots and intraday candidate snapshots. A positive breakout fixture must contain at least 20 bars and cross the configured trigger.

- [x] **Step 4: Run GREEN and blast-radius verification**

Run: `./.venv/bin/python -m pytest -q backend/tests/test_playbook_engine.py backend/tests/test_target_scoring.py backend/tests/test_quant_lifecycle.py`

Expected: all pass; the 利欧事故 fixture emits no actionable entry.

- [x] **Step 5: Checkpoint without committing unrelated dirty files**

Run: `git diff --check -- backend/app/services/playbook_engine.py backend/app/services/quant_lifecycle.py scripts/daily_report.py backend/tests/test_playbook_engine.py backend/tests/test_target_scoring.py backend/tests/test_quant_lifecycle.py`

Expected: exit 0. Do not stage or commit until the existing dirty-worktree ownership is resolved.

### Task 2: One signal, one authorization

**Files:**
- Modify: `backend/app/services/quant_lifecycle.py`
- Modify: `backend/app/services/notification_gate.py`
- Test: `backend/tests/test_quant_lifecycle.py`
- Test: `backend/tests/test_notification_gate.py`

- [x] **Step 1: Write a failing test that repeated active scans cannot emit a second entry**

```python
assert first_confirmed["alerts"][0]["signal_id"] == stored["entry_signal"]["signal_id"]
assert repeated_scan["alerts"] == []
assert expired_scan["alerts"][0]["action"] == "entry_cancelled"
```

- [x] **Step 2: Run the focused test and verify RED**

Run: `./.venv/bin/python -m pytest -q backend/tests/test_quant_lifecycle.py -k "signal_id or repeated_active"`

- [x] **Step 3: Add a stable signal identity and expiry contract**

The identity payload is `code + playbook + side + trigger_price + first_scan_id`; store it in `entry_signal`. Active scans update observation time but do not re-notify. A changed playbook, expired timestamp, trigger loss, price-limit breach, or missing authorization transitions to `cancelled`.

- [x] **Step 4: Verify notification deduplication**

Run: `./.venv/bin/python -m pytest -q backend/tests/test_quant_lifecycle.py backend/tests/test_notification_gate.py`

### Task 3: Audited portfolio fills

**Files:**
- Modify: `backend/app/services/portfolio_store.py`
- Modify: `backend/app/services/bot_commands.py`
- Test: `backend/tests/test_portfolio_state.py`

- [x] **Step 1: Write failing tests for fill metadata and idempotency**

```python
result = apply_trade_to_user_portfolio(
    path, "buy", "002131", "利欧股份", 200, 3.995,
    trade_date="2026-07-20", fill_id="fill_user_lio_20260720",
    source="user_confirmed_chat", recommendation_id="rec_lio_20260720",
)
assert result["fill"]["fill_id"] == "fill_user_lio_20260720"
assert apply_same_fill_again["duplicate"] is True
assert portfolio["positions"][0]["shares"] == 200
```

- [x] **Step 2: Verify RED**

Run: `./.venv/bin/python -m pytest -q backend/tests/test_portfolio_state.py -k "fill_metadata or idempotent"`

- [x] **Step 3: Extend the trade API without breaking existing callers**

Add keyword-only `fill_id`, `source`, `occurred_at`, `recommendation_id`, `signal_id`, and `fees`. Persist an append-only `trade_events` row and the same identifiers in `trade_history`; reject a duplicate `fill_id` before changing cash or shares.

- [x] **Step 4: Verify portfolio behavior**

Run: `./.venv/bin/python -m pytest -q backend/tests/test_portfolio_state.py backend/tests/test_order_manager.py`

### Task 4: Record the user-confirmed Lio position

**Files:**
- Modify through service: `data/user_portfolio.json`
- Verify: temporary copy plus JSON invariants

- [x] **Step 1: Rehearse against a temporary copy**

Use `CONGXI_PORTFOLIO_PATH` or an explicit temporary path. Assert 200 shares at 3.995, cash decreases by ¥799 before unknown fees, and a single audited fill exists.

- [x] **Step 2: Apply once to the real local portfolio**

Use `fill_id=fill_user_lio_002131_20260720`, `source=user_confirmed_chat`, and leave unavailable recommendation/fee details explicitly pending rather than inventing values.

- [x] **Step 3: Verify real portfolio truth**

Run a read-only JSON check confirming one `002131` position, 200 shares, average cost 3.995, and no duplicate fill.

### Task 5: Recommendation-to-outcome attribution

**Files:**
- Create: `backend/app/services/execution_ledger.py`
- Modify: `backend/app/services/recommendation_review.py`
- Modify: `backend/app/services/prediction_lab.py`
- Test: `backend/tests/test_execution_ledger.py`
- Test: `backend/tests/test_recommendation_review.py`

- [x] **Step 1: Write failing lifecycle tests**

The test must prove `signal → authorization → recommendation → fill → outcome` can be joined, while an unlinked fill is reported as `unattributed` and never changes strategy metrics.

- [x] **Step 2: Implement an append-only execution ledger**

Each event contains `event_id`, `event_type`, `occurred_at`, `code`, `signal_id`, `recommendation_id`, `fill_id`, payload and source. Duplicate `event_id` is idempotent; conflicting reuse fails closed.

- [x] **Step 3: Verify attribution and review output**

Run: `./.venv/bin/python -m pytest -q backend/tests/test_execution_ledger.py backend/tests/test_recommendation_review.py backend/tests/test_prediction_lab.py`

### Task 6: Report-level hard veto

**Files:**
- Modify: `scripts/daily_report.py`
- Test: `backend/tests/test_daily_report_delivery.py`

- [x] **Step 1: Write failing tests for unsynced holdings and report veto**

When a user-confirmed fill is pending portfolio writeback or the main report state is `未触发不买`, executable buy/add rows and notification actions must be zero.

- [x] **Step 2: Implement one visible decision gate**

The first-screen decision object becomes the single source consumed by both Markdown rendering and notifications. Hard-risk and portfolio-truth failures override lower-level candidate actions.

- [x] **Step 3: Verify report behavior with temporary data paths**

Run: `CONGXI_PORTFOLIO_PATH=<tmp> CONGXI_CANDIDATE_POOL_PATH=<tmp> CONGXI_REPORT_ARCHIVE_DIR=<tmp> ./.venv/bin/python -m pytest -q backend/tests/test_daily_report_delivery.py`

### Task 7: Promotion metrics that measure profit, not storytelling

**Files:**
- Modify: `backend/app/services/prediction_lab.py`
- Modify: `backend/app/services/strategy_evidence_lifecycle.py`
- Test: `backend/tests/test_prediction_lab.py`
- Test: `backend/tests/test_strategy_evidence_lifecycle.py`

- [x] **Step 1: Add failing tests for coverage-adjusted precision and net expectancy**

Metrics must expose buy-signal precision, abstention rate, coverage, average win/loss, payoff ratio, net expectancy after cost, profit factor and max drawdown. Missing benchmark, tradability or actual cost keeps `promotion_eligible=False`.

- [x] **Step 2: Implement paired baseline/candidate summaries**

Compare `price_action_v1` and the candidate version on identical independent units. Do not claim improvement when the confidence interval overlaps zero or when accuracy rises only because abstention coverage collapsed.

- [x] **Step 3: Verify promotion lifecycle**

Run: `./.venv/bin/python -m pytest -q backend/tests/test_prediction_lab.py backend/tests/test_prediction_lab_due.py backend/tests/test_strategy_evidence_lifecycle.py`

### Task 8: Final verification and handoff

**Files:**
- Modify: `README.md` only if behavior or runbook changed
- Evidence: `docs/superpowers/reports/2026-07-21-profit-accuracy-baseline.md`
- Verify: all touched files and full relevant suites

- [x] **Step 1: Run focused safety suites**

Run: `./.venv/bin/python -m pytest -q backend/tests/test_playbook_engine.py backend/tests/test_target_scoring.py backend/tests/test_quant_lifecycle.py backend/tests/test_portfolio_state.py backend/tests/test_notification_gate.py backend/tests/test_prediction_lab.py backend/tests/test_prediction_lab_due.py backend/tests/test_strategy_evidence_lifecycle.py backend/tests/test_recommendation_review.py`

- [x] **Step 2: Run repository verification**

Run the project-defined full test and lint commands discovered from README/CI. Then run `git diff --check`.

- [x] **Step 3: Recompute the frozen baseline**

Report independent sample count, outcome coverage, directional accuracy with confidence interval, benchmark/tradability/cost coverage, attributed execution count and net expectancy. Do not state that profitability improved until shadow evidence satisfies Task 7.

- [x] **Step 4: Present remaining risks**

Explicitly separate: implemented locally, tested locally, running service verified, report artifact verified, and strategy profitability not yet proven.
