"""SQLite persistence layer with explicit transactions and indexes.

The application intentionally uses the standard-library sqlite3 driver. Each
thread reuses its own connection (never shared across threads), which keeps the
layer thread-safe for FastAPI's threadpool and the background analysis worker
while WAL mode provides concurrent readers.
"""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator

from app.utils import ACTIVE_STATUS_SQL

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    source_type TEXT NOT NULL,
    device_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    occurred_epoch INTEGER NOT NULL,
    received_at TEXT NOT NULL,
    received_epoch INTEGER NOT NULL,
    latitude REAL,
    longitude REAL,
    zone TEXT,
    severity TEXT NOT NULL,
    confidence REAL NOT NULL,
    payload TEXT NOT NULL,
    raw_record TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    validation_status TEXT NOT NULL DEFAULT 'valid',
    duplicate_of TEXT,
    ingested_at TEXT NOT NULL,
    batch_id TEXT
);

CREATE TABLE IF NOT EXISTS rejected_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id TEXT,
    source_event_id TEXT,
    raw_record TEXT NOT NULL,
    errors TEXT NOT NULL,
    reason TEXT NOT NULL,
    received_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS quality_issues (
    issue_id TEXT PRIMARY KEY,
    issue_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    explanation TEXT NOT NULL,
    event_id TEXT,
    device_id TEXT,
    detected_at TEXT NOT NULL,
    suggested_action TEXT NOT NULL,
    details TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'open'
);

CREATE TABLE IF NOT EXISTS incidents (
    incident_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    category TEXT NOT NULL,
    severity TEXT NOT NULL,
    confidence REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    created_epoch INTEGER NOT NULL,
    updated_epoch INTEGER NOT NULL,
    event_ids TEXT NOT NULL,
    devices TEXT NOT NULL,
    zones TEXT NOT NULL,
    sources TEXT NOT NULL,
    explanation TEXT NOT NULL,
    missing_info TEXT NOT NULL,
    contradictions TEXT NOT NULL,
    recommended_actions TEXT NOT NULL,
    event_count INTEGER NOT NULL DEFAULT 0,
    is_possible_association INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS incident_events (
    incident_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    occurred_epoch INTEGER NOT NULL,
    sequence INTEGER NOT NULL,
    PRIMARY KEY (incident_id, event_id),
    FOREIGN KEY (incident_id) REFERENCES incidents(incident_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS incident_status (
    incident_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ingestion_runs (
    batch_id TEXT PRIMARY KEY,
    initiated_at TEXT NOT NULL,
    completed_at TEXT,
    total INTEGER NOT NULL DEFAULT 0,
    accepted INTEGER NOT NULL DEFAULT 0,
    rejected INTEGER NOT NULL DEFAULT 0,
    duplicates INTEGER NOT NULL DEFAULT 0,
    run_quality INTEGER NOT NULL DEFAULT 0,
    run_correlation INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'running',
    error TEXT
);

CREATE TABLE IF NOT EXISTS assistant_history (
    history_id TEXT PRIMARY KEY,
    query TEXT NOT NULL,
    response TEXT NOT NULL,
    pending_action TEXT,
    confirmed_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
    audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
    action TEXT NOT NULL,
    actor TEXT NOT NULL,
    target TEXT,
    details TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_occurred ON events(occurred_epoch);
CREATE INDEX IF NOT EXISTS idx_events_received ON events(received_epoch);
CREATE INDEX IF NOT EXISTS idx_events_source ON events(source_type);
CREATE INDEX IF NOT EXISTS idx_events_device ON events(device_id);
CREATE INDEX IF NOT EXISTS idx_events_zone_time ON events(zone, occurred_epoch);
CREATE INDEX IF NOT EXISTS idx_events_severity ON events(severity);
CREATE INDEX IF NOT EXISTS idx_events_content_hash ON events(content_hash);
CREATE INDEX IF NOT EXISTS idx_events_status ON events(validation_status);
CREATE INDEX IF NOT EXISTS idx_rejected_batch ON rejected_events(batch_id);
CREATE INDEX IF NOT EXISTS idx_rejected_source_event ON rejected_events(source_event_id);
CREATE INDEX IF NOT EXISTS idx_quality_type ON quality_issues(issue_type);
CREATE INDEX IF NOT EXISTS idx_quality_severity ON quality_issues(severity);
CREATE INDEX IF NOT EXISTS idx_quality_event ON quality_issues(event_id);
CREATE INDEX IF NOT EXISTS idx_quality_device ON quality_issues(device_id);
CREATE INDEX IF NOT EXISTS idx_incidents_severity ON incidents(severity);
CREATE INDEX IF NOT EXISTS idx_incidents_status ON incidents(status);
CREATE INDEX IF NOT EXISTS idx_incidents_created ON incidents(created_epoch);
CREATE INDEX IF NOT EXISTS idx_incidents_category ON incidents(category);
CREATE INDEX IF NOT EXISTS idx_incident_events_event ON incident_events(event_id);
CREATE INDEX IF NOT EXISTS idx_ingestion_runs_time ON ingestion_runs(initiated_at);
"""

TABLES = (
    "incident_events",
    "incident_status",
    "incidents",
    "quality_issues",
    "rejected_events",
    "events",
    "ingestion_runs",
    "assistant_history",
    "audit_log",
)


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        # Connections are reused per thread. Opening/closing a connection per
        # query (and checkpointing/deleting the WAL when the last one closed)
        # made a single-record POST /ingest cost ~50-120 ms on Windows.
        self._local = threading.local()
        self._pool: list[sqlite3.Connection] = []
        self._pool_lock = threading.Lock()
        self._generation = 0

    # ------------------------------------------------------------------ core
    def connect(self) -> sqlite3.Connection:
        """Open a new, caller-owned connection."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(self.path), timeout=45, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 45000")
        # synchronous is per-connection; NORMAL is durable-on-checkpoint in WAL
        # mode and avoids an fsync on every commit.
        connection.execute("PRAGMA synchronous = NORMAL")
        return connection

    def _thread_connection(self) -> sqlite3.Connection:
        connection = getattr(self._local, "connection", None)
        if connection is None or getattr(self._local, "generation", -1) != self._generation:
            connection = self.connect()
            with self._pool_lock:
                self._pool.append(connection)
            self._local.connection = connection
            self._local.generation = self._generation
        return connection

    def init_schema(self) -> None:
        connection = self.connect()
        try:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(SCHEMA)
            self._migrate(connection)
            connection.commit()
        finally:
            connection.close()

    def close(self) -> None:
        """Close pooled connections (lets SQLite checkpoint the WAL)."""
        with self._pool_lock:
            pool, self._pool = self._pool, []
            self._generation += 1
        for connection in pool:
            try:
                connection.close()
            except sqlite3.Error:  # pragma: no cover - best effort
                pass

    # Columns added after the first release; ALTER keeps existing DBs usable.
    _ADDED_COLUMNS = (
        ("incident_status", "assigned_to", "TEXT"),
        ("assistant_history", "session_id", "TEXT"),
        ("assistant_history", "decision", "TEXT"),
    )

    def _migrate(self, connection: sqlite3.Connection) -> None:
        for table, column, column_type in self._ADDED_COLUMNS:
            existing = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
            if column not in existing:
                connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_assistant_history_session "
            "ON assistant_history(session_id, created_at)"
        )

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self._thread_connection()
        if connection.in_transaction:
            # Nested use on the same thread: keep the old isolated behaviour.
            connection = self.connect()
            owned = True
        else:
            owned = False
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            if owned:
                connection.close()

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        connection = self._thread_connection()
        nested = connection.in_transaction
        try:
            yield connection
        finally:
            # Never leave a read snapshot open on a pooled connection.
            if not nested and connection.in_transaction:
                connection.rollback()

    def fetchall(self, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
        with self.read() as connection:
            return [dict(row) for row in connection.execute(sql, tuple(params)).fetchall()]

    def fetchone(self, sql: str, params: Iterable[Any] = ()) -> dict[str, Any] | None:
        with self.read() as connection:
            row = connection.execute(sql, tuple(params)).fetchone()
            return dict(row) if row else None

    def scalar(self, sql: str, params: Iterable[Any] = (), default: Any = None) -> Any:
        row = self.fetchone(sql, params)
        if not row:
            return default
        return next(iter(row.values()))

    def execute(self, sql: str, params: Iterable[Any] = ()) -> int:
        with self.transaction() as connection:
            cursor = connection.execute(sql, tuple(params))
            return cursor.rowcount

    def reset(self) -> None:
        """Remove all application data without dropping the schema."""
        with self.transaction() as connection:
            for table in TABLES:
                connection.execute(f"DELETE FROM {table}")
            connection.execute(
                "DELETE FROM sqlite_sequence WHERE name IN ('events','rejected_events')"
            )

    def stats(self) -> dict[str, Any]:
        with self.read() as connection:
            events = connection.execute(
                "SELECT COUNT(*) FROM events WHERE validation_status = 'valid'"
            ).fetchone()[0]
            duplicate_content = connection.execute(
                "SELECT COUNT(*) FROM events WHERE validation_status = 'duplicate'"
            ).fetchone()[0]
            rejected = connection.execute("SELECT COUNT(*) FROM rejected_events").fetchone()[0]
            incidents = connection.execute("SELECT COUNT(*) FROM incidents").fetchone()[0]
            open_incidents = connection.execute(
                f"SELECT COUNT(*) FROM incidents WHERE status IN ({ACTIVE_STATUS_SQL})"
            ).fetchone()[0]
            high_incidents = connection.execute(
                "SELECT COUNT(*) FROM incidents WHERE severity IN ('high','critical')"
            ).fetchone()[0]
            quality = connection.execute("SELECT COUNT(*) FROM quality_issues").fetchone()[0]
            unhealthy = connection.execute(
                "SELECT COUNT(DISTINCT device_id) FROM quality_issues "
                "WHERE issue_type IN ('unhealthy_device','offline_device') AND device_id IS NOT NULL"
            ).fetchone()[0]
            runs = connection.execute("SELECT COUNT(*) FROM ingestion_runs").fetchone()[0]
        return {
            "valid_events": events,
            "duplicate_content_events": duplicate_content,
            "rejected_events": rejected,
            "incidents": incidents,
            "open_incidents": open_incidents,
            "high_severity_incidents": high_incidents,
            "quality_issues": quality,
            "unhealthy_devices": unhealthy,
            "ingestion_runs": runs,
        }

    def health(self) -> dict[str, Any]:
        try:
            with self.read() as connection:
                connection.execute("SELECT 1").fetchone()
                integrity = connection.execute("PRAGMA quick_check").fetchone()[0]
            return {
                "database_connected": True,
                "database_integrity": integrity,
                # File name only: /health is unauthenticated and must not reveal local folder paths.
                "database_path": self.path.name,
                "database_size_bytes": self.path.stat().st_size if self.path.exists() else 0,
            }
        except Exception as exc:  # pragma: no cover - reported honestly at runtime
            return {
                "database_connected": False,
                "database_error": str(exc),
                "database_path": self.path.name,
            }
