"""Resume-variant CRUD. The human authors these; the agent only selects one."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from ..db import transaction
from ..events import log_event
from ..util import now_iso


def resolve_path(path: str) -> str:
    """Normalize to an absolute path string (existence not required yet)."""
    return str(Path(path).expanduser().resolve())


def add_resume(
    conn: sqlite3.Connection, track: str, file_path: str, notes: str | None = None
) -> int:
    track = track.strip().lower()
    if not track:
        raise ValueError("track is required")
    path = resolve_path(file_path)
    now = now_iso()
    with transaction(conn):
        cur = conn.execute(
            "INSERT INTO resume_variants (track, file_path, notes, updated_at) "
            "VALUES (?, ?, ?, ?)",
            (track, path, notes, now),
        )
        rid = int(cur.lastrowid)
        log_event(conn, "resume_variant", rid, "added", {"track": track, "file_path": path})
    return rid


def list_resumes(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(conn.execute("SELECT * FROM resume_variants ORDER BY track, id").fetchall())


def get_resume(conn: sqlite3.Connection, resume_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM resume_variants WHERE id = ?", (resume_id,)
    ).fetchone()


def resumes_for_track(conn: sqlite3.Connection, track: str) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            "SELECT * FROM resume_variants WHERE track = ? ORDER BY updated_at DESC",
            (track.strip().lower(),),
        ).fetchall()
    )


def update_resume(conn: sqlite3.Connection, resume_id: int, **fields) -> None:
    allowed = {"track", "file_path", "notes"}
    updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if "track" in updates:
        updates["track"] = str(updates["track"]).strip().lower()
    if "file_path" in updates:
        updates["file_path"] = resolve_path(str(updates["file_path"]))
    if not updates:
        return
    updates["updated_at"] = now_iso()
    with transaction(conn):
        assignments = ", ".join(f"{c} = ?" for c in updates)
        conn.execute(
            f"UPDATE resume_variants SET {assignments} WHERE id = ?",
            (*updates.values(), resume_id),
        )
        log_event(conn, "resume_variant", resume_id, "updated", {"fields": sorted(updates)})


def delete_resume(conn: sqlite3.Connection, resume_id: int) -> None:
    with transaction(conn):
        conn.execute("DELETE FROM resume_variants WHERE id = ?", (resume_id,))
        log_event(conn, "resume_variant", resume_id, "deleted")


def missing_files(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Resume rows whose file_path does not currently exist on disk."""
    return [r for r in list_resumes(conn) if not Path(r["file_path"]).exists()]
