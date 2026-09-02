# Runtime Recovery Implementation Plan

> **Spec:** `docs/superpowers/specs/2026-09-02-runtime-recovery-design.md`

## Task 1: Prediction collection stays within its time budget

**Files:**
- Modify: `backend/tests/test_prediction_lab_due.py`
- Modify: `backend/tests/test_tencent_client.py`
- Modify: `backend/tests/test_realtime_kline_scraper.py`
- Modify: `backend/app/data_sources/tencent_client.py`
- Modify: `backend/app/data_sources/realtime_kline_scraper.py`
- Modify: `scripts/run_prediction_lab.py`

1. Add a behavior test proving multiple K-line requests overlap while concurrency stays bounded.
2. Add a behavior test proving one timed-out symbol is skipped with reason `kline_timeout` while healthy symbols are still written.
3. Run the focused tests and confirm they fail against the current serial implementation.
4. Add regression tests for oversized Tencent quote batches, unsupported instruments, and blocking Scrapling calls.
5. Implement 50-symbol quote batching, isolate invalid codes, move blocking Scrapling I/O off the event loop, and use cancellable Tencent K-lines for mass prediction work.
6. Implement bounded concurrency and per-symbol timeout for collection and remote backfill, preserving ledger and universe contracts.
7. Re-run focused tests and prediction test module, then verify current production-data copies complete within the 300-second scheduler budget.

## Task 2: Prediction backfill survives collection failure

**Files:**
- Modify: `backend/tests/test_prediction_lab_due.py`
- Modify: `backend/app/main.py`

1. Add a scheduler behavior test whose collection subprocess times out and whose backfill succeeds.
2. Confirm the test fails because the current shared exception boundary skips backfill.
3. Split the two subprocess stages into independent result boundaries and return structured status for observability.
4. Re-run focused tests and scheduler-related tests.

## Task 3: Feishu retries transient delivery failures

**Files:**
- Modify: `backend/tests/test_feishu_pusher.py`
- Modify: `backend/app/services/feishu_pusher.py`

1. Add tests proving a transient API/Webhook failure is retried, while placeholder configuration causes no request.
2. Confirm the retry test fails with the current one-attempt behavior.
3. Implement bounded retry with injectable sleep and concise failure diagnostics; keep API-first/Webhook-fallback semantics.
4. Re-run Feishu and daily-report delivery tests without real credentials.

## Task 4: Validate Sentinel and operational boundaries

**Files:**
- No production write expected unless validation exposes a new defect.

1. Run the AKShare fallback directly and require non-empty current-date events.
2. Run `run_sentinel.py --mode news` with temporary output, portfolio, and archive paths.
3. Verify zero-event runs remain degraded and never masquerade as success.
4. Verify易淘金 permission failure still blocks writes and exposes a sanitized reason.

## Task 5: Quality gates and deployment readiness

**Files:**
- Modify only if a concrete check exposes a defect.

1. Run focused regression tests, full backend tests, Ruff, and shell checks.
2. Inspect the complete diff for data-path, permission, and secret leaks.
3. Commit the isolated repair branch if all checks pass.
4. Switch the live service only through an explicit, reversible project-path update; verify `/api/health`, scheduler startup, Sentinel result, prediction status, and unchanged account files.

## Self-review

- The design preserves the prediction universe and only changes scheduling/failure isolation.
- Retry counts are bounded, so a Feishu outage cannot create an infinite send loop.
- Sentinel validation writes only to temporary paths.
- The only unresolved external dependency is macOS Accessibility permission for 易淘金; it stays fail-closed.
