"""Cover-letter library CRUD. Letters may be finished text or Jinja2 templates."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from ..db import transaction
from ..events import log_event
from ..util import now_iso
from .resumes import resolve_path


def add_cover_letter(
    conn: sqlite3.Connection,
    name: str,
    file_path: str,
    track: str | None = None,
    is_template: bool = False,
) -> int:
    if not name.strip():
        raise ValueError("name is required")
    path = resolve_path(file_path)
    track_norm = track.strip().lower() if track else None
    now = now_iso()
    with transaction(conn):
        cur = conn.execute(
            "INSERT INTO cover_letters (name, track, is_template, file_path, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (name.strip(), track_norm, 1 if is_template else 0, path, now),
        )
        cid = int(cur.lastrowid)
        log_event(
            conn, "cover_letter", cid, "added",
            {"name": name, "track": track_norm, "is_template": bool(is_template)},
        )
    return cid


def list_cover_letters(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(conn.execute("SELECT * FROM cover_letters ORDER BY track, id").fetchall())


def get_cover_letter(conn: sqlite3.Connection, cover_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM cover_letters WHERE id = ?", (cover_id,)).fetchone()


def cover_letters_for_track(conn: sqlite3.Connection, track: str) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            "SELECT * FROM cover_letters WHERE track = ? ORDER BY updated_at DESC",
            (track.strip().lower(),),
        ).fetchall()
    )


def update_cover_letter(conn: sqlite3.Connection, cover_id: int, **fields) -> None:
    allowed = {"name", "track", "is_template", "file_path"}
    updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if "track" in updates:
        updates["track"] = str(updates["track"]).strip().lower()
    if "file_path" in updates:
        updates["file_path"] = resolve_path(str(updates["file_path"]))
    if "is_template" in updates:
        updates["is_template"] = 1 if updates["is_template"] else 0
    if not updates:
        return
    updates["updated_at"] = now_iso()
    with transaction(conn):
        assignments = ", ".join(f"{c} = ?" for c in updates)
        conn.execute(
            f"UPDATE cover_letters SET {assignments} WHERE id = ?",
            (*updates.values(), cover_id),
        )
        log_event(conn, "cover_letter", cover_id, "updated", {"fields": sorted(updates)})


def delete_cover_letter(conn: sqlite3.Connection, cover_id: int) -> None:
    with transaction(conn):
        conn.execute("DELETE FROM cover_letters WHERE id = ?", (cover_id,))
        log_event(conn, "cover_letter", cover_id, "deleted")


def missing_files(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return [r for r in list_cover_letters(conn) if not Path(r["file_path"]).exists()]
