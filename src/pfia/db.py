from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


SCHEMA = """
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    latest_job_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    degraded_mode INTEGER NOT NULL DEFAULT 0,
    failure_code TEXT,
    config_snapshot_json TEXT NOT NULL,
    report_path TEXT,
    executive_summary TEXT
);

CREATE TABLE IF NOT EXISTS jobs (
    job_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    status TEXT NOT NULL,
    stage TEXT NOT NULL,
    attempt INTEGER NOT NULL DEFAULT 1,
    failure_code TEXT,
    degraded_mode INTEGER NOT NULL DEFAULT 0,
    message TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(session_id) REFERENCES sessions(session_id)
);

CREATE TABLE IF NOT EXISTS job_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    stage TEXT NOT NULL,
    event TEXT NOT NULL,
    level TEXT NOT NULL,
    message TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS preprocessing_summaries (
    session_id TEXT PRIMARY KEY,
    payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS session_runtime_metadata (
    session_id TEXT PRIMARY KEY,
    payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    review_id TEXT NOT NULL,
    source TEXT NOT NULL,
    created_at TEXT NOT NULL,
    rating INTEGER,
    language TEXT NOT NULL,
    app_version TEXT,
    text_normalized TEXT NOT NULL,
    text_anonymized TEXT NOT NULL,
    dedupe_hash TEXT NOT NULL,
    flags_json TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    sentiment_score REAL DEFAULT 0,
    cluster_id TEXT,
    UNIQUE(session_id, review_id)
);

CREATE TABLE IF NOT EXISTS clusters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    cluster_id TEXT NOT NULL,
    label TEXT NOT NULL,
    summary TEXT NOT NULL,
    review_ids_json TEXT NOT NULL,
    top_quote_ids_json TEXT NOT NULL,
    priority_score REAL NOT NULL,
    sentiment_score REAL NOT NULL,
    trend_delta REAL NOT NULL,
    confidence TEXT NOT NULL,
    degraded_reason TEXT,
    keywords_json TEXT NOT NULL,
    sources_json TEXT NOT NULL,
    size INTEGER NOT NULL,
    anomaly_flag INTEGER NOT NULL DEFAULT 0,
    UNIQUE(session_id, cluster_id)
);

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    alert_id TEXT NOT NULL,
    cluster_id TEXT NOT NULL,
    type TEXT NOT NULL,
    severity TEXT NOT NULL,
    reason TEXT NOT NULL,
    spike_ratio REAL,
    insufficient_history INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    UNIQUE(session_id, alert_id)
);

CREATE TABLE IF NOT EXISTS chat_turns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS system_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_reviews_session ON reviews(session_id);
CREATE INDEX IF NOT EXISTS idx_clusters_session ON clusters(session_id);
CREATE INDEX IF NOT EXISTS idx_alerts_session ON alerts(session_id);
"""


def utcnow() -> datetime:
    """Return the current UTC timestamp."""
    return datetime.now(timezone.utc)


class Database:
    """Thin SQLite access layer used by the repository."""

    def __init__(self, db_path: Path):
        """Initialize the database file and ensure the schema exists.

        Args:
            db_path: Filesystem path to the SQLite database file.
        """
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        """Open a configured SQLite connection.

        Returns:
            SQLite connection with row factory enabled.
        """
        connection = sqlite3.connect(self.db_path, timeout=30, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        """Apply the embedded schema to the database."""
        with self._connect() as connection:
            connection.executescript(SCHEMA)
            connection.commit()

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        """Yield a transaction-scoped SQLite connection.

        Yields:
            An open SQLite connection that is committed and closed automatically.
        """
        connection = self._connect()
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def execute(self, query: str, params: tuple[Any, ...] = ()) -> None:
        """Execute a write-oriented SQL statement.

        Args:
            query: SQL text to execute.
            params: Positional parameters bound to the statement.
        """
        with self.connection() as connection:
            connection.execute(query, params)

    def fetchone(self, query: str, params: tuple[Any, ...] = ()) -> sqlite3.Row | None:
        """Fetch a single SQLite row.

        Args:
            query: SQL text to execute.
            params: Positional parameters bound to the statement.

        Returns:
            The first matching row, if any.
        """
        with self.connection() as connection:
            return connection.execute(query, params).fetchone()

    def fetchall(self, query: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        """Fetch all rows for a query.

        Args:
            query: SQL text to execute.
            params: Positional parameters bound to the statement.

        Returns:
            List of matching rows.
        """
        with self.connection() as connection:
            return list(connection.execute(query, params).fetchall())

    def upsert_system_state(self, key: str, value: dict[str, Any] | str) -> None:
        """Insert or replace a key-value entry in the system state table.

        Args:
            key: Stable system-state key.
            value: String or JSON-serializable payload.
        """
        if not isinstance(value, str):
            value = json.dumps(value, ensure_ascii=False)
        timestamp = utcnow().isoformat()
        self.execute(
            """
            INSERT INTO system_state (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = excluded.updated_at
            """,
            (key, value, timestamp),
        )

    def get_system_state(self, key: str) -> sqlite3.Row | None:
        """Fetch a system-state row by key.

        Args:
            key: Stable system-state key.

        Returns:
            Matching row or ``None`` when no value was written yet.
        """
        return self.fetchone("SELECT * FROM system_state WHERE key = ?", (key,))
