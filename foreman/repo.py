"""Data-access layer for the ledger — all SQL lives here (no ORM)."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from .db import now_iso
from .models import validate_transition


# -- tasks ------------------------------------------------------------------ #
def create_task(conn: sqlite3.Connection, *, title: str, description: str = "",
                source: str = "manual", acceptance_notes: str = "") -> int:
    ts = now_iso()
    cur = conn.execute(
        "INSERT INTO tasks (source, title, description, acceptance_notes, status, "
        "created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
        (source, title, description, acceptance_notes, "queued", ts, ts),
    )
    conn.commit()
    return int(cur.lastrowid)


def get_task(conn: sqlite3.Connection, task_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    return dict(row) if row else None


def list_tasks(conn: sqlite3.Connection, status: str | None = None) -> list[dict]:
    if status:
        rows = conn.execute(
            "SELECT * FROM tasks WHERE status = ? ORDER BY id DESC", (status,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM tasks ORDER BY id DESC").fetchall()
    return [dict(r) for r in rows]


def set_task_status(conn: sqlite3.Connection, task_id: int, target: str,
                    *, validate: bool = True) -> None:
    cur = (get_task(conn, task_id) or {}).get("status", "queued")
    if validate:
        validate_transition(cur, target)
    conn.execute("UPDATE tasks SET status = ?, updated_at = ? WHERE id = ?",
                 (target, now_iso(), task_id))
    conn.commit()


def update_task(conn: sqlite3.Connection, task_id: int, **fields: Any) -> None:
    if not fields:
        return
    fields["updated_at"] = now_iso()
    cols = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(f"UPDATE tasks SET {cols} WHERE id = ?", (*fields.values(), task_id))
    conn.commit()


def bump_attempts(conn: sqlite3.Connection, task_id: int) -> None:
    conn.execute("UPDATE tasks SET attempts = attempts + 1, updated_at = ? WHERE id = ?",
                 (now_iso(), task_id))
    conn.commit()


# -- runs ------------------------------------------------------------------- #
def add_run(conn: sqlite3.Connection, *, task_id: int, role: str, model: str = "",
            turns: int = 0, tests_passed: bool | None = None, verdict: str | None = None,
            input_tokens: int = 0, output_tokens: int = 0, cost_usd: float = 0.0,
            started_at: str | None = None) -> int:
    cur = conn.execute(
        "INSERT INTO runs (task_id, role, model, turns, input_tokens, output_tokens, "
        "cost_usd, tests_passed, verdict, started_at, ended_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (task_id, role, model, turns, input_tokens, output_tokens, cost_usd,
         None if tests_passed is None else int(tests_passed), verdict,
         started_at or now_iso(), now_iso()),
    )
    conn.commit()
    return int(cur.lastrowid)


def list_runs(conn: sqlite3.Connection, task_id: int | None = None) -> list[dict]:
    if task_id is not None:
        rows = conn.execute("SELECT * FROM runs WHERE task_id = ? ORDER BY id", (task_id,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM runs ORDER BY id DESC").fetchall()
    return [dict(r) for r in rows]


# -- events ----------------------------------------------------------------- #
def log_event(conn: sqlite3.Connection, *, task_id: int | None, actor: str,
              action: str, detail: dict | None = None) -> None:
    conn.execute(
        "INSERT INTO events (ts, task_id, actor, action, detail) VALUES (?,?,?,?,?)",
        (now_iso(), task_id, actor, action, json.dumps(detail) if detail else None),
    )
    conn.commit()


def recent_events(conn: sqlite3.Connection, limit: int = 50,
                  task_id: int | None = None) -> list[dict]:
    if task_id is not None:
        rows = conn.execute(
            "SELECT * FROM events WHERE task_id = ? ORDER BY id DESC LIMIT ?",
            (task_id, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def status_counts(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute(
        "SELECT status, COUNT(*) n FROM tasks GROUP BY status"
    ).fetchall()
    return {r["status"]: r["n"] for r in rows}
