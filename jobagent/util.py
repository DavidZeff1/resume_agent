"""Small, dependency-free helpers shared across the package."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any


def utcnow() -> datetime:
    """Timezone-aware 'now' in UTC. Always use this, never naive datetimes."""
    return datetime.now(timezone.utc)


def now_iso() -> str:
    """Current UTC time as an ISO-8601 string (what we store in SQLite)."""
    return utcnow().isoformat()


def iso_in_days(days: int) -> str:
    """ISO-8601 timestamp `days` from now (used for follow-up scheduling)."""
    return (utcnow() + timedelta(days=days)).isoformat()


def parse_iso(value: str | None) -> datetime | None:
    """Parse an ISO timestamp we previously stored; tolerant of None/blank."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:  # treat legacy naive values as UTC
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def to_json(value: Any) -> str:
    """Serialize a value for a JSON text column. Stable key order for diffs."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def from_json(value: str | None, default: Any = None) -> Any:
    """Parse a JSON text column, returning `default` on null/blank/garbage."""
    if value is None or value == "":
        return default
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return default


def deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge `override` into `base`, returning a new dict.

    Lists and scalars in `override` replace those in `base`; nested dicts merge.
    """
    out = dict(base)
    for key, value in (override or {}).items():
        if key in out and isinstance(out[key], dict) and isinstance(value, dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def truncate(text: str | None, limit: int = 12000) -> str:
    """Clamp long text (e.g. a job description) before sending it to a model."""
    if not text:
        return ""
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n…[truncated]"
