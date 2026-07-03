# Target Pool Operational Clarity Engineering Review

Date: 2026-07-02
Review target: `docs/superpowers/specs/2026-07-02-target-pool-operational-clarity-spec.md`
Decision: implement in small, test-locked phases

## NOT In Scope

- Auto-trading or broker API integration: requires a separate credential, audit, and safety design.
- New alpha model or stock-picking prompt overhaul: the immediate failure is lifecycle semantics, not discovery.
- Deleting old evidence or historical reports: cleanup must be reversible.
- Replacing Sentinel/Serenity: they remain evidence sources.
- Repricing every historical target: only current report contract and live lifecycle need correction.

## What Already Exists

- `TargetPoolStore` and `PositionWatchStore` already provide file-backed lifecycle primitives.
- `build_target_scores_for_report()` and `score_target()` already compute account executability.
- `build_outside_pool_scan_for_report()` already produces low-account radar candidates.
- `build_next_day_strategy_sections()` already centralizes the report sections.
- `apply_trade_to_user_portfolio()` already applies manual buy/sell input to JSON.
- Daily report delivery tests already cover the renderer, but some assertions currently encode the wrong semantics.

## Scope Challenge

The minimum valuable fix is not a broad data rewrite. It is:

1. Stop rendering outside-pool rows as executable.
2. Make the report vocabulary unambiguous.
3. Persist or clearly separate radar rows.
4. Hide research references from default target-pool counts.
5. Fix cash consistency after manual trades.

This is small enough to land in one focused branch. It should not wait for a full database migration.

## Data Flow

```text
Sentinel / Serenity evidence
          |
          v
  research_reference archive
          |
          | only if account + data + trigger gates pass
          v
 target_pool.json ----------------------+
  executable / watching / removed       |
          |                             |
          v                             |
 build_target_scores_for_report()       |
          |                             |
          +------------+                |
                       v                |
                 daily_report.py        |
                       ^                |
                       |                |
 small_account_discovery --> radar_pool.json
     outside_pool_scan      observation only
```

## State Machine

```text
research_reference
      |
      | affordable + data complete + evidence still fresh
      v
watching
      |
      | price + volume + amount + fund flow trigger
      v
executable
      |
      | user buys
      v
position_watch
      |
      | stale / invalidated / stop hit / thesis broken
      v
removed

radar_pool is separate:

radar_candidate --promotion gates pass--> watching/executable
radar_candidate --stale or invalidated--> expired/removed from radar
```

## Architecture Findings

P1: Primary executable selection currently violates its own section title.

Fix: `_primary_trade_candidate()` must return only `_first_executable_target()`. Move outside-pool rows to `_render_trigger_pool()` or a renamed radar renderer. This is a regression fix and must be test-locked.

P1: Durable pool and report-only radar are inconsistent.

Fix: add `RadarPoolStore` or equivalent durable `data/radar_pool.json` writer for `build_outside_pool_scan_for_report()` output. The report can still receive in-memory rows, but follow-up user questions must query the same durable source.

P1: `research_reference` is allowed in the same default pool vocabulary as tradable targets.

Fix: introduce helper APIs such as `visible_target_items()` and `research_reference_items()`. Default "标的池总数" uses visible target items only.

P2: Manual holding plans are under-specified.

Fix: after manual trade updates, upsert a position watch plan where stop/target evidence exists. Renderer should derive a temporary plan when no store entry exists.

P2: Portfolio cash has two divergent fields.

Fix: normalize cash writes in `recalculate_portfolio()` and `apply_trade_to_user_portfolio()`, then add regression tests.

## Test Coverage Diagram

```text
build_next_day_strategy_sections()
  |
  +-- target_scores executable present
  |     +-- renders first-screen executable buy/add [test]
  |
  +-- no executable + outside_pool_scan present
  |     +-- first screen says no buy [new regression test]
  |     +-- radar section shows observation only [new regression test]
  |
  +-- research_reference present
  |     +-- hidden from default target-pool count [new test]
  |     +-- visible only in research/archive audit [new test]
  |
  +-- positions present
        +-- stored PositionWatchStore plan [new test]
        +-- derived fallback stop/target [new test]

apply_trade_to_user_portfolio()
  |
  +-- buy
  |     +-- available_cash == cash [new test]
  |     +-- total_assets = cash + market value [new test]
  |
  +-- sell
        +-- available_cash == cash [new test]
        +-- realized_pnl updated [existing + extend]
```

## Failure Modes

| New/changed path | Failure mode | Mitigation |
|---|---|---|
| Radar persistence | Stale radar row keeps showing after conditions expire. | Add `last_seen_at` and `expires_at`; hide stale rows by default. |
| Visible target filtering | A genuine executable is hidden because action/status mapping is too strict. | Keep explicit allowed action set and add executable-present regression test. |
| Research archive split | Existing code expects `research_reference` inside `candidate_pool.json`. | Do not physically migrate in MVP; only change default query/render helpers. |
| Position-watch fallback | Derived stop/target looks authoritative. | Mark fallback as `derived` in report text. |
| Cash normalization | Legacy callers read `cash` only. | Keep `cash` as synchronized alias for one release cycle. |
| Report wording | User still sees "标的" and assumes buyable. | First screen must use "可执行标的" only when executable exists; radar heading must say "观察，不等于可买". |

## Worktree Parallelization Strategy

Lane A can run alone first because it changes the central report contract.

```text
Lane A: renderer semantics + report tests
Lane B: portfolio cash + position watch tests
Lane C: radar durable store + visible pool helpers

Recommended order:
  1. Land A.
  2. Run B and C in parallel worktrees.
  3. Merge B + C.
  4. Run full report generation on temp copies.
```

## Implementation Tasks

| ID | Task | Files | Acceptance |
|---|---|---|---|
| T1 | Stop outside-pool promotion to executable | `scripts/daily_report.py`, `backend/tests/test_daily_report_delivery.py` | No outside-pool row appears as `核心主攻`; first screen says no buy when no executable exists. |
| T2 | Rename/report sections around executable vs radar | `scripts/daily_report.py` | Report first screen distinguishes holding, buy, sell, and radar observation. |
| T3 | Add visible target-pool helpers | `backend/app/services/quant_lifecycle.py`, tests | Default count excludes `research_reference`; explicit research count remains available. |
| T4 | Add radar durability | `backend/app/services/quant_lifecycle.py` or new service, `scripts/daily_report.py`, tests | `outside_pool_scan` rows can be queried after report generation without pretending they are target-pool entries. |
| T5 | Fix portfolio cash consistency | `backend/app/services/portfolio_store.py`, `backend/tests/test_portfolio_state.py` | Buy/sell keeps `available_cash == cash`; total assets recompute remains correct. |
| T6 | Improve position action rendering | `scripts/daily_report.py`, position-watch tests | `000629`-style holdings show concrete hold/stop/target/add conditions. |
| T7 | Real report verification | temp env paths + `scripts/daily_report.py` | Report archive generated; delivery status checked; no secret output. |

## Required Verification

```bash
PYTHONPATH=.:backend .venv/bin/pytest -q \
  backend/tests/test_daily_report_delivery.py \
  backend/tests/test_quant_lifecycle.py \
  backend/tests/test_portfolio_state.py \
  backend/tests/test_small_account_discovery.py
```

Then run a report using temporary copies:

```bash
CONGXI_PORTFOLIO_PATH=/tmp/congxi-user-portfolio.json \
CONGXI_CANDIDATE_POOL_PATH=/tmp/congxi-candidate-pool.json \
CONGXI_REPORT_ARCHIVE_DIR=/tmp/congxi-report-archive \
PYTHONPATH=.:backend .venv/bin/python scripts/daily_report.py
```

Manual report checks:

- First screen does not call radar rows executable.
- Current holding appears before new-buy ideas.
- Research references are not counted as default tradable target pool.
- No API key, webhook, cookie, or token is printed.

