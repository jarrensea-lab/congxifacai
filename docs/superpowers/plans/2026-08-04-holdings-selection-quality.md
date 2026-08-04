# Holdings Selection Quality Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add deterministic, cycle-aware financial quality, per-lot risk audit, held-position context, and historical recommendation price freshness to the target scorecard, then generate a safe 2026-08-05 strategy from temporary copies.

**Architecture:** Keep the existing Target Pool, composite score, playbook, visible decision gate, and Yitaojin safety boundaries. Add two pure evaluators (`financial_quality.py` and `recommendation_reference.py`), expose their results through `target_scoring.py`, and pass real holding context from `scripts/daily_report.py`; no research source can bypass existing production eligibility or risk gates.

**Tech Stack:** Python 3.14, pytest, Ruff, existing JSON Target Pool stores, Markdown report generator.

---

## File map

- Create `backend/app/services/financial_quality.py`: normalize financial metrics and return cycle-aware quality/valuation audit.
- Create `backend/tests/test_financial_quality.py`: direct behavior tests for quality, red flags, missing inputs, and cyclicals.
- Modify `backend/app/services/composite_score.py`: map financial quality into the existing 15-point component.
- Modify `backend/tests/test_composite_score.py`: verify cycle-aware fundamental scoring.
- Modify `backend/app/services/position_sizing.py`: return per-lot risk and concentration fields on every path.
- Modify `backend/tests/test_position_sizing.py`: verify risk audit on allowed and blocked candidates.
- Create `backend/app/services/recommendation_reference.py`: compare the stored recommendation quote with the current snapshot quote.
- Create `backend/tests/test_recommendation_reference.py`: verify current, stale-divergence, and unverifiable states.
- Modify `backend/app/services/target_scoring.py`: expose financial, risk, held-position, and historical-reference fields without weakening hard gates.
- Modify `backend/tests/test_target_scoring.py`: verify held `buy` becomes `add`, research-only holdings remain managed, and risk/reference fields survive blocking.
- Modify `scripts/daily_report.py`: pass holding codes and stored recommendation data into scoring, then persist the new audit fields.
- Modify `backend/tests/test_target_pool_daily_refresh.py` and `backend/tests/test_daily_report_delivery.py`: verify holding context and persisted audit fields.
- Add `docs/reviews/2026-08-05-strategy-quality-review.md`: record the generated strategy path, current holdings, blocked entries, freshness limits, and review outcome.

### Task 1: Financial quality evaluator

**Files:**
- Create: `backend/app/services/financial_quality.py`
- Create: `backend/tests/test_financial_quality.py`

- [ ] **Step 1: Write failing tests for compounder quality and red flags**

```python
from app.services.financial_quality import assess_financial_quality


def test_compounder_quality_rewards_cash_conversion_and_clean_working_capital():
    result = assess_financial_quality({
        "status": "ok",
        "earnings_profile": "compounder",
        "revenue_yoy_pct": 18,
        "gross_margin_pct": 35,
        "gross_margin_yoy_pct": 2,
        "inventory_yoy_pct": 8,
        "receivable_yoy_pct": 9,
        "operating_cashflow_yoy_pct": 25,
        "free_cash_flow": 120_000_000,
        "debt_to_asset_pct": 32,
        "pe_ttm": 24,
    })

    assert result["score"] >= 80
    assert result["coverage"] >= 0.8
    assert result["valuation_eligible"] is True
    assert result["flags"] == []


def test_working_capital_and_cash_flow_deterioration_are_auditable():
    result = assess_financial_quality({
        "status": "ok",
        "revenue_yoy_pct": 5,
        "gross_margin_yoy_pct": -3,
        "inventory_yoy_pct": 28,
        "receivable_yoy_pct": 31,
        "operating_cashflow_yoy_pct": -45,
        "free_cash_flow": -1,
    })

    assert result["score"] < 50
    assert set(result["flags"]) >= {
        "gross_margin_declining",
        "inventory_outpaces_revenue",
        "receivable_outpaces_revenue",
        "operating_cashflow_deteriorating",
        "free_cash_flow_negative",
    }
```

- [ ] **Step 2: Run the tests and verify RED**

Run: `PYTHONPATH=.:backend /Volumes/kishi\ aino/AI/workflows/恭喜发财/.venv/bin/python -m pytest backend/tests/test_financial_quality.py -q`

Expected: FAIL because `app.services.financial_quality` does not exist.

- [ ] **Step 3: Implement the pure evaluator**

Implement `assess_financial_quality(financial)` with explicit metric aliases, weighted available-score calculation, coverage, `earnings_profile`, flags, and `valuation_eligible`. Accept only `cyclical`, `compounder`, or `unknown`; treat every other value as `unknown`. For `cyclical`, set `valuation_eligible=True` only when `normalized_earnings_positive` is true or free cash flow is positive.

- [ ] **Step 4: Run the focused tests and verify GREEN**

Run: `PYTHONPATH=.:backend /Volumes/kishi\ aino/AI/workflows/恭喜发财/.venv/bin/python -m pytest backend/tests/test_financial_quality.py -q`

Expected: all tests pass.

- [ ] **Step 5: Commit Task 1**

```bash
git add backend/app/services/financial_quality.py backend/tests/test_financial_quality.py
git commit -m "feat: add cycle-aware financial quality"
```

### Task 2: Composite score financial integration

**Files:**
- Modify: `backend/app/services/composite_score.py`
- Modify: `backend/tests/test_composite_score.py`

- [ ] **Step 1: Write a failing cyclicals test**

Add a test that builds two otherwise identical snapshots with `earnings_profile="cyclical"` and low PE. The first has negative free cash flow and no normalized earnings; the second has positive free cash flow. Assert the first does not receive valuation bonus and scores lower in `fundamental_valuation`.

- [ ] **Step 2: Run the exact test and verify RED**

Run: `PYTHONPATH=.:backend /Volumes/kishi\ aino/AI/workflows/恭喜发财/.venv/bin/python -m pytest backend/tests/test_composite_score.py -q`

Expected: FAIL because the current component awards low-PE points without cycle evidence.

- [ ] **Step 3: Replace the simple financial component with the evaluator**

Import `assess_financial_quality`. Convert its quality score and coverage into at most 12 points and allow up to 3 valuation points only when `valuation_eligible` is true. Include `earnings_profile`, coverage, and flags in the reason string; keep the component maximum at 15 and preserve missing-source semantics.

- [ ] **Step 4: Run composite and target-scoring tests**

Run: `PYTHONPATH=.:backend /Volumes/kishi\ aino/AI/workflows/恭喜发财/.venv/bin/python -m pytest backend/tests/test_composite_score.py backend/tests/test_target_scoring.py -q`

Expected: all tests pass.

- [ ] **Step 5: Commit Task 2**

```bash
git add backend/app/services/composite_score.py backend/tests/test_composite_score.py
git commit -m "feat: score financial quality deterministically"
```

### Task 3: Per-lot risk and concentration audit

**Files:**
- Modify: `backend/app/services/position_sizing.py`
- Modify: `backend/tests/test_position_sizing.py`

- [ ] **Step 1: Write failing tests for allowed and blocked one-lot risk**

```python
def test_position_size_reports_one_lot_risk_and_concentration():
    result = calculate_position_size(
        code="002185",
        entry_price=15.53,
        stop_loss=13.98,
        available_cash=6281.8,
        total_assets=10058.8,
        profile=_profile(risk_per_trade_pct=2),
    )
    assert result["risk_per_lot"] == 155.0
    assert result["lot_concentration_pct"] == 15.44
    assert result["risk_budget_utilization_pct"] == 77.05


def test_blocked_position_still_reports_risk_audit():
    result = calculate_position_size(
        code="002837",
        entry_price=49.43,
        stop_loss=44.49,
        available_cash=6281.8,
        total_assets=10058.8,
        profile=_profile(risk_per_trade_pct=2),
    )
    assert result["block_reason"] == "risk_budget_too_small"
    assert result["risk_per_lot"] == 494.0
    assert result["lot_concentration_pct"] == 49.14
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `PYTHONPATH=.:backend /Volumes/kishi\ aino/AI/workflows/恭喜发财/.venv/bin/python -m pytest backend/tests/test_position_sizing.py -q`

Expected: FAIL with missing audit keys.

- [ ] **Step 3: Add the audit fields to the shared base result**

Calculate one-lot value/risk after validating entry and stop. Return rounded `risk_per_lot`, `risk_budget_utilization_pct`, and `lot_concentration_pct` from every subsequent branch, including `risk_budget_too_small` and `lot_size_exceeded`.

- [ ] **Step 4: Run position and target scoring tests**

Run: `PYTHONPATH=.:backend /Volumes/kishi\ aino/AI/workflows/恭喜发财/.venv/bin/python -m pytest backend/tests/test_position_sizing.py backend/tests/test_target_scoring.py -q`

Expected: all tests pass.

- [ ] **Step 5: Commit Task 3**

```bash
git add backend/app/services/position_sizing.py backend/tests/test_position_sizing.py
git commit -m "feat: expose per-lot risk audit"
```

### Task 4: Historical recommendation freshness

**Files:**
- Create: `backend/app/services/recommendation_reference.py`
- Create: `backend/tests/test_recommendation_reference.py`

- [ ] **Step 1: Write failing direct tests**

```python
from app.services.recommendation_reference import audit_recommendation_reference


def test_reference_quote_is_stale_after_large_price_divergence():
    result = audit_recommendation_reference(
        {"realtime_quote": {"price": 5.68, "trading_date": "2026-08-03"}},
        current_price=7.96,
    )
    assert result["status"] == "stale_divergence"
    assert result["usable_for_current_action"] is False


def test_reference_quote_within_five_percent_is_current():
    result = audit_recommendation_reference(
        {"realtime_quote": {"price": 10.0}},
        current_price=10.4,
    )
    assert result["status"] == "current"
    assert result["usable_for_current_action"] is True


def test_reference_quote_without_prices_is_unverifiable():
    result = audit_recommendation_reference({}, current_price=10.0)
    assert result["status"] == "unverifiable"
    assert result["usable_for_current_action"] is False
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `PYTHONPATH=.:backend /Volumes/kishi\ aino/AI/workflows/恭喜发财/.venv/bin/python -m pytest backend/tests/test_recommendation_reference.py -q`

Expected: FAIL because the module does not exist.

- [ ] **Step 3: Implement the pure audit function**

Return `status`, `reference_price`, `current_price`, `divergence_pct`, `reference_trading_date`, and `usable_for_current_action`. Use absolute percentage divergence from the stored reference price and a default 5% limit. Never edit the historical recommendation.

- [ ] **Step 4: Run the focused tests and verify GREEN**

Run: `PYTHONPATH=.:backend /Volumes/kishi\ aino/AI/workflows/恭喜发财/.venv/bin/python -m pytest backend/tests/test_recommendation_reference.py -q`

Expected: all tests pass.

- [ ] **Step 5: Commit Task 4**

```bash
git add backend/app/services/recommendation_reference.py backend/tests/test_recommendation_reference.py
git commit -m "feat: audit historical recommendation prices"
```

### Task 5: Target scorecard holding context and audit propagation

**Files:**
- Modify: `backend/app/services/target_scoring.py`
- Modify: `backend/tests/test_target_scoring.py`
- Modify: `scripts/daily_report.py`
- Modify: `backend/tests/test_target_pool_daily_refresh.py`
- Modify: `backend/tests/test_daily_report_delivery.py`

- [ ] **Step 1: Write failing target-scoring tests**

Add tests asserting:

```python
result = score_target(
    snapshot,
    available_cash=6281.8,
    total_assets=10058.8,
    is_held=True,
)
assert result["position_context"] == "held"
assert result["position_management_required"] is True
assert result["entry_action"] in {"add", "watch", "research_only"}
assert result["risk_per_lot"] > 0
assert result["financial_quality_score"] >= 0
```

For a triggered research-only holding, assert `action="research_only"`, `entry_action="research_only"`, and `position_management_required=True`. For a normal held buy, assert the public action and entry action are `add`, not `buy`.

- [ ] **Step 2: Run target tests and verify RED**

Run: `PYTHONPATH=.:backend /Volumes/kishi\ aino/AI/workflows/恭喜发财/.venv/bin/python -m pytest backend/tests/test_target_scoring.py -q`

Expected: FAIL because `is_held` and the new fields do not exist.

- [ ] **Step 3: Implement scorecard propagation**

In `score_target`, add keyword `is_held=False`, calculate `financial_quality`, calculate `historical_reference` from `snapshot["historical_recommendation"]`, copy risk fields from sizing, and finalize `position_context`, `position_management_required`, and `entry_action`. Convert an allowed held `buy` to `add`; do not convert blocked or research-only actions.

- [ ] **Step 4: Write failing report-refresh tests**

Add a test where `held_codes={"000100"}` and the pool contains `000100` plus another item. Assert the fake scorer receives `is_held=True` only for `000100`, receives the stored recommendation in `snapshot["historical_recommendation"]`, and the persisted `scoring_decision` contains the new financial, risk, position, and reference fields.

- [ ] **Step 5: Run report-refresh tests and verify RED**

Run: `PYTHONPATH=.:backend /Volumes/kishi\ aino/AI/workflows/恭喜发财/.venv/bin/python -m pytest backend/tests/test_target_pool_daily_refresh.py backend/tests/test_daily_report_delivery.py -q`

Expected: FAIL because the report entry does not accept or persist holding context.

- [ ] **Step 6: Implement daily report propagation**

Add optional `held_codes` to `build_target_scores_for_report`, normalize it once, inject `item.get("last_recommendation")` into the snapshot, pass `is_held`, and persist these exact keys in `scoring_decision`:

```python
"financial_quality_score"
"financial_quality_coverage"
"financial_quality_flags"
"earnings_profile"
"risk_per_lot"
"risk_budget_utilization_pct"
"lot_concentration_pct"
"position_context"
"position_management_required"
"entry_action"
"historical_reference_status"
"historical_reference_divergence_pct"
```

At the main call site, derive held codes from the loaded portfolio positions.

- [ ] **Step 7: Run all changed-area tests**

Run: `PYTHONPATH=.:backend /Volumes/kishi\ aino/AI/workflows/恭喜发财/.venv/bin/python -m pytest backend/tests/test_financial_quality.py backend/tests/test_recommendation_reference.py backend/tests/test_composite_score.py backend/tests/test_position_sizing.py backend/tests/test_target_scoring.py backend/tests/test_target_pool_daily_refresh.py backend/tests/test_daily_report_delivery.py -q`

Expected: all tests pass.

- [ ] **Step 8: Commit Task 5**

```bash
git add backend/app/services/target_scoring.py backend/tests/test_target_scoring.py scripts/daily_report.py backend/tests/test_target_pool_daily_refresh.py backend/tests/test_daily_report_delivery.py
git commit -m "feat: separate holdings from entry eligibility"
```

### Task 6: Generate and review the 2026-08-05 strategy

**Files:**
- Create: `docs/reviews/2026-08-05-strategy-quality-review.md`

- [ ] **Step 1: Create an isolated report fixture directory**

Declare `STRATEGY_TMP_DIR=$(mktemp -d)`; copy the current production `data/user_portfolio.json`, `data/candidate_pool.json`, and `data/position_watch.json` into it, and point all writable environment variables to that directory. Set `FEISHU_WEBHOOK_URL=` and do not enable Yitaojin writes.

- [ ] **Step 2: Generate the report for the explicit service dates**

Run with:

```bash
CONGXI_REPORT_DATE=2026-08-04 \
CONGXI_TARGET_DATE=2026-08-05 \
CONGXI_PORTFOLIO_PATH="$STRATEGY_TMP_DIR/user_portfolio.json" \
CONGXI_CANDIDATE_POOL_PATH="$STRATEGY_TMP_DIR/candidate_pool.json" \
CONGXI_POSITION_WATCH_PATH="$STRATEGY_TMP_DIR/position_watch.json" \
CONGXI_VISIBLE_DECISION_GATE_PATH="$STRATEGY_TMP_DIR/visible_decision_gate.json" \
CONGXI_REPORT_ARCHIVE_DIR="$STRATEGY_TMP_DIR/reports" \
FEISHU_WEBHOOK_URL= \
PYTHONPATH=.:backend /Volumes/kishi\ aino/AI/workflows/恭喜发财/.venv/bin/python scripts/daily_report.py
```

Expected: a Markdown report exists under the temporary archive; no production JSON changes and no external message is sent.

- [ ] **Step 3: Review the report contract**

Verify the first screen names both current holdings, gives explicit hold/sell/add conditions, names every blocked data source or risk gate, exposes no research-only item as a buy, and treats short-video themes as research context only.

- [ ] **Step 4: Record the review**

Write `docs/reviews/2026-08-05-strategy-quality-review.md` with source timestamps, report path, holdings facts, entry decision, risk levels, freshness limitations, and a pass/fail table. Do not include sensitive account identifiers or credentials.

- [ ] **Step 5: Commit the review**

```bash
git add docs/reviews/2026-08-05-strategy-quality-review.md
git commit -m "docs: review august 5 strategy"
```

### Task 7: Final verification and draft PR

**Files:**
- Verify all changed files and documentation.

- [ ] **Step 1: Run the full test suite**

Run: `PYTHONPATH=.:backend /Volumes/kishi\ aino/AI/workflows/恭喜发财/.venv/bin/python -m pytest backend/tests -q`

Expected: zero failures.

- [ ] **Step 2: Run static and script validation**

```bash
/Volumes/kishi\ aino/AI/workflows/恭喜发财/.venv/bin/python -m ruff check backend scripts
bash -n scripts/congxicai-v7-service.sh scripts/guardian.sh scripts/install-congxicai-v7-launchd.sh
plutil -lint scripts/com.zhuchenyuan.congxicai-v7.plist
```

Expected: all commands exit 0.

- [ ] **Step 3: Inspect the exact diff and repository state**

Run: `git status -sb && git diff origin/main...HEAD --check && git diff --stat origin/main...HEAD`

Expected: only the approved feature, tests, spec, plan, and strategy review are present; no secret or runtime artifact is tracked.

- [ ] **Step 4: Push and open a Draft PR**

Push `codex/douyin-holdings-selection-optimization`, then open a Draft PR to `main`. The PR body must explain the safety boundary, TDD evidence, report-generation method, strategy freshness limits, and checks.
