"""SQLite access: connection setup, schema init, and a transaction helper.

The DB is a single plain file (guardrail §2: inspectable, editable). We enable
foreign keys and WAL so concurrent reads during a long pipeline run are safe.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .config import Config

_SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def connect(config: Config) -> sqlite3.Connection:
    """Open a tuned connection to the configured DB file."""
    config.paths.data_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.paths.db_path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """Create all tables/indexes if absent (idempotent)."""
    conn.executescript(_SCHEMA_PATH.read_text(encoding="utf-8"))


def get_conn(config: Config) -> sqlite3.Connection:
    """Connect and ensure the schema exists — the common entry point."""
    conn = connect(config)
    init_db(conn)
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Run a block atomically. Commits on success, rolls back on error.

    We open connections in autocommit mode (isolation_level=None) and manage
    explicit BEGIN/COMMIT here so multi-write operations are all-or-nothing.
    """
    conn.execute("BEGIN")
    try:
        yield conn
    except Exception:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")
