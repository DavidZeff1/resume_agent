"""File tools, sandboxed to a workspace directory.

Reads and writes are confined to ``AGENT_WORKSPACE`` (default: current working
directory) so the agent can't wander off and touch arbitrary paths.
"""

from __future__ import annotations

import os
from pathlib import Path

from . import registry

WORKSPACE = Path(os.environ.get("AGENT_WORKSPACE", ".")).resolve()
MAX_BYTES = 100_000


def _resolve(path: str) -> Path:
    p = (WORKSPACE / path).resolve()
    if p != WORKSPACE and WORKSPACE not in p.parents:
        raise ValueError(f"path {path!r} is outside the workspace {WORKSPACE}")
    return p


@registry.tool
def read_file(path: str) -> str:
    """Read a UTF-8 text file from the workspace and return its contents.

    Args:
        path: file path relative to the workspace root
    """
    p = _resolve(path)
    if not p.is_file():
        return f"Error: no such file: {path}"
    data = p.read_text(encoding="utf-8", errors="replace")
    if len(data) > MAX_BYTES:
        return data[:MAX_BYTES] + f"\n... [truncated, {len(data)} bytes total]"
    return data


@registry.tool
def write_file(path: str, content: str) -> str:
    """Create or overwrite a text file in the workspace.

    Args:
        path: file path relative to the workspace root
        content: the full text to write
    """
    p = _resolve(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"Wrote {len(content)} bytes to {path}"


@registry.tool
def list_dir(path: str = ".") -> str:
    """List the entries of a directory in the workspace.

    Args:
        path: directory path relative to the workspace root (default: root)
    """
    p = _resolve(path)
    if not p.is_dir():
        return f"Error: not a directory: {path}"
    entries = sorted(
        f"{c.name}/" if c.is_dir() else c.name for c in p.iterdir()
    )
    return "\n".join(entries) if entries else "(empty)"
