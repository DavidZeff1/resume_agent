"""SQLite connection + schema init for the Foreman ledger."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

_SCHEMA = Path(__file__).with_name("schema.sql")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get_conn(db_path: str | Path) -> sqlite3.Connection:
    """Open (creating if needed) the ledger DB and ensure the schema exists.

    Pass ``":memory:"`` for an ephemeral ledger (used by the demo and tests).
    """
    if db_path != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")   # UI + background worker share the file
    conn.executescript(_SCHEMA.read_text(encoding="utf-8"))
    conn.commit()
    return conn
