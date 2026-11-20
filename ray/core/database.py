"""SQLite persistence layer.

A single database file holds every persistent record: tasks, notes, reminders,
calendar events, memories, file index, automation workflows, audit log, AI
usage, cache and credentials (encrypted blobs only).

Design notes
------------
* WAL journalling so the UI, scheduler and API server can read concurrently.
* One connection per thread (the API server is threaded).
* Schema versioning through ``PRAGMA user_version`` with forward migrations.
* FTS5 virtual tables for file/content search.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable, Sequence

from ..core.logging_setup import get_logger

log = get_logger("core.database")

SCHEMA_VERSION = 5

SCHEMA_STATEMENTS: list[str] = [
    # ---------------------------------------------------------- productivity
    """CREATE TABLE IF NOT EXISTS tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        description TEXT DEFAULT '',
        priority TEXT DEFAULT 'normal',
        status TEXT DEFAULT 'open',
        category TEXT DEFAULT 'general',
        due_at TEXT,
        recurrence TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        completed_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS notes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL DEFAULT '',
        body TEXT NOT NULL DEFAULT '',
        category TEXT DEFAULT 'general',
        pinned INTEGER DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS reminders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        due_at TEXT NOT NULL,
        recurrence TEXT,
        enabled INTEGER DEFAULT 1,
        last_fired TEXT,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        description TEXT DEFAULT '',
        start_at TEXT NOT NULL,
        end_at TEXT,
        all_day INTEGER DEFAULT 0,
        location TEXT DEFAULT '',
        color TEXT DEFAULT 'cyan',
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS habits (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        cadence TEXT DEFAULT 'daily',
        target INTEGER DEFAULT 1,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS habit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        habit_id INTEGER NOT NULL,
        day TEXT NOT NULL,
        count INTEGER DEFAULT 1,
        UNIQUE(habit_id, day)
    )""",
    """CREATE TABLE IF NOT EXISTS pomodoro (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        started_at TEXT NOT NULL,
        finished_at TEXT,
        minutes INTEGER NOT NULL,
        label TEXT DEFAULT '',
        completed INTEGER DEFAULT 0
    )""",
    # ---------------------------------------------------------------- memory
    """CREATE TABLE IF NOT EXISTS memories (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        category TEXT DEFAULT 'general',
        title TEXT NOT NULL,
        content TEXT NOT NULL,
        sensitivity TEXT DEFAULT 'normal',
        source TEXT DEFAULT 'user',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        last_used TEXT,
        use_count INTEGER DEFAULT 0
    )""",
    # ------------------------------------------------------------ knowledge
    """CREATE TABLE IF NOT EXISTS files (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        path TEXT NOT NULL UNIQUE,
        name TEXT NOT NULL,
        ext TEXT DEFAULT '',
        size INTEGER DEFAULT 0,
        mtime REAL DEFAULT 0,
        sha256 TEXT,
        kind TEXT DEFAULT 'other',
        content_chars INTEGER DEFAULT 0,
        preview TEXT DEFAULT '',
        indexed_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_files_name ON files(name)",
    "CREATE INDEX IF NOT EXISTS idx_files_ext ON files(ext)",
    "CREATE INDEX IF NOT EXISTS idx_files_mtime ON files(mtime)",
    """CREATE VIRTUAL TABLE IF NOT EXISTS files_fts USING fts5(
        name, path, content, tokenize='unicode61'
    )""",
    """CREATE TABLE IF NOT EXISTS index_roots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        path TEXT NOT NULL UNIQUE,
        enabled INTEGER DEFAULT 1,
        last_indexed TEXT,
        file_count INTEGER DEFAULT 0
    )""",
    """CREATE TABLE IF NOT EXISTS knowledge_docs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        path TEXT,
        summary TEXT DEFAULT '',
        tags TEXT DEFAULT '',
        added_at TEXT NOT NULL
    )""",
    # -------------------------------------------------------------- browser
    """CREATE TABLE IF NOT EXISTS bookmarks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        url TEXT NOT NULL UNIQUE,
        tags TEXT DEFAULT '',
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS browser_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT DEFAULT '',
        url TEXT NOT NULL,
        kind TEXT DEFAULT 'page',
        ts TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS research_reports (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        question TEXT NOT NULL,
        report TEXT NOT NULL,
        sources TEXT DEFAULT '[]',
        created_at TEXT NOT NULL
    )""",
    # ----------------------------------------------------------- automation
    """CREATE TABLE IF NOT EXISTS workflows (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        description TEXT DEFAULT '',
        enabled INTEGER DEFAULT 1,
        trigger_json TEXT NOT NULL,
        conditions_json TEXT DEFAULT '[]',
        actions_json TEXT NOT NULL,
        permission_level INTEGER DEFAULT 1,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        last_run TEXT,
        run_count INTEGER DEFAULT 0
    )""",
    """CREATE TABLE IF NOT EXISTS workflow_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        workflow_id INTEGER NOT NULL,
        started_at TEXT NOT NULL,
        finished_at TEXT,
        status TEXT DEFAULT 'running',
        trigger TEXT DEFAULT 'manual',
        detail TEXT DEFAULT ''
    )""",
    # --------------------------------------------------------------- voice
    """CREATE TABLE IF NOT EXISTS voice_sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        started_at TEXT NOT NULL,
        ended_at TEXT NOT NULL,
        seconds REAL DEFAULT 0,
        agent_id TEXT DEFAULT '',
        note TEXT DEFAULT ''
    )""",
    "CREATE INDEX IF NOT EXISTS idx_voice_sessions_start ON voice_sessions(started_at)",
    # ----------------------------------------------------------- security
    """CREATE TABLE IF NOT EXISTS audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL,
        actor TEXT DEFAULT 'ray',
        action TEXT NOT NULL,
        tool TEXT DEFAULT '',
        target TEXT DEFAULT '',
        permission_level INTEGER DEFAULT 1,
        result TEXT DEFAULT 'ok',
        detail TEXT DEFAULT ''
    )""",
    "CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts)",
    """CREATE TABLE IF NOT EXISTS approvals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL,
        tool TEXT NOT NULL,
        args_json TEXT DEFAULT '{}',
        level INTEGER NOT NULL,
        reason TEXT DEFAULT '',
        status TEXT DEFAULT 'pending',
        decided_at TEXT,
        decided_by TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS credentials (
        name TEXT PRIMARY KEY,
        ciphertext BLOB NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        last_tested TEXT,
        last_test_ok INTEGER,
        last_test_detail TEXT
    )""",
    # ------------------------------------------------------------------ ai
    """CREATE TABLE IF NOT EXISTS usage (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL,
        provider TEXT NOT NULL,
        model TEXT DEFAULT '',
        kind TEXT DEFAULT 'chat',
        input_tokens INTEGER DEFAULT 0,
        output_tokens INTEGER DEFAULT 0,
        characters INTEGER DEFAULT 0,
        ok INTEGER DEFAULT 1,
        error TEXT DEFAULT '',
        cached INTEGER DEFAULT 0,
        cost_usd REAL DEFAULT 0
    )""",
    "CREATE INDEX IF NOT EXISTS idx_usage_ts ON usage(ts)",
    """CREATE TABLE IF NOT EXISTS cache (
        key TEXT PRIMARY KEY,
        provider TEXT DEFAULT '',
        model TEXT DEFAULT '',
        response TEXT NOT NULL,
        created_at REAL NOT NULL,
        expires_at REAL NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS conversations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT DEFAULT 'New conversation',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        conversation_id INTEGER NOT NULL,
        role TEXT NOT NULL,
        content TEXT NOT NULL,
        created_at TEXT NOT NULL,
        meta TEXT DEFAULT '{}'
    )""",
    "CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id)",
    # ---------------------------------------------------------------- misc
    """CREATE TABLE IF NOT EXISTS app_usage (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        path TEXT DEFAULT '',
        last_launched TEXT NOT NULL,
        launch_count INTEGER DEFAULT 1,
        UNIQUE(name, path)
    )""",
    """CREATE TABLE IF NOT EXISTS notifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL,
        title TEXT NOT NULL,
        body TEXT DEFAULT '',
        kind TEXT DEFAULT 'info',
        read INTEGER DEFAULT 0
    )""",
    """CREATE TABLE IF NOT EXISTS agent_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        agent TEXT NOT NULL,
        goal TEXT NOT NULL,
        status TEXT DEFAULT 'running',
        started_at TEXT NOT NULL,
        finished_at TEXT,
        steps INTEGER DEFAULT 0,
        result TEXT DEFAULT ''
    )""",
]


def utcnow() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class Database:
    """Thread-safe SQLite wrapper."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock_down()
        self._local = threading.local()
        self._write_lock = threading.RLock()

    def _lock_down(self) -> None:
        """Make the data directory owner-only on POSIX.

        The database holds tasks, notes, memories and the audit trail.  On a
        shared machine another local account must not be able to read it, and
        SQLite creates the file with the process umask (often 0644).
        """
        if os.name == "nt":  # pragma: no cover - ACLs, not modes
            return
        for target, mode in ((self.path.parent, 0o700),):
            try:
                os.chmod(target, mode)
            except OSError:  # pragma: no cover - read-only or foreign mount
                pass
        for name in (self.path.name, f"{self.path.name}-wal", f"{self.path.name}-shm"):
            candidate = self.path.parent / name
            try:
                if candidate.exists():
                    os.chmod(candidate, 0o600)
            except OSError:  # pragma: no cover
                pass

    # ------------------------------------------------------------ connections
    def connect(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(str(self.path), timeout=15, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA busy_timeout=8000")
            self._lock_down()  # the file only exists after the first connect
            self._local.conn = conn
        return conn

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            try:
                conn.close()
            finally:
                self._local.conn = None

    # --------------------------------------------------------------- schema
    def migrate(self) -> int:
        conn = self.connect()
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version == 0:
            with self._write_lock, conn:
                for statement in SCHEMA_STATEMENTS:
                    conn.execute(statement)
                conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            log.info("database initialised at %s (schema v%s)", self.path, SCHEMA_VERSION)
            return SCHEMA_VERSION
        if version < SCHEMA_VERSION:
            self._upgrade(conn, version)
        return SCHEMA_VERSION

    def _upgrade(self, conn: sqlite3.Connection, from_version: int) -> None:
        log.info("migrating database from v%s to v%s", from_version, SCHEMA_VERSION)
        with self._write_lock, conn:
            # Re-running the CREATE IF NOT EXISTS set is safe and fills in any
            # table added by a newer schema.
            for statement in SCHEMA_STATEMENTS:
                conn.execute(statement)
            if from_version < 2:
                self._add_column(conn, "tasks", "recurrence", "TEXT")
            if from_version < 3:
                self._add_column(conn, "workflows", "permission_level", "INTEGER DEFAULT 1")
            if from_version < 4:
                # Real (or locally estimated) spend per call, so the credit caps
                # in ray/ai/policy.py are measured rather than guessed.
                self._add_column(conn, "usage", "cost_usd", "REAL DEFAULT 0")
            conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

    @staticmethod
    def _add_column(conn: sqlite3.Connection, table: str, column: str, decl: str) -> None:
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")

    # ---------------------------------------------------------------- queries
    def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        conn = self.connect()
        with self._write_lock, conn:
            cursor = conn.execute(sql, tuple(params))
            return cursor.lastrowid if cursor.lastrowid is not None else cursor.rowcount

    def executemany(self, sql: str, rows: Iterable[Sequence[Any]]) -> None:
        conn = self.connect()
        with self._write_lock, conn:
            conn.executemany(sql, rows)

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[dict]:
        conn = self.connect()
        return [dict(row) for row in conn.execute(sql, tuple(params)).fetchall()]

    def query_one(self, sql: str, params: Sequence[Any] = ()) -> dict | None:
        conn = self.connect()
        row = conn.execute(sql, tuple(params)).fetchone()
        return dict(row) if row else None

    def scalar(self, sql: str, params: Sequence[Any] = (), default: Any = None) -> Any:
        row = self.connect().execute(sql, tuple(params)).fetchone()
        return row[0] if row and row[0] is not None else default

    # --------------------------------------------------------------- helpers
    def insert(self, table: str, values: dict[str, Any]) -> int:
        columns = ", ".join(values)
        placeholders = ", ".join("?" for _ in values)
        return self.execute(
            f"INSERT INTO {table} ({columns}) VALUES ({placeholders})", tuple(values.values())
        )

    def update(self, table: str, row_id: int, values: dict[str, Any]) -> int:
        if not values:
            return 0
        assignments = ", ".join(f"{key} = ?" for key in values)
        return self.execute(
            f"UPDATE {table} SET {assignments} WHERE id = ?", (*values.values(), row_id)
        )

    def delete(self, table: str, row_id: int) -> int:
        return self.execute(f"DELETE FROM {table} WHERE id = ?", (row_id,))

    def table_counts(self) -> dict[str, int]:
        tables = [
            "tasks", "notes", "reminders", "events", "memories", "files",
            "workflows", "workflow_runs", "audit_log", "approvals", "usage",
            "conversations", "bookmarks", "notifications", "agent_runs",
        ]
        counts = {}
        for table in tables:
            try:
                counts[table] = int(self.scalar(f"SELECT COUNT(*) FROM {table}", default=0))
            except sqlite3.Error:
                counts[table] = 0
        return counts

    def integrity_check(self) -> tuple[bool, str]:
        row = self.connect().execute("PRAGMA integrity_check").fetchone()
        result = row[0] if row else "unknown"
        return (result == "ok", result)

    def backup(self, destination: Path | str) -> Path:
        """Consistent online backup using SQLite's backup API."""
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        target = sqlite3.connect(str(destination))
        try:
            with self._write_lock:
                self.connect().backup(target)
            target.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            target.close()
        log.info("database backed up to %s", destination)
        return destination

    def size_bytes(self) -> int:
        try:
            total = self.path.stat().st_size
            wal = self.path.with_suffix(self.path.suffix + "-wal")
            if wal.exists():
                total += wal.stat().st_size
            return total
        except OSError:
            return 0


def copy_database_file(db: Database, destination: Path) -> Path:
    """Cold copy fallback used by the backup script."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    db.close()
    shutil.copy2(db.path, destination)
    return destination
