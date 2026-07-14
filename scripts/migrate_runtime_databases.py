#!/usr/bin/env python3
"""Split a legacy SQLite file into local business and APScheduler databases."""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from uuid import uuid4


SCHEDULER_TABLE = "apscheduler_jobs"


def _readonly_connection(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)


def _quick_check(connection: sqlite3.Connection, label: str) -> str:
    result = str(connection.execute("PRAGMA quick_check").fetchone()[0])
    if result != "ok":
        raise RuntimeError(f"{label} quick_check failed: {result}")
    return result


def _user_tables(connection: sqlite3.Connection) -> list[str]:
    return [
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]


def _table_count(connection: sqlite3.Connection, table: str) -> int:
    quoted = '"' + table.replace('"', '""') + '"'
    return int(connection.execute(f"SELECT count(*) FROM {quoted}").fetchone()[0])


def _business_counts(connection: sqlite3.Connection) -> dict[str, int]:
    return {
        table: _table_count(connection, table)
        for table in _user_tables(connection)
        if table != SCHEDULER_TABLE
    }


def _scheduler_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE apscheduler_jobs (
            id VARCHAR(191) NOT NULL,
            next_run_time FLOAT,
            job_state BLOB NOT NULL,
            PRIMARY KEY (id)
        );
        CREATE INDEX ix_apscheduler_jobs_next_run_time
            ON apscheduler_jobs (next_run_time);
        """
    )


def _temp_path(destination: Path) -> Path:
    return destination.parent / f".{destination.name}.{uuid4().hex}.tmp"


def _backup_path(backup_dir: Path, source: Path) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    candidate = backup_dir / f"{source.stem}-{timestamp}.db"
    if candidate.exists():
        candidate = backup_dir / f"{source.stem}-{timestamp}-{uuid4().hex[:8]}.db"
    return candidate


def migrate_runtime_databases(
    *,
    source: str | Path,
    business: str | Path,
    scheduler: str | Path,
    backup_dir: str | Path,
) -> dict:
    """Copy and validate a legacy DB without mutating or deleting the source."""
    source_path = Path(source).expanduser()
    business_path = Path(business).expanduser()
    scheduler_path = Path(scheduler).expanduser()
    backup_directory = Path(backup_dir).expanduser()

    if not source_path.is_file():
        raise FileNotFoundError(f"source database not found: {source_path}")
    if business_path.resolve() == scheduler_path.resolve():
        raise ValueError("business and scheduler databases must use distinct paths")
    for destination in (business_path, scheduler_path):
        if os.path.lexists(destination):
            raise FileExistsError(f"destination already exists: {destination}")
        for suffix in ("-wal", "-shm"):
            artifact = Path(f"{destination}{suffix}")
            if os.path.lexists(artifact):
                raise FileExistsError(
                    f"destination artifact already exists: {artifact}"
                )

    business_path.parent.mkdir(parents=True, exist_ok=True)
    scheduler_path.parent.mkdir(parents=True, exist_ok=True)
    backup_directory.mkdir(parents=True, exist_ok=True)

    business_temp = _temp_path(business_path)
    scheduler_temp = _temp_path(scheduler_path)
    backup_path = _backup_path(backup_directory, source_path)
    backup_temp = _temp_path(backup_path)
    temp_paths = (business_temp, scheduler_temp, backup_temp)

    try:
        with closing(_readonly_connection(source_path)) as source_conn:
            source_quick_check = _quick_check(source_conn, "source")
            source_counts = _business_counts(source_conn)
            source_tables = set(_user_tables(source_conn))
            scheduler_rows = (
                list(
                    source_conn.execute(
                        "SELECT id, next_run_time, job_state FROM apscheduler_jobs"
                    )
                )
                if SCHEDULER_TABLE in source_tables
                else []
            )

            with closing(sqlite3.connect(business_temp)) as business_conn:
                source_conn.backup(business_conn)
            with closing(sqlite3.connect(backup_temp)) as backup_conn:
                source_conn.backup(backup_conn)

        with closing(sqlite3.connect(business_temp)) as business_conn:
            business_conn.execute("DROP TABLE IF EXISTS apscheduler_jobs")
            business_conn.commit()
            business_quick_check = _quick_check(business_conn, "business")
            business_counts = _business_counts(business_conn)
        if business_counts != source_counts:
            raise RuntimeError(
                f"business row-count mismatch: source={source_counts} destination={business_counts}"
            )

        with closing(sqlite3.connect(scheduler_temp)) as scheduler_conn:
            _scheduler_schema(scheduler_conn)
            scheduler_conn.executemany(
                "INSERT INTO apscheduler_jobs(id, next_run_time, job_state) VALUES (?, ?, ?)",
                scheduler_rows,
            )
            scheduler_conn.commit()
            scheduler_quick_check = _quick_check(scheduler_conn, "scheduler")
            scheduler_jobs = _table_count(scheduler_conn, SCHEDULER_TABLE)
        if scheduler_jobs != len(scheduler_rows):
            raise RuntimeError(
                f"scheduler row-count mismatch: source={len(scheduler_rows)} destination={scheduler_jobs}"
            )

        with closing(sqlite3.connect(backup_temp)) as backup_conn:
            _quick_check(backup_conn, "backup")

        published_runtime_paths: list[Path] = []
        try:
            os.replace(backup_temp, backup_path)
            os.replace(business_temp, business_path)
            published_runtime_paths.append(business_path)
            os.replace(scheduler_temp, scheduler_path)
            published_runtime_paths.append(scheduler_path)
        except Exception as publish_error:
            rollback_errors: list[str] = []
            for published_path in reversed(published_runtime_paths):
                for artifact in (
                    published_path,
                    Path(f"{published_path}-wal"),
                    Path(f"{published_path}-shm"),
                ):
                    try:
                        artifact.unlink(missing_ok=True)
                    except OSError as rollback_error:
                        rollback_errors.append(f"{artifact}: {rollback_error}")
            if rollback_errors:
                raise RuntimeError(
                    "runtime database publish failed and rollback was incomplete: "
                    + "; ".join(rollback_errors)
                ) from publish_error
            raise
        return {
            "source": str(source_path),
            "business": str(business_path),
            "scheduler": str(scheduler_path),
            "backup_path": str(backup_path),
            "source_quick_check": source_quick_check,
            "business_quick_check": business_quick_check,
            "scheduler_quick_check": scheduler_quick_check,
            "business_counts": business_counts,
            "scheduler_jobs": scheduler_jobs,
        }
    finally:
        for temp_path in temp_paths:
            temp_path.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--business", required=True, type=Path)
    parser.add_argument("--scheduler", required=True, type=Path)
    parser.add_argument("--backup-dir", type=Path)
    args = parser.parse_args()
    backup_dir = args.backup_dir or args.business.parent / "backups"
    result = migrate_runtime_databases(
        source=args.source,
        business=args.business,
        scheduler=args.scheduler,
        backup_dir=backup_dir,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
