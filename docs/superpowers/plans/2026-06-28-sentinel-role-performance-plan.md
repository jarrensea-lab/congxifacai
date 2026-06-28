# Sentinel Role Performance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a file-based Sentinel performance review layer that records role predictions, evaluates outcomes, separates user execution discipline, scores roles and the judge, and renders reports without changing trading behavior.

**Architecture:** Add a new `backend/app/ai/sentinel_role_performance.py` module as a旁路 evidence layer. It writes JSONL/JSON/Markdown under `data/sentinel/`, exposes pure functions for testing, and is optionally called from `run_debate()` after the existing debate snapshot is saved. Existing `DebateTracker`, trading engine, and risk guard remain unchanged.

**Tech Stack:** Python stdlib, pytest, existing `backend` package layout.

---

## File Structure

- Create `backend/app/ai/sentinel_role_performance.py`
  - Owns prediction extraction, JSONL persistence, outcome evaluation, scoring, adjustment suggestions, and Markdown rendering.
- Create `backend/tests/test_sentinel_role_performance.py`
  - Tests schema, scoring, execution discipline separation, report rendering, and no forbidden trading actions.
- Modify `backend/app/engine/workshop.py`
  - After existing `DebateTracker.save(...)`, best-effort append role predictions. Failure must not affect the mature report/debate flow.
- Update `docs/sentinel/2026-06-28-sentinel-role-performance-worklist.md`
  - Mark completed implementation phases after verification.

## Task 1: Prediction Recording

**Files:**
- Create: `backend/app/ai/sentinel_role_performance.py`
- Test: `backend/tests/test_sentinel_role_performance.py`

- [ ] Write failing tests for role prediction records.
- [ ] Implement role extraction for `hunter`, `accountant`, `guardian`, `researcher`, and `judge`.
- [ ] Persist records to `data/sentinel/role_predictions/YYYY-MM-DD.jsonl`.
- [ ] Verify records include `role`, `prediction_type`, `target`, `horizon`, `confidence`, and `source_report`.

## Task 2: Outcome Evaluation

**Files:**
- Modify: `backend/app/ai/sentinel_role_performance.py`
- Test: `backend/tests/test_sentinel_role_performance.py`

- [ ] Write failing tests for due/not-due/unavailable outcomes.
- [ ] Implement outcome evaluation for direction hit, return error, benchmark excess return, and risk hit/miss.
- [ ] Persist evaluated outcomes to `data/sentinel/role_outcomes/YYYY-MM-DD.jsonl`.

## Task 3: Role Scoring and Execution Discipline

**Files:**
- Modify: `backend/app/ai/sentinel_role_performance.py`
- Test: `backend/tests/test_sentinel_role_performance.py`

- [ ] Write failing tests showing user non-execution does not penalize a role.
- [ ] Implement role score weights: result 40, duty 35, risk 15, explanation 10.
- [ ] Implement judge score weights: account impact 50, synthesis 30, risk compliance 20.
- [ ] Implement separate execution discipline records and scoring.

## Task 4: Advice Performance

**Files:**
- Modify: `backend/app/ai/sentinel_role_performance.py`
- Test: `backend/tests/test_sentinel_role_performance.py`

- [ ] Write failing tests distinguishing paper advice from executed advice.
- [ ] Implement win rate, average return, max drawdown, benchmark comparison, and risk improvement summaries.
- [ ] Persist summaries to `data/sentinel/advice_performance/YYYY-MM-DD.json`.

## Task 5: Adjustment Suggestions and Reports

**Files:**
- Modify: `backend/app/ai/sentinel_role_performance.py`
- Test: `backend/tests/test_sentinel_role_performance.py`

- [ ] Write failing tests for allowed suggestion actions only.
- [ ] Implement `increase_weight`, `decrease_weight`, `keep_weight`, `watch_role`, `review_prompt`, and `require_human_review` suggestions.
- [ ] Render `sentinel_role_scorecard.md` and `sentinel_advice_performance.md`.
- [ ] Ensure reports explain role quality versus user execution discipline.

## Task 6: Debate Flow Hook

**Files:**
- Modify: `backend/app/engine/workshop.py`
- Test: `backend/tests/test_sentinel_role_performance.py` or `backend/tests/test_runtime_regressions.py`

- [ ] Write failing test that `run_debate()` best-effort saves Sentinel predictions.
- [ ] Hook prediction recording after the existing `DebateTracker.save(...)`.
- [ ] Ensure exceptions are logged and never break `run_debate()`.

## Task 7: Verification and Worklist Update

**Files:**
- Modify: `docs/sentinel/2026-06-28-sentinel-role-performance-worklist.md`

- [ ] Run focused Sentinel role performance tests.
- [ ] Run existing Sentinel/Horizon focused tests.
- [ ] Run runtime regression tests.
- [ ] Mark implemented worklist items without overstating unimplemented automation or Feishu push.
