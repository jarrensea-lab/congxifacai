# Sentinel Automation and Next-Day Investment Report Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a reliable local automation path for Sentinel and upgrade the daily report into a next-trading-day investment strategy report.

**Architecture:** Keep `launchd` as the local supervisor, FastAPI/APScheduler as the in-app scheduler, and standalone scripts as manual fallback. Add focused Sentinel services for news input packages and review output archiving, then have `scripts/daily_report.py` consume those artifacts while preserving Webhook-only delivery and local Markdown archive guarantees.

**Tech Stack:** Python 3.14, FastAPI, APScheduler, SQLite/SQLAlchemy, launchd plist, pytest, ruff.

---

## File Structure

- Create `backend/app/services/schedule_policy.py`: next-trading-day schedule decisions, Sunday-evening main-report support, and human-readable run reasons.
- Create `backend/app/ai/sentinel_research.py`: build Sentinel news input packages from Tushare/Horizon events and render Markdown summaries.
- Create `scripts/run_sentinel.py`: manual and scheduler-safe Sentinel runner for news import, research package creation, and performance review.
- Create `scripts/congxicai-v7-service.sh`: launchd-safe FastAPI service runner with external-volume write probe.
- Create `scripts/com.zhuchenyuan.congxicai-v7.plist`: project-owned launchd template with current absolute paths.
- Modify `backend/app/main.py`: update APScheduler times, add Sunday main-report job, add Sentinel review job, expose job status in health data.
- Modify `scripts/daily_report.py`: rename main report, add data-source audit, Sentinel research package section, role matrix, and next-day execution playbook.
- Modify tests under `backend/tests/`: add focused regression tests for schedule policy, Sentinel package building, report sections, and launchd plist safety.
- Modify docs/worklists and README/CHANGELOG after verification.

## Task 1: Schedule Policy and Launchd Foundation

**Files:**
- Create: `backend/app/services/schedule_policy.py`
- Create: `backend/tests/test_schedule_policy.py`
- Create: `scripts/congxicai-v7-service.sh`
- Create: `scripts/com.zhuchenyuan.congxicai-v7.plist`
- Modify: `backend/app/main.py`
- Modify: `README.md`

- [ ] **Step 1: Write schedule-policy failing tests**

Add tests for:

```python
from datetime import date, time

from app.services.schedule_policy import (
    main_report_target_date,
    should_run_main_report,
    should_run_premarket_calibration,
)


def test_sunday_evening_main_report_targets_monday():
    assert should_run_main_report(date(2026, 6, 28), time(20, 30)) is True
    assert main_report_target_date(date(2026, 6, 28)).isoformat() == "2026-06-29"


def test_saturday_evening_does_not_run_main_report():
    assert should_run_main_report(date(2026, 6, 27), time(20, 30)) is False


def test_trading_day_evening_targets_next_trading_day():
    assert should_run_main_report(date(2026, 6, 29), time(20, 30)) is True
    assert main_report_target_date(date(2026, 6, 29)).isoformat() == "2026-06-30"


def test_premarket_calibration_only_runs_on_trading_day():
    assert should_run_premarket_calibration(date(2026, 6, 29), time(8, 50)) is True
    assert should_run_premarket_calibration(date(2026, 6, 28), time(8, 50)) is False
```

- [ ] **Step 2: Run schedule-policy tests and verify they fail**

Run:

```bash
env PYTHONPATH=backend .venv/bin/python -m pytest backend/tests/test_schedule_policy.py -q
```

Expected: import failure for `app.services.schedule_policy`.

- [ ] **Step 3: Implement schedule policy**

Create functions:

```python
def main_report_target_date(run_date: date) -> date
def should_run_main_report(run_date: date | None = None, run_time: time | None = None) -> bool
def should_run_premarket_calibration(run_date: date | None = None, run_time: time | None = None) -> bool
def schedule_reason(job: str, run_date: date | None = None) -> str
```

Rules:

- Trading-day evening main report always runs and targets the next trading day.
- Sunday evening main report runs only when the next trading day is Monday.
- Saturday does not run.
- Premarket calibration only runs on a trading day.

- [ ] **Step 4: Add launchd runner and plist**

`scripts/congxicai-v7-service.sh` must:

- `cd` to `/Volumes/Aino Kishi/AI/workflows/恭喜发财`.
- Create `logs/launchd`.
- Probe write access to `/Volumes/Aino Kishi/AI/projects/司库/01-资料采集/量化投资/恭喜发财报告`.
- Start `.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000` with `PYTHONPATH=backend`.

`scripts/com.zhuchenyuan.congxicai-v7.plist` must:

- Label `com.zhuchenyuan.congxicai-v7`.
- Use `/bin/bash` and the project runner script.
- Use `RunAtLoad=true`, `KeepAlive=true`, `ThrottleInterval=30`.
- Write logs to `logs/launchd/congxicai-v7.stdout.log` and `.stderr.log`.

- [ ] **Step 5: Update FastAPI scheduler registration**

In `backend/app/main.py`:

- Change premarket job time to `08:50`, name `盘前短策略校准`.
- Change daily/main report job time to `20:30`, name `次日投资策略主报告`.
- Add Sunday `20:30` main report cron with `day_of_week='sun'`.
- Add Sentinel review job at `21:00`.
- Guard job bodies with `schedule_policy` so cron wakeups skip safely when the date is not eligible.

- [ ] **Step 6: Verify task**

Run:

```bash
env PYTHONPATH=backend .venv/bin/python -m pytest backend/tests/test_schedule_policy.py -q
.venv/bin/python -m ruff check backend/app/services/schedule_policy.py backend/app/main.py
```

Expected: tests pass and ruff passes.

## Task 2: Sentinel News Package and Review Runner

**Files:**
- Create: `backend/app/ai/sentinel_research.py`
- Create: `backend/tests/test_sentinel_research.py`
- Create: `scripts/run_sentinel.py`
- Modify: `backend/app/main.py`

- [ ] **Step 1: Write failing Sentinel research tests**

Test expectations:

- `build_news_research_package(events, report_date)` returns `date`, `event_count`, `key_event_count`, `top_themes`, `top_symbols`, `risk_events`, and `source_status`.
- `render_research_package_markdown(package)` includes source counts and never emits forbidden trading actions.
- `persist_research_package(package, output_root)` writes JSON and Markdown.

- [ ] **Step 2: Run tests and verify they fail**

Run:

```bash
env PYTHONPATH=backend .venv/bin/python -m pytest backend/tests/test_sentinel_research.py -q
```

Expected: import failure for `app.ai.sentinel_research`.

- [ ] **Step 3: Implement Sentinel research package**

Implementation rules:

- Count themes from `event["themes"]`.
- Count symbols from `event["symbols"]`.
- Treat key events as `event["is_key"] is True`.
- Risk events are events whose content contains one of `风险`, `监管`, `下跌`, `亏损`, `减持`, `处罚`, `退市`, `暴雷`.
- Persist to:
  - `data/sentinel/research_packages/YYYY-MM-DD.json`
  - `data/sentinel/reports/YYYY-MM-DD_sentinel_research_package.md`

- [ ] **Step 4: Implement `scripts/run_sentinel.py`**

Commands:

```bash
env PYTHONPATH=backend .venv/bin/python scripts/run_sentinel.py --date 2026-06-28 --mode news
env PYTHONPATH=backend .venv/bin/python scripts/run_sentinel.py --date 2026-06-28 --mode review
env PYTHONPATH=backend .venv/bin/python scripts/run_sentinel.py --date 2026-06-28 --mode all
```

Behavior:

- `news`: import default Tushare news, write `news_events`, build and persist research package.
- `review`: generate scorecard artifacts from existing outcomes if present; if none exist, write a small status message and exit 0.
- `all`: run both.

- [ ] **Step 5: Wire Sentinel review job**

`backend/app/main.py` should call a small async wrapper that runs the Sentinel runner logic in-process or via `asyncio.to_thread`.

- [ ] **Step 6: Verify task**

Run:

```bash
env PYTHONPATH=backend .venv/bin/python -m pytest backend/tests/test_sentinel_research.py backend/tests/test_horizon_news_importer.py backend/tests/test_sentinel_role_performance.py -q
.venv/bin/python -m ruff check backend/app/ai/sentinel_research.py scripts/run_sentinel.py
```

Expected: tests and ruff pass.

## Task 3: Next-Day Main Report Upgrade

**Files:**
- Modify: `scripts/daily_report.py`
- Modify: `backend/tests/test_daily_report_delivery.py`

- [ ] **Step 1: Write failing report-section tests**

Add tests that generated Markdown includes:

- `## 一、明日总策略`
- `## 三、数据源审计`
- `## 五、Sentinel 研究输入`
- `## 六、四人辩论矩阵`
- `## 七、裁判裁决`
- `## 八、明日执行剧本`

Also test archive filename ends with `YYYY-MM-DD_次日投资策略主报告.md`.

- [ ] **Step 2: Run tests and verify they fail**

Run:

```bash
env PYTHONPATH=backend .venv/bin/python -m pytest backend/tests/test_daily_report_delivery.py -q
```

Expected: assertions fail until sections are added.

- [ ] **Step 3: Implement report helpers**

Add focused helpers in `scripts/daily_report.py`:

```python
def build_data_source_audit(...)
def load_sentinel_research_package(report_date: str) -> dict
def build_role_matrix(roles: dict) -> str
def build_next_day_execution_playbook(...)
```

Rules:

- Data source audit must include market data, Tushare news, Sentinel, DeepSeek, Qwen, portfolio, SQLite.
- Sentinel section must show degraded state if package missing.
- Role matrix must show hunter, accountant, guardian, researcher even if one is empty.
- Execution playbook must put account affordability before any observation list.

- [ ] **Step 4: Rename main report archive title**

Change the final `save_report_to_obsidian(... title=...)` call to use `title="次日投资策略主报告"`.

- [ ] **Step 5: Verify task**

Run:

```bash
env PYTHONPATH=backend .venv/bin/python -m pytest backend/tests/test_daily_report_delivery.py backend/tests/test_runtime_regressions.py -q
.venv/bin/python -m ruff check scripts/daily_report.py
```

Expected: tests and ruff pass.

## Task 4: End-to-End Automation Verification

**Files:**
- Modify: `README.md`
- Modify: `CHANGELOG.md`
- Modify: `docs/worklists/2026-06-28-sentinel-automation-investment-report-worklist.md`

- [ ] **Step 1: Run focused tests**

Run:

```bash
env PYTHONPATH=backend .venv/bin/python -m pytest backend/tests/test_schedule_policy.py backend/tests/test_sentinel_research.py backend/tests/test_daily_report_delivery.py backend/tests/test_horizon_news_importer.py backend/tests/test_sentinel_role_performance.py -q
```

- [ ] **Step 2: Run full test suite and lint**

Run:

```bash
env PYTHONPATH=backend .venv/bin/python -m pytest backend/tests/ -q
.venv/bin/python -m ruff check backend scripts
```

- [ ] **Step 3: Run Sentinel news import for 2026-06-28**

Run:

```bash
env PYTHONPATH=backend .venv/bin/python scripts/run_sentinel.py --date 2026-06-28 --mode news
```

Expected: nonzero news count and research package paths printed.

- [ ] **Step 4: Validate launchd plist without loading**

Run:

```bash
plutil -lint scripts/com.zhuchenyuan.congxicai-v7.plist
bash -n scripts/congxicai-v7-service.sh
```

Expected: plist OK and shell syntax OK.

- [ ] **Step 5: Load launchd service only after local script validation**

Run:

```bash
mkdir -p ~/Library/LaunchAgents
cp scripts/com.zhuchenyuan.congxicai-v7.plist ~/Library/LaunchAgents/com.zhuchenyuan.congxicai-v7.plist
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.zhuchenyuan.congxicai-v7.plist 2>/dev/null || true
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.zhuchenyuan.congxicai-v7.plist
launchctl kickstart -k gui/$(id -u)/com.zhuchenyuan.congxicai-v7
launchctl print gui/$(id -u)/com.zhuchenyuan.congxicai-v7 | sed -n '1,80p'
```

Expected: service exists and has no immediate `last exit code` failure. If launchd cannot access the external volume, stop and report because this is a local permission/security boundary.

- [ ] **Step 6: Verify FastAPI health**

Run:

```bash
curl -s http://127.0.0.1:8000/api/health
```

Expected: JSON with `status: ok` and `database: ok`.

- [ ] **Step 7: Update docs**

README and CHANGELOG must mention:

- v7.3 automation direction.
- launchd service name.
- main report timing and Sunday-evening rule.
- Sentinel research package and Webhook-only boundary.

- [ ] **Step 8: Final verification**

Run tests and ruff again. Then report changed files, loaded service status, report paths, and any skipped verification.

## Self-Review Notes

- This plan covers worklist P0-P3 at a minimal useful level.
- It keeps Sentinel research-only and does not add auto-trading.
- It preserves Webhook-only delivery.
- It treats launchd loading as reversible: project plist first, copied user LaunchAgent second, `bootout` fallback before `bootstrap`.
- It does not delete OHHF `.env.local`.

