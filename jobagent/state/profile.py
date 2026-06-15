"""The single profile row — the human's core facts (build plan §4/§5).

This is the ONLY source of facts the agent may use about the human. The prep
stage reads from here; anything missing becomes an unanswered field for the
human rather than something the agent invents (guardrail §2).
"""

from __future__ import annotations

import sqlite3

from ..db import transaction
from ..events import log_event
from ..util import from_json, now_iso, to_json

# Plain text columns the human can set directly.
_TEXT_COLUMNS = {
    "full_name", "email", "phone", "citizenship", "work_authorization",
    "location", "github_url", "linkedin_url", "portfolio_url",
    "salary_expectation_notes",
}


def get_profile(conn: sqlite3.Connection) -> dict | None:
    """Return the profile as a dict (languages/extra_facts decoded), or None."""
    row = conn.execute("SELECT * FROM profile WHERE id = 1").fetchone()
    if row is None:
        return None
    d = dict(row)
    d["languages"] = from_json(d.get("languages"), [])
    d["extra_facts"] = from_json(d.get("extra_facts"), {})
    return d


def set_profile(
    conn: sqlite3.Connection,
    *,
    languages: list[str] | None = None,
    extra_facts: dict | None = None,
    merge_extra_facts: bool = True,
    **fields: str | None,
) -> dict:
    """Create or update the profile row (id = 1). Partial updates supported.

    Only non-None values are written, so callers can update one field at a time.
    `languages` replaces the list; `extra_facts` merges by default.
    """
    unknown = set(fields) - _TEXT_COLUMNS
    if unknown:
        raise ValueError(f"Unknown profile field(s): {sorted(unknown)}")

    updates: dict[str, object] = {k: v for k, v in fields.items() if v is not None}

    if languages is not None:
        updates["languages"] = to_json(languages)
    if extra_facts is not None:
        if merge_extra_facts:
            current = (get_profile(conn) or {}).get("extra_facts", {}) or {}
            current.update(extra_facts)
            updates["extra_facts"] = to_json(current)
        else:
            updates["extra_facts"] = to_json(extra_facts)

    updates["updated_at"] = now_iso()

    with transaction(conn):
        conn.execute("INSERT INTO profile (id) VALUES (1) ON CONFLICT(id) DO NOTHING")
        assignments = ", ".join(f"{col} = ?" for col in updates)
        conn.execute(
            f"UPDATE profile SET {assignments} WHERE id = 1",
            tuple(updates.values()),
        )
        log_event(conn, "profile", 1, "updated", {"fields": sorted(updates)})

    return get_profile(conn)  # type: ignore[return-value]


def missing_core_fields(conn: sqlite3.Connection) -> list[str]:
    """Core fields that should be set before the pipeline can prep applications."""
    required = ["full_name", "email", "work_authorization", "location"]
    profile = get_profile(conn) or {}
    return [f for f in required if not profile.get(f)]
