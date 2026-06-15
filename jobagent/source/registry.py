"""Adapter registry. Importing this module registers all built-in adapters."""

from __future__ import annotations

from typing import Mapping

from .base import ADAPTERS, Adapter, AdapterError

# Importing each module triggers its @register(...) side effect.
from . import greenhouse, html_generic, lever, workable  # noqa: F401,E402


def get_adapter(ats_type: str, *, has_board_url: bool = False) -> Adapter:
    """Look up an adapter by ats_type.

    Unknown types fall back to the generic HTML adapter when a board_url is
    available, so a new ATS can be added to the watchlist with ats_type 'other'.
    """
    ats_type = (ats_type or "").strip().lower()
    if ats_type in ADAPTERS:
        return ADAPTERS[ats_type]
    if has_board_url and "other" in ADAPTERS:
        return ADAPTERS["other"]
    raise AdapterError(f"no adapter for ats_type {ats_type!r}")


def supported_ats() -> list[str]:
    return sorted(ADAPTERS)
