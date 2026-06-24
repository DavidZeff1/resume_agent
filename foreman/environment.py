"""Environment — the world the agents perceive and act on.

A ``Workspace`` is a code directory plus a *test command* (the objective truth
signal). Agents sense it (read files, diff, test results) and act on it (edit
files, run tests). PEAS framing:

    Performance : a green, reviewed diff in as few turns as possible
    Environment : this workspace + the task description
    Actuators   : the file-edit / bash / run-tests tools
    Sensors     : file reads, the unified diff, the test runner's exit code

The test command runs as a real subprocess — its exit code is something the
agents cannot talk their way past. That is the point.
"""

from __future__ import annotations

import difflib
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


class PathEscape(PermissionError):
    """Raised when an agent tries to touch a path outside the workspace root."""


@dataclass
class CmdResult:
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


class Workspace:
    def __init__(self, root: str | Path, test_cmd: list[str] | None = None) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        # Default truth signal: run check.py with the current interpreter.
        # ``-B`` + PYTHONDONTWRITEBYTECODE (set in run_cmd) ensure the test never
        # imports stale .pyc bytecode after the agent edits a source file — a real
        # gotcha for any agent that edits code then immediately re-runs it.
        self.test_cmd = test_cmd or [sys.executable, "-B", "check.py"]
        self._baseline = self._snapshot()

    # -- path safety (a hard guardrail, enforced in one place) --------------- #
    def safe_path(self, rel: str) -> Path:
        p = (self.root / rel).resolve()
        if p != self.root and self.root not in p.parents:
            raise PathEscape(f"path escapes workspace: {rel!r}")
        return p

    # -- sensors ------------------------------------------------------------- #
    def read_text(self, rel: str) -> str:
        return self.safe_path(rel).read_text(encoding="utf-8")

    def list_files(self) -> list[str]:
        return sorted(
            str(p.relative_to(self.root))
            for p in self.root.rglob("*")
            if p.is_file()
        )

    def _snapshot(self) -> dict[str, str]:
        snap: dict[str, str] = {}
        for p in self.root.rglob("*"):
            if p.is_file():
                try:
                    snap[str(p.relative_to(self.root))] = p.read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    pass
        return snap

    def diff(self) -> str:
        """Unified diff of the workspace vs. the baseline captured at start."""
        current = self._snapshot()
        out: list[str] = []
        for rel in sorted(set(self._baseline) | set(current)):
            before = self._baseline.get(rel, "").splitlines(keepends=True)
            after = current.get(rel, "").splitlines(keepends=True)
            if before != after:
                out.extend(difflib.unified_diff(
                    before, after, fromfile=f"a/{rel}", tofile=f"b/{rel}"
                ))
        return "".join(out)

    # -- actuators ----------------------------------------------------------- #
    def write_text(self, rel: str, content: str) -> None:
        path = self.safe_path(rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def run_cmd(self, cmd: list[str], timeout: float = 60.0) -> CmdResult:
        env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
        proc = subprocess.run(
            cmd, cwd=self.root, capture_output=True, text=True,
            timeout=timeout, env=env,
        )
        return CmdResult(proc.returncode, proc.stdout, proc.stderr)

    def run_tests(self) -> CmdResult:
        return self.run_cmd(self.test_cmd)

    # -- helpers ------------------------------------------------------------- #
    @classmethod
    def seed(cls, root: str | Path, files: dict[str, str],
             test_cmd: list[str] | None = None) -> "Workspace":
        """Create a workspace pre-populated with ``files`` (for demos/tests)."""
        root = Path(root)
        for rel, content in files.items():
            p = (root / rel)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
        return cls(root, test_cmd=test_cmd)
