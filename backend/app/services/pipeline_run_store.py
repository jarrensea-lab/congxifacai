"""Durable, idempotent stage ledger for the v9 opportunity pipeline."""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

from app.version import PIPELINE_VERSION


TERMINAL_STAGE_STATUSES = frozenset({"succeeded", "degraded"})
VALID_STAGE_STATUSES = frozenset(
    {"pending", "running", "succeeded", "degraded", "failed"}
)


class StaleStageAttempt(RuntimeError):
    """Raised when an older attempt tries to overwrite the current stage."""


def _now() -> str:
    return datetime.now().astimezone().isoformat()


class PipelineRunStore:
    """Persist pipeline runs and compare-and-set stage transitions in SQLite."""

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=10000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS pipeline_runs (
                    run_id TEXT PRIMARY KEY,
                    trade_date TEXT NOT NULL,
                    pipeline_version TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(trade_date, pipeline_version)
                );

                CREATE TABLE IF NOT EXISTS pipeline_stages (
                    run_id TEXT NOT NULL,
                    stage_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempt INTEGER NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT NOT NULL DEFAULT '',
                    input_count INTEGER NOT NULL DEFAULT 0,
                    output_count INTEGER NOT NULL DEFAULT 0,
                    data_cutoff_at TEXT NOT NULL DEFAULT '',
                    artifact_path TEXT NOT NULL DEFAULT '',
                    artifact_digest TEXT NOT NULL DEFAULT '',
                    error_code TEXT NOT NULL DEFAULT '',
                    recovery_action TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY(run_id, stage_name),
                    FOREIGN KEY(run_id) REFERENCES pipeline_runs(run_id)
                );
                """
            )

    @staticmethod
    def _as_dict(row: sqlite3.Row | None) -> dict[str, Any]:
        return dict(row) if row is not None else {}

    def begin_run(self, trade_date: str) -> dict[str, Any]:
        run_id = f"{PIPELINE_VERSION}:{trade_date}"
        now = _now()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT OR IGNORE INTO pipeline_runs
                    (run_id, trade_date, pipeline_version, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (run_id, trade_date, PIPELINE_VERSION, now, now),
            )
            row = connection.execute(
                """
                SELECT * FROM pipeline_runs
                WHERE trade_date = ? AND pipeline_version = ?
                """,
                (trade_date, PIPELINE_VERSION),
            ).fetchone()
            connection.commit()
            return self._as_dict(row)
        finally:
            connection.close()

    def start_stage(
        self,
        run_id: str,
        stage_name: str,
        *,
        input_count: int = 0,
    ) -> dict[str, Any]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT * FROM pipeline_stages
                WHERE run_id = ? AND stage_name = ?
                """,
                (run_id, stage_name),
            ).fetchone()
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO pipeline_stages
                        (run_id, stage_name, status, attempt, started_at, input_count)
                    VALUES (?, ?, 'running', 1, ?, ?)
                    """,
                    (run_id, stage_name, _now(), max(0, int(input_count))),
                )
            row = connection.execute(
                """
                SELECT * FROM pipeline_stages
                WHERE run_id = ? AND stage_name = ?
                """,
                (run_id, stage_name),
            ).fetchone()
            connection.commit()
            return self._as_dict(row)
        finally:
            connection.close()

    def resume_stage(self, run_id: str, stage_name: str) -> dict[str, Any]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT * FROM pipeline_stages
                WHERE run_id = ? AND stage_name = ?
                """,
                (run_id, stage_name),
            ).fetchone()
            if row is None:
                connection.rollback()
                return self.start_stage(run_id, stage_name)
            next_attempt = int(row["attempt"]) + 1
            connection.execute(
                """
                UPDATE pipeline_stages
                SET status = 'running', attempt = ?, started_at = ?, finished_at = '',
                    output_count = 0, data_cutoff_at = '', artifact_path = '',
                    artifact_digest = '', error_code = '', recovery_action = ''
                WHERE run_id = ? AND stage_name = ?
                """,
                (next_attempt, _now(), run_id, stage_name),
            )
            current = connection.execute(
                """
                SELECT * FROM pipeline_stages
                WHERE run_id = ? AND stage_name = ?
                """,
                (run_id, stage_name),
            ).fetchone()
            connection.commit()
            return self._as_dict(current)
        finally:
            connection.close()

    def finish_stage(
        self,
        run_id: str,
        stage_name: str,
        *,
        status: str,
        attempt: int | None = None,
        output_count: int = 0,
        data_cutoff_at: str = "",
        artifact_path: str = "",
        artifact_digest: str = "",
        error_code: str = "",
        recovery_action: str = "",
    ) -> dict[str, Any]:
        if status not in VALID_STAGE_STATUSES:
            raise ValueError(f"invalid_stage_status:{status}")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                """
                SELECT * FROM pipeline_stages
                WHERE run_id = ? AND stage_name = ?
                """,
                (run_id, stage_name),
            ).fetchone()
            if current is None:
                raise KeyError(f"unknown_stage:{run_id}:{stage_name}")
            expected_attempt = int(attempt or current["attempt"])
            cursor = connection.execute(
                """
                UPDATE pipeline_stages
                SET status = ?, finished_at = ?, output_count = ?,
                    data_cutoff_at = ?, artifact_path = ?, artifact_digest = ?,
                    error_code = ?, recovery_action = ?
                WHERE run_id = ? AND stage_name = ? AND attempt = ?
                """,
                (
                    status,
                    _now(),
                    max(0, int(output_count)),
                    data_cutoff_at,
                    artifact_path,
                    artifact_digest,
                    error_code,
                    recovery_action,
                    run_id,
                    stage_name,
                    expected_attempt,
                ),
            )
            if cursor.rowcount != 1:
                latest = connection.execute(
                    """
                    SELECT * FROM pipeline_stages
                    WHERE run_id = ? AND stage_name = ?
                    """,
                    (run_id, stage_name),
                ).fetchone()
                connection.rollback()
                if latest is not None and latest["status"] in TERMINAL_STAGE_STATUSES:
                    return self._as_dict(latest)
                raise StaleStageAttempt(
                    f"stale_stage_attempt:{run_id}:{stage_name}:{expected_attempt}"
                )
            connection.execute(
                "UPDATE pipeline_runs SET updated_at = ? WHERE run_id = ?",
                (_now(), run_id),
            )
            row = connection.execute(
                """
                SELECT * FROM pipeline_stages
                WHERE run_id = ? AND stage_name = ?
                """,
                (run_id, stage_name),
            ).fetchone()
            connection.commit()
            return self._as_dict(row)
        finally:
            connection.close()

    def get_stage(self, run_id: str, stage_name: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM pipeline_stages
                WHERE run_id = ? AND stage_name = ?
                """,
                (run_id, stage_name),
            ).fetchone()
        if row is None:
            raise KeyError(f"unknown_stage:{run_id}:{stage_name}")
        return self._as_dict(row)

    def stages_for_run(self, run_id: str) -> dict[str, dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM pipeline_stages
                WHERE run_id = ?
                ORDER BY rowid
                """,
                (run_id,),
            ).fetchall()
        return {str(row["stage_name"]): self._as_dict(row) for row in rows}

    def incomplete_stages(
        self,
        run_id: str,
        expected: Sequence[str],
    ) -> list[str]:
        stages = self.stages_for_run(run_id)
        return [
            stage_name
            for stage_name in expected
            if stages.get(stage_name, {}).get("status") not in TERMINAL_STAGE_STATUSES
        ]

    def run_id_for_trade_date(self, trade_date: str) -> str:
        """Return the existing id without creating a due run."""
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT run_id FROM pipeline_runs
                WHERE trade_date = ? AND pipeline_version = ?
                """,
                (trade_date, PIPELINE_VERSION),
            ).fetchone()
        return str(row["run_id"]) if row is not None else ""
