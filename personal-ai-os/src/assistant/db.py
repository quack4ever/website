"""The database: the assistant's notebook and filing cabinet.

WHAT IS SQLITE?
---------------
A database that is just one file on your disk.  There is no server to install
or start.  Your Mac already ships with it.  We use it because it is
transactional (a half-finished write can never corrupt your data), it is fast,
and it has a built-in search engine called FTS5.

WHAT IS "WAL"?
--------------
Write-Ahead Logging.  It lets the daemon write while the CLI reads, at the
same time, without either blocking the other.  Without it, running
`assistant status` while the daemon was busy could hang.

WHAT IS A MIGRATION?
--------------------
A numbered change to the shape of the database.  We record which migrations
have run in the ``meta`` table, so upgrading the app never loses your data and
never runs the same change twice.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from . import paths
from .version import SCHEMA_VERSION

_local = threading.local()


# --------------------------------------------------------------------------
# Migrations.  Append new ones; never edit an old one.
# --------------------------------------------------------------------------
MIGRATIONS: List[str] = [
    # -- 1 -----------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS meta (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );

    -- Folders you have explicitly authorised.  Empty by default: a fresh
    -- install can read nothing at all until you grant a scope.
    CREATE TABLE IF NOT EXISTS scopes (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        path       TEXT NOT NULL UNIQUE,
        mode       TEXT NOT NULL CHECK (mode IN ('read','readwrite')),
        note       TEXT,
        created_at TEXT NOT NULL
    );

    -- Your written policy: "you may X but never Y".
    CREATE TABLE IF NOT EXISTS rules (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        effect     TEXT NOT NULL CHECK (effect IN ('allow','deny','ask')),
        capability TEXT NOT NULL,
        path_glob  TEXT,
        constraints TEXT,
        note       TEXT,
        enabled    INTEGER NOT NULL DEFAULT 1,
        priority   INTEGER NOT NULL DEFAULT 100,
        created_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_rules_cap ON rules(capability, enabled);

    -- Actions waiting for your yes/no.
    CREATE TABLE IF NOT EXISTS approvals (
        id         TEXT PRIMARY KEY,
        created_at TEXT NOT NULL,
        status     TEXT NOT NULL CHECK (status IN ('pending','approved','denied','expired')),
        capability TEXT NOT NULL,
        tool       TEXT NOT NULL,
        arguments  TEXT NOT NULL,
        risk       TEXT NOT NULL,
        summary    TEXT NOT NULL,
        resources  TEXT,
        session_id TEXT,
        expires_at TEXT,
        decided_at TEXT,
        decided_by TEXT,
        remember   INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX IF NOT EXISTS idx_approvals_status ON approvals(status, created_at);

    -- The CCTV recording.  Append-only by convention and by API.
    CREATE TABLE IF NOT EXISTS audit (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        ts          TEXT NOT NULL,
        actor       TEXT NOT NULL,
        event       TEXT NOT NULL,
        tool        TEXT,
        capability  TEXT,
        decision    TEXT,
        rule_id     INTEGER,
        arguments   TEXT,
        resources   TEXT,
        outcome     TEXT,
        error       TEXT,
        session_id  TEXT,
        duration_ms INTEGER
    );
    CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit(ts);
    CREATE INDEX IF NOT EXISTS idx_audit_event ON audit(event, ts);

    -- Long-term memory.
    CREATE TABLE IF NOT EXISTS memory (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        kind       TEXT NOT NULL,
        text       TEXT NOT NULL,
        scope      TEXT,
        confidence REAL NOT NULL DEFAULT 0.7,
        source     TEXT,
        pinned     INTEGER NOT NULL DEFAULT 0,
        use_count  INTEGER NOT NULL DEFAULT 0,
        last_used  TEXT,
        expires_at TEXT,
        deleted    INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX IF NOT EXISTS idx_memory_kind ON memory(kind, deleted);
    CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts
        USING fts5(mem_id UNINDEXED, text, tokenize='unicode61');

    -- The file catalogue.
    CREATE TABLE IF NOT EXISTS files (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        path         TEXT NOT NULL UNIQUE,
        name         TEXT NOT NULL,
        ext          TEXT,
        size         INTEGER NOT NULL,
        mtime        REAL NOT NULL,
        content_hash TEXT,
        kind         TEXT,
        indexed_at   TEXT NOT NULL,
        has_text     INTEGER NOT NULL DEFAULT 0,
        chars        INTEGER NOT NULL DEFAULT 0,
        error        TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_files_hash ON files(content_hash);
    CREATE INDEX IF NOT EXISTS idx_files_ext  ON files(ext);
    CREATE INDEX IF NOT EXISTS idx_files_name ON files(name);
    CREATE VIRTUAL TABLE IF NOT EXISTS files_fts
        USING fts5(file_id UNINDEXED, name, body, tokenize='unicode61');

    -- Plans produced by the planning engine.
    CREATE TABLE IF NOT EXISTS plans (
        id           TEXT PRIMARY KEY,
        created_at   TEXT NOT NULL,
        updated_at   TEXT NOT NULL,
        goal         TEXT NOT NULL,
        status       TEXT NOT NULL,
        chosen       TEXT,
        alternatives TEXT,
        rationale    TEXT,
        risk         REAL,
        session_id   TEXT
    );
    CREATE TABLE IF NOT EXISTS plan_steps (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        plan_id     TEXT NOT NULL,
        idx         INTEGER NOT NULL,
        title       TEXT NOT NULL,
        tool        TEXT,
        arguments   TEXT,
        capability  TEXT,
        status      TEXT NOT NULL DEFAULT 'pending',
        result      TEXT,
        error       TEXT,
        started_at  TEXT,
        finished_at TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_steps_plan ON plan_steps(plan_id, idx);

    -- Conversations.
    CREATE TABLE IF NOT EXISTS sessions (
        id         TEXT PRIMARY KEY,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        title      TEXT
    );
    CREATE TABLE IF NOT EXISTS messages (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        ts         TEXT NOT NULL,
        role       TEXT NOT NULL,
        content    TEXT NOT NULL,
        meta       TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, id);

    -- Scheduled autonomous work (only runs inside your rules).
    CREATE TABLE IF NOT EXISTS jobs (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        name        TEXT NOT NULL UNIQUE,
        every_seconds INTEGER NOT NULL,
        action      TEXT NOT NULL,
        enabled     INTEGER NOT NULL DEFAULT 1,
        last_run    TEXT,
        next_run    TEXT,
        last_status TEXT,
        last_error  TEXT,
        created_at  TEXT NOT NULL
    );

    -- What we spent, so cloud usage is never invisible.
    CREATE TABLE IF NOT EXISTS usage (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        ts            TEXT NOT NULL,
        provider      TEXT NOT NULL,
        model         TEXT,
        role          TEXT,
        input_tokens  INTEGER,
        output_tokens INTEGER,
        session_id    TEXT
    );

    -- Reflections: what the system learned after finishing work.
    CREATE TABLE IF NOT EXISTS reflections (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        ts         TEXT NOT NULL,
        plan_id    TEXT,
        session_id TEXT,
        worked     INTEGER,
        summary    TEXT NOT NULL,
        lessons    TEXT
    );
    """,
]


def connect(path: Optional[Path] = None, *, fresh: bool = False) -> sqlite3.Connection:
    """Open the database, creating and migrating it if needed.

    One connection is cached per thread (``threading.local``).  SQLite
    connections are not safe to share across threads, and the daemon uses
    several, so this keeps them separate automatically.
    """
    target = Path(path) if path else paths.db_path()
    key = str(target)
    if not fresh:
        cached = getattr(_local, "conns", {}).get(key)
        if cached is not None:
            return cached

    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(target), timeout=15.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=15000")
    migrate(conn)
    try:
        target.chmod(paths.FILE_MODE)
    except OSError:
        pass

    if not hasattr(_local, "conns"):
        _local.conns = {}
    _local.conns[key] = conn
    return conn


def close_all() -> None:
    """Close this thread's cached connections (used by tests and shutdown)."""
    for conn in getattr(_local, "conns", {}).values():
        try:
            conn.close()
        except Exception:
            pass
    _local.conns = {}


def _applied_version(conn: sqlite3.Connection) -> int:
    try:
        row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    except sqlite3.OperationalError:
        return 0
    return int(row["value"]) if row else 0


def migrate(conn: sqlite3.Connection) -> int:
    """Run any migrations that have not been applied yet.  Returns the version."""
    current = _applied_version(conn)
    for number, script in enumerate(MIGRATIONS, start=1):
        if number <= current:
            continue
        # NOTE: sqlite3.executescript() commits any open transaction *before*
        # it runs, so we cannot wrap it with conn.execute("BEGIN") from the
        # outside - the COMMIT would then have no transaction to close.  The
        # fix is to put BEGIN/COMMIT inside the script text itself, which
        # executescript runs verbatim.  The whole migration, including the
        # version bump, is therefore one atomic unit: it either fully applies
        # or leaves the database exactly as it was.
        bundled = (
            "BEGIN;\n"
            + script
            + "\nINSERT INTO meta(key,value) VALUES('schema_version','%d') "
              "ON CONFLICT(key) DO UPDATE SET value=excluded.value;\n" % number
            + "COMMIT;\n"
        )
        try:
            conn.executescript(bundled)
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.OperationalError:
                pass  # already rolled back by SQLite
            raise
        current = number
    return current


def schema_version(conn: Optional[sqlite3.Connection] = None) -> int:
    return _applied_version(conn or connect())


# --------------------------------------------------------------------------
# Small helpers so the rest of the code never writes raw boilerplate.
# --------------------------------------------------------------------------
def query(conn: sqlite3.Connection, sql: str, args: Sequence[Any] = ()) -> List[sqlite3.Row]:
    return list(conn.execute(sql, tuple(args)).fetchall())


def one(conn: sqlite3.Connection, sql: str, args: Sequence[Any] = ()) -> Optional[sqlite3.Row]:
    return conn.execute(sql, tuple(args)).fetchone()


def execute(conn: sqlite3.Connection, sql: str, args: Sequence[Any] = ()) -> sqlite3.Cursor:
    return conn.execute(sql, tuple(args))


def rows_to_dicts(rows: Iterable[sqlite3.Row]) -> List[Dict[str, Any]]:
    return [dict(r) for r in rows]


def json_dump(value: Any) -> str:
    """Store structured data in a TEXT column, safely."""
    return json.dumps(value, ensure_ascii=False, default=str)


def json_load(text: Optional[str], default: Any = None) -> Any:
    if not text:
        return default
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return default
