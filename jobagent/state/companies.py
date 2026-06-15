"""Company watchlist CRUD + sync from config.

The watchlist is finite and human-defined (guardrail §2). It lives in
``config.yaml`` as the source of truth and is mirrored into the `companies`
table via ``sync_from_config`` so the sourcing stage can join against jobs.
"""

from __future__ import annotations

import sqlite3

from ..config import Config
from ..db import transaction
from ..events import log_event
from ..util import now_iso

VALID_ATS = {"greenhouse", "lever", "workable", "comeet", "other"}


def add_company(
    conn: sqlite3.Connection,
    name: str,
    ats_type: str,
    board_token: str | None = None,
    board_url: str | None = None,
    notes: str | None = None,
    active: bool = True,
) -> int:
    """Insert or update a company (keyed by unique name)."""
    name = name.strip()
    ats_type = ats_type.strip().lower()
    if not name:
        raise ValueError("company name is required")
    if ats_type not in VALID_ATS:
        raise ValueError(f"ats_type must be one of {sorted(VALID_ATS)}")
    if not (board_token or board_url):
        raise ValueError("provide board_token or board_url")
    now = now_iso()
    with transaction(conn):
        cur = conn.execute(
            """
            INSERT INTO companies (name, ats_type, board_token, board_url, notes, active,
                                   created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                ats_type    = excluded.ats_type,
                board_token = excluded.board_token,
                board_url   = excluded.board_url,
                notes       = excluded.notes,
                active      = excluded.active,
                updated_at  = excluded.updated_at
            """,
            (name, ats_type, board_token, board_url, notes, 1 if active else 0, now, now),
        )
        row = conn.execute("SELECT id FROM companies WHERE name = ?", (name,)).fetchone()
        cid = int(row["id"])
        log_event(conn, "company", cid, "upserted", {"name": name, "ats_type": ats_type})
    return cid


def list_companies(conn: sqlite3.Connection, active_only: bool = False) -> list[sqlite3.Row]:
    sql = "SELECT * FROM companies"
    if active_only:
        sql += " WHERE active = 1"
    sql += " ORDER BY name"
    return list(conn.execute(sql).fetchall())


def get_company(conn: sqlite3.Connection, company_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM companies WHERE id = ?", (company_id,)).fetchone()


def set_active(conn: sqlite3.Connection, company_id: int, active: bool) -> None:
    with transaction(conn):
        conn.execute(
            "UPDATE companies SET active = ?, updated_at = ? WHERE id = ?",
            (1 if active else 0, now_iso(), company_id),
        )
        log_event(conn, "company", company_id, "set_active", {"active": bool(active)})


def delete_company(conn: sqlite3.Connection, company_id: int) -> None:
    with transaction(conn):
        conn.execute("DELETE FROM companies WHERE id = ?", (company_id,))
        log_event(conn, "company", company_id, "deleted")


def sync_from_config(conn: sqlite3.Connection, config: Config) -> dict:
    """Mirror the config watchlist into the companies table (upsert by name).

    Returns a small summary. Does not delete companies absent from config —
    that stays a deliberate human action via `company rm`.
    """
    synced = 0
    for c in config.companies:
        add_company(
            conn,
            name=c.name,
            ats_type=c.ats_type,
            board_token=c.board_token,
            board_url=c.board_url,
            notes=c.notes,
            active=c.active,
        )
        synced += 1
    log_event(conn, "pipeline", None, "companies_synced", {"count": synced})
    return {"synced": synced}
