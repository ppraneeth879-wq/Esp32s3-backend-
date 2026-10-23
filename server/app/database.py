"""SQLite persistence for LifeLog."""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any

# The database lives on a mounted volume so memories and tasks survive
# container rebuilds. Override with LIFELOG_DB_PATH.
DB_PATH = Path(os.getenv("LIFELOG_DB_PATH", "lifelog.db"))


def get_conn() -> sqlite3.Connection:
    parent = DB_PATH.parent
    if str(parent) not in ("", "."):
        parent.mkdir(parents=True, exist_ok=True)
    # A busy timeout plus WAL means the background Google sync can write
    # without blocking a request that is reading. Without it, a write in
    # progress makes every reader wait, and because the readers run on the
    # event loop, the whole server stalls.
    conn = sqlite3.connect(DB_PATH, timeout=15.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=15000")
        conn.execute("PRAGMA synchronous=NORMAL")
    except sqlite3.Error:
        pass
    return conn


# Columns added after the initial schema. Applied to existing databases via
# ALTER TABLE so upgrades do not lose data.
_MEMORIES_MIGRATIONS = [
    ("is_important", "INTEGER DEFAULT 0"),
    ("importance", "REAL DEFAULT 0"),
    ("category", "TEXT DEFAULT 'memory'"),
    ("reason", "TEXT DEFAULT ''"),
    ("source", "TEXT DEFAULT 'llm'"),
    ("session_id", "TEXT DEFAULT ''"),
    ("content_hash", "TEXT DEFAULT ''"),
    # Google-sourced entries (calendar events, important mail).
    ("external_id", "TEXT DEFAULT ''"),
    ("starts_at", "REAL"),
    ("ends_at", "REAL"),
    ("location", "TEXT DEFAULT ''"),
    ("status", "TEXT DEFAULT ''"),
    ("link", "TEXT DEFAULT ''"),
]

_ACTIONS_MIGRATIONS = [
    ("due_date", "TEXT"),
    ("source", "TEXT DEFAULT 'llm'"),
    ("session_id", "TEXT DEFAULT ''"),
    ("created_at", "REAL"),
    ("completed_at", "REAL"),
]


def _apply_migrations(c: sqlite3.Cursor, table: str, migrations: list[tuple[str, str]]) -> None:
    """Add any missing columns to `table`."""
    existing = {row["name"] for row in c.execute(f"PRAGMA table_info({table})").fetchall()}
    for name, decl in migrations:
        if name not in existing:
            c.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


def init_db() -> None:
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
    CREATE TABLE IF NOT EXISTS google_credentials (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT,
        google_user_id TEXT,
        access_token TEXT,
        refresh_token TEXT,
        expires_at REAL,
        scopes TEXT
    )""")
    c.execute("""
    CREATE TABLE IF NOT EXISTS memories (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        vector_id TEXT,
        transcript TEXT,
        summary TEXT,
        sentiment TEXT,
        tags TEXT,
        topics TEXT,
        tasks TEXT,
        people TEXT,
        dates TEXT,
        action_items TEXT,
        created_at REAL
    )""")
    c.execute("""
    CREATE TABLE IF NOT EXISTS actions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        memory_id INTEGER,
        title TEXT,
        description TEXT,
        action_type TEXT,
        priority TEXT,
        status TEXT,
        confidence REAL
    )""")
    c.execute("""
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT
    )""")
    _apply_migrations(c, "memories", _MEMORIES_MIGRATIONS)
    _apply_migrations(c, "actions", _ACTIONS_MIGRATIONS)
    c.execute("CREATE INDEX IF NOT EXISTS idx_memories_category ON memories(category)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_memories_important ON memories(is_important)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_memories_starts_at ON memories(starts_at)")
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_memories_external_id ON memories(external_id)"
    )
    c.execute("CREATE INDEX IF NOT EXISTS idx_actions_status ON actions(status)")
    conn.commit()
    conn.close()