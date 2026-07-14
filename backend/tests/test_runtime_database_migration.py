import sqlite3
from pathlib import Path

import pytest


def _create_source_database(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE positions (id INTEGER PRIMARY KEY, stock_code TEXT NOT NULL);
            CREATE TABLE ai_strategies (id INTEGER PRIMARY KEY, content TEXT NOT NULL);
            CREATE TABLE apscheduler_jobs (
                id VARCHAR(191) NOT NULL PRIMARY KEY,
                next_run_time FLOAT,
                job_state BLOB NOT NULL
            );
            CREATE INDEX ix_apscheduler_jobs_next_run_time
                ON apscheduler_jobs (next_run_time);
            INSERT INTO positions(stock_code) VALUES ('000725'), ('600839');
            INSERT INTO ai_strategies(content) VALUES ('audit-1');
            INSERT INTO apscheduler_jobs(id, next_run_time, job_state)
                VALUES ('premarket', 1.0, X'0102'), ('main_report', 2.0, X'0304');
            """
        )


def _tables(path: Path) -> set[str]:
    with sqlite3.connect(path) as conn:
        return {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }


def test_migration_splits_business_and_scheduler_tables(tmp_path):
    from scripts.migrate_runtime_databases import migrate_runtime_databases

    source = tmp_path / "legacy" / "stock_data.db"
    source.parent.mkdir()
    _create_source_database(source)
    business = tmp_path / "local" / "stock_data.db"
    scheduler = tmp_path / "local" / "scheduler_jobs.db"
    backup_dir = tmp_path / "local" / "backups"

    result = migrate_runtime_databases(
        source=source,
        business=business,
        scheduler=scheduler,
        backup_dir=backup_dir,
    )

    assert result["source_quick_check"] == "ok"
    assert result["business_quick_check"] == "ok"
    assert result["scheduler_quick_check"] == "ok"
    assert result["business_counts"] == {"ai_strategies": 1, "positions": 2}
    assert result["scheduler_jobs"] == 2
    assert _tables(business) == {"ai_strategies", "positions"}
    assert _tables(scheduler) == {"apscheduler_jobs"}
    assert Path(result["backup_path"]).exists()
    assert _tables(source) == {"ai_strategies", "apscheduler_jobs", "positions"}


def test_migration_refuses_to_overwrite_existing_destinations(tmp_path):
    from scripts.migrate_runtime_databases import migrate_runtime_databases

    source = tmp_path / "source.db"
    _create_source_database(source)
    business = tmp_path / "business.db"
    scheduler = tmp_path / "scheduler.db"
    business.write_bytes(b"occupied")

    with pytest.raises(FileExistsError, match="destination already exists"):
        migrate_runtime_databases(
            source=source,
            business=business,
            scheduler=scheduler,
            backup_dir=tmp_path / "backups",
        )

    assert business.read_bytes() == b"occupied"
    assert not scheduler.exists()


def test_migration_creates_empty_scheduler_database_when_legacy_table_is_absent(
    tmp_path,
):
    from scripts.migrate_runtime_databases import migrate_runtime_databases

    source = tmp_path / "source.db"
    _create_source_database(source)
    with sqlite3.connect(source) as conn:
        conn.execute("DROP TABLE apscheduler_jobs")

    business = tmp_path / "business.db"
    scheduler = tmp_path / "scheduler.db"
    result = migrate_runtime_databases(
        source=source,
        business=business,
        scheduler=scheduler,
        backup_dir=tmp_path / "backups",
    )

    assert result["scheduler_jobs"] == 0
    assert _tables(scheduler) == {"apscheduler_jobs"}
    assert _tables(business) == {"ai_strategies", "positions"}


def test_migration_reads_committed_rows_from_active_wal(tmp_path):
    from scripts.migrate_runtime_databases import migrate_runtime_databases

    source = tmp_path / "source.db"
    _create_source_database(source)
    writer = sqlite3.connect(source)
    try:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("INSERT INTO positions(stock_code) VALUES ('300002')")
        writer.commit()

        result = migrate_runtime_databases(
            source=source,
            business=tmp_path / "business.db",
            scheduler=tmp_path / "scheduler.db",
            backup_dir=tmp_path / "backups",
        )
    finally:
        writer.close()

    assert result["business_counts"]["positions"] == 3


def test_migration_rolls_back_published_database_when_second_publish_fails(
    tmp_path, monkeypatch
):
    import scripts.migrate_runtime_databases as migration

    source = tmp_path / "source.db"
    _create_source_database(source)
    business = tmp_path / "business.db"
    scheduler = tmp_path / "scheduler.db"
    real_replace = migration.os.replace

    def fail_scheduler_publish(source_path, destination_path):
        if Path(destination_path) == scheduler:
            raise OSError("simulated scheduler publish failure")
        return real_replace(source_path, destination_path)

    monkeypatch.setattr(migration.os, "replace", fail_scheduler_publish)

    with pytest.raises(OSError, match="simulated scheduler publish failure"):
        migration.migrate_runtime_databases(
            source=source,
            business=business,
            scheduler=scheduler,
            backup_dir=tmp_path / "backups",
        )

    assert not business.exists()
    assert not scheduler.exists()
    assert not list(tmp_path.glob(".*.tmp"))


def test_migration_refuses_shared_business_and_scheduler_destination(tmp_path):
    from scripts.migrate_runtime_databases import migrate_runtime_databases

    source = tmp_path / "legacy.db"
    _create_source_database(source)
    shared_destination = tmp_path / "runtime.db"

    with pytest.raises(ValueError, match="must use distinct paths"):
        migrate_runtime_databases(
            source=source,
            business=shared_destination,
            scheduler=shared_destination,
            backup_dir=tmp_path / "backups",
        )


@pytest.mark.parametrize("suffix", ["-wal", "-shm"])
def test_migration_refuses_stale_destination_sidecars(tmp_path, suffix):
    from scripts.migrate_runtime_databases import migrate_runtime_databases

    source = tmp_path / "legacy.db"
    _create_source_database(source)
    business = tmp_path / "business.db"
    Path(f"{business}{suffix}").write_bytes(b"stale")

    with pytest.raises(FileExistsError, match="destination artifact already exists"):
        migrate_runtime_databases(
            source=source,
            business=business,
            scheduler=tmp_path / "scheduler.db",
            backup_dir=tmp_path / "backups",
        )
