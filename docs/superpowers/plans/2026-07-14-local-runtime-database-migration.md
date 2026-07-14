# Local Runtime Database Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move mutable SQLite runtime state off the external project volume, split APScheduler jobs from business data, and migrate the live database with verified rollback evidence.

**Architecture:** `backend/app/config.py` becomes the single source of truth for a local runtime state directory under `~/Library/Application Support/congxicai-v7`. SQLAlchemy business sessions use `stock_data.db`; APScheduler uses a separate `scheduler_jobs.db`. A repeatable migration CLI uses SQLite's backup API, validates integrity and row counts, writes through temporary files, preserves a timestamped rollback snapshot, and never deletes the legacy database.

**Tech Stack:** Python 3.12+, SQLite backup API, SQLAlchemy, APScheduler, pytest, launchd.

---

### Task 1: Centralize local runtime paths

**Files:**
- Modify: `backend/app/config.py`
- Modify: `backend/app/database.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_runtime_database_paths.py`

- [x] **Step 1: Write failing path tests**

```python
def test_runtime_database_defaults_live_under_user_library(monkeypatch):
    monkeypatch.delenv("CONGXI_DATABASE_PATH", raising=False)
    paths = resolve_runtime_database_paths()
    assert "Library/Application Support/congxicai-v7" in paths.business
    assert paths.business != paths.scheduler

def test_runtime_database_environment_overrides_are_respected(monkeypatch, tmp_path):
    monkeypatch.setenv("CONGXI_DATABASE_PATH", str(tmp_path / "business.db"))
    monkeypatch.setenv("CONGXI_SCHEDULER_DATABASE_PATH", str(tmp_path / "jobs.db"))
    paths = resolve_runtime_database_paths()
    assert paths.business == str(tmp_path / "business.db")
    assert paths.scheduler == str(tmp_path / "jobs.db")
```

- [x] **Step 2: Verify tests fail**

Run: `PYTHONPATH=.:backend .venv/bin/python -m pytest backend/tests/test_runtime_database_paths.py -q`

Expected: import failure because `resolve_runtime_database_paths` does not exist.

- [x] **Step 3: Implement one path resolver**

```python
@dataclass(frozen=True)
class RuntimeDatabasePaths:
    business: str
    scheduler: str

def resolve_runtime_database_paths() -> RuntimeDatabasePaths:
    state_dir = Path(os.getenv("CONGXI_STATE_DIR", "~/Library/Application Support/congxicai-v7")).expanduser()
    return RuntimeDatabasePaths(
        business=str(Path(os.getenv("CONGXI_DATABASE_PATH", state_dir / "stock_data.db")).expanduser()),
        scheduler=str(Path(os.getenv("CONGXI_SCHEDULER_DATABASE_PATH", state_dir / "scheduler_jobs.db")).expanduser()),
    )
```

Use the resolver in both `database.py` and `main.py`; create both parent directories before engine/job-store construction.

- [x] **Step 4: Verify targeted tests pass**

Run: `PYTHONPATH=.:backend .venv/bin/python -m pytest backend/tests/test_runtime_database_paths.py backend/tests/test_runtime_regressions.py -q`

Expected: all pass.

### Task 2: Build a safe repeatable migration CLI

**Files:**
- Create: `scripts/migrate_runtime_databases.py`
- Test: `backend/tests/test_runtime_database_migration.py`

- [x] **Step 1: Write failing migration tests**

```python
def test_migration_splits_business_and_scheduler_tables(tmp_path):
    result = migrate_runtime_databases(source, business, scheduler, backup_dir)
    assert result["business_counts"] == source_business_counts
    assert result["scheduler_jobs"] == 2
    assert "apscheduler_jobs" not in business_tables
    assert scheduler_tables == {"apscheduler_jobs"}

def test_migration_refuses_to_overwrite_existing_destinations(tmp_path):
    business.write_bytes(b"occupied")
    with pytest.raises(FileExistsError):
        migrate_runtime_databases(source, business, scheduler, backup_dir)
```

- [x] **Step 2: Verify tests fail**

Run: `PYTHONPATH=.:backend .venv/bin/python -m pytest backend/tests/test_runtime_database_migration.py -q`

Expected: import failure because the migration module does not exist.

- [x] **Step 3: Implement atomic migration**

The implementation must:

```python
source_conn.backup(business_temp_conn)
source_conn.backup(backup_conn)
business_temp_conn.execute("DROP TABLE IF EXISTS apscheduler_jobs")
create_scheduler_schema(scheduler_temp_conn)
scheduler_temp_conn.executemany(
    "INSERT INTO apscheduler_jobs(id, next_run_time, job_state) VALUES (?, ?, ?)",
    source_conn.execute("SELECT id, next_run_time, job_state FROM apscheduler_jobs"),
)
```

Then run `PRAGMA quick_check`, compare every non-scheduler source table count to the business destination, compare APScheduler counts, close all handles, and atomically `os.replace()` temporary files into final destinations. Existing destinations must fail closed; source and backup must remain untouched.

- [x] **Step 4: Verify migration tests pass**

Run: `PYTHONPATH=.:backend .venv/bin/python -m pytest backend/tests/test_runtime_database_migration.py -q`

Expected: all pass.

### Task 3: Update deployment contract and documentation

**Files:**
- Modify: `.env.example`
- Modify: `scripts/install-congxicai-v7-launchd.sh`
- Modify: `README.md`
- Modify: `CHANGELOG.md`
- Test: `backend/tests/test_runtime_database_paths.py`

- [x] **Step 1: Add failing deployment-contract assertions**

Assert that the generated/installed service exports `CONGXI_STATE_DIR`, and that `.env.example` documents `CONGXI_DATABASE_PATH` plus `CONGXI_SCHEDULER_DATABASE_PATH` without real credentials.

- [x] **Step 2: Verify the assertions fail**

Run: `PYTHONPATH=.:backend .venv/bin/python -m pytest backend/tests/test_runtime_database_paths.py -q`

Expected: missing runtime-state configuration assertions.

- [x] **Step 3: Implement and document the contract**

Use `~/Library/Application Support/congxicai-v7` as the default local state root. Document that the external project copy is retained for rollback and that real portfolio JSON remains the account truth source.

- [x] **Step 4: Verify targeted tests and shell/plist syntax**

Run:

```bash
PYTHONPATH=.:backend .venv/bin/python -m pytest backend/tests/test_runtime_database_paths.py -q
bash -n scripts/install-congxicai-v7-launchd.sh scripts/guardian.sh
plutil -lint scripts/com.zhuchenyuan.congxicai-v7.plist
```

Expected: all pass.

### Task 4: Ship, migrate, and cut over the live service

**Files:**
- Runtime source: `backend/data/stock_data.db`
- Runtime destinations: `~/Library/Application Support/congxicai-v7/stock_data.db`, `~/Library/Application Support/congxicai-v7/scheduler_jobs.db`
- Runtime backups: `~/Library/Application Support/congxicai-v7/backups/`

- [x] **Step 1: Run complete pre-cutover verification**

Run: `PYTHONPATH=.:backend .venv/bin/python -m pytest backend/tests -q && .venv/bin/python -m ruff check backend scripts`

Expected: zero failures.

- [ ] **Step 2: Review, commit, and merge before touching live state**

Commit code/docs only, create a ready PR, wait for CI, and merge. Never commit SQLite files, WAL/SHM files, credentials, or local backup manifests. The existing launchd process continues using the legacy database until the verified code is merged and the migration window starts.

- [ ] **Step 3: Stop launchd and prove the process released SQLite**

Run: `launchctl bootout gui/$(id -u)/com.zhuchenyuan.congxicai-v7`

Verify: `lsof backend/data/stock_data.db*` returns no service process.

- [ ] **Step 4: Execute migration CLI**

Run:

```bash
PYTHONPATH=.:backend .venv/bin/python scripts/migrate_runtime_databases.py \
  --source backend/data/stock_data.db \
  --business "$HOME/Library/Application Support/congxicai-v7/stock_data.db" \
  --scheduler "$HOME/Library/Application Support/congxicai-v7/scheduler_jobs.db"
```

Expected: source `quick_check=ok`; business counts match all non-scheduler tables; scheduler contains 10 jobs; rollback backup path printed.

- [ ] **Step 5: Reinstall/restart service and verify cutover**

Run: `bash scripts/install-congxicai-v7-launchd.sh`, then verify `launchctl print`, `/health`, and `lsof` show both local database files under `~/Library/Application Support/congxicai-v7` and no open handle to the legacy external database.

- [ ] **Step 6: Verify post-cutover data parity**

Run `PRAGMA quick_check`, compare table counts against the pre-cutover manifest, confirm 10 scheduled jobs, and verify the latest report/delivery status remains unchanged. Any mismatch triggers immediate stop and rollback to the preserved source path.

Keep the legacy DB unchanged until at least one successful scheduled cycle.

---

## Self-Review

- Spec coverage: local business DB, isolated scheduler DB, repeatable migration, rollback, launchd cutover, parity verification, docs, tests.
- Placeholder scan: no TBD/TODO placeholders.
- Type consistency: `RuntimeDatabasePaths.business` and `.scheduler` are used consistently by configuration, application engine, scheduler, migration CLI, and tests.
- Execution mode: implementation ran inline; ship audits may use isolated workers when the active ship workflow explicitly requires them.
