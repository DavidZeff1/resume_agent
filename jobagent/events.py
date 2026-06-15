"""The append-only audit log (`events` table).

Every meaningful action — status transitions, sourced jobs, queued
applications, generated follow-ups — gets a row here so the human can always
reconstruct exactly what the agent did and why.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from .util import now_iso, to_json


def log_event(
    conn: sqlite3.Connection,
    entity_type: str,
    entity_id: int | None,
    action: str,
    detail: Any = None,
) -> int:
    """Append an audit event. `detail` may be any JSON-serializable value."""
    cur = conn.execute(
        "INSERT INTO events (ts, entity_type, entity_id, action, detail) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            now_iso(),
            entity_type,
            entity_id,
            action,
            to_json(detail) if detail is not None else None,
        ),
    )
    return int(cur.lastrowid)


def recent_events(conn: sqlite3.Connection, limit: int = 50) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            "SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    )
