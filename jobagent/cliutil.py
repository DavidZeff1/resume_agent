"""Tiny, dependency-free helpers for rendering CLI output."""

from __future__ import annotations

import sqlite3
from typing import Iterable, Sequence


def _as_dict(row) -> dict:
    return dict(row) if isinstance(row, sqlite3.Row) else dict(row)


def fmt_cell(value, width: int | None = None) -> str:
    if value is None:
        s = "-"
    elif isinstance(value, bool):
        s = "yes" if value else "no"
    else:
        s = str(value)
    s = s.replace("\n", " ").strip()
    if width is not None and len(s) > width:
        s = s[: max(0, width - 1)] + "…"
    return s


def print_table(
    rows: Iterable,
    columns: Sequence[tuple[str, str]],
    max_width: int = 48,
    empty: str = "(none)",
) -> None:
    """Print rows as an aligned text table.

    `columns` is a sequence of (key, header) pairs.
    """
    rows = [_as_dict(r) for r in rows]
    if not rows:
        print(empty)
        return

    headers = [h for _, h in columns]
    widths = [len(h) for h in headers]
    table: list[list[str]] = []
    for r in rows:
        cells = [fmt_cell(r.get(k), max_width) for k, _ in columns]
        table.append(cells)
        for i, c in enumerate(cells):
            widths[i] = max(widths[i], len(c))

    line = "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
    print(line)
    print("  ".join("-" * widths[i] for i in range(len(headers))))
    for cells in table:
        print("  ".join(cells[i].ljust(widths[i]) for i in range(len(cells))))


def print_kv(data: dict, title: str | None = None, order: Sequence[str] | None = None) -> None:
    """Print a dict as aligned key: value lines."""
    if title:
        print(f"== {title} ==")
    if not data:
        print("(empty)")
        return
    keys = list(order) if order else list(data.keys())
    keys += [k for k in data.keys() if k not in keys]
    width = max((len(k) for k in keys), default=0)
    for k in keys:
        if k not in data:
            continue
        print(f"  {k.ljust(width)} : {fmt_cell(data[k], 120)}")


def confirm(prompt: str, assume_yes: bool = False) -> bool:
    if assume_yes:
        return True
    try:
        answer = input(f"{prompt} [y/N] ").strip().lower()
    except EOFError:
        return False
    return answer in {"y", "yes"}
