"""Git worktree isolation — each task develops on its own branch.

A worktree is the agents' blast radius: a separate working tree + branch checked
out of the target repo. A bad run is ``git worktree remove`` and it never touched
``main``. When the target isn't a git repo (the demo, the tests), this falls back
to a plain throwaway directory.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .environment import Workspace


def _git(root: str | Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(root),
                          capture_output=True, text=True)


def is_git_repo(path: str | Path) -> bool:
    try:
        r = _git(path, "rev-parse", "--is-inside-work-tree")
        return r.returncode == 0 and r.stdout.strip() == "true"
    except (FileNotFoundError, NotADirectoryError):
        return False


def create_worktree(repo_root: str | Path, branch: str, base: str = "HEAD") -> Path:
    path = Path(tempfile.mkdtemp(prefix="foreman-wt-"))
    r = _git(repo_root, "worktree", "add", "-b", branch, str(path), base)
    if r.returncode != 0:
        shutil.rmtree(path, ignore_errors=True)
        raise RuntimeError(f"git worktree add failed: {r.stderr.strip()}")
    return path


def remove_worktree(repo_root: str | Path, path: str | Path) -> None:
    _git(repo_root, "worktree", "remove", "--force", str(path))
    shutil.rmtree(path, ignore_errors=True)


def commit_all(path: str | Path, message: str) -> str | None:
    _git(path, "add", "-A")
    r = _git(path, "-c", "user.email=foreman@local", "-c", "user.name=Foreman",
             "commit", "-m", message)
    if r.returncode != 0:
        return None
    sha = _git(path, "rev-parse", "HEAD")
    return sha.stdout.strip() or None


@dataclass
class WorkspaceHandle:
    workspace: Workspace
    branch: str | None = None
    worktree_path: Path | None = None
    repo_root: str | None = None

    @property
    def is_git(self) -> bool:
        return self.worktree_path is not None

    def cleanup(self) -> None:
        if self.worktree_path and self.repo_root:
            remove_worktree(self.repo_root, self.worktree_path)


def make_workspace(*, target_repo: str | Path | None = None, branch: str | None = None,
                   test_cmd: list[str] | None = None, seed_files: dict | None = None,
                   root: str | Path | None = None) -> WorkspaceHandle:
    """Build the workspace a task runs in.

    With a git ``target_repo`` -> an isolated worktree on ``branch``.
    Otherwise -> a plain (optionally seeded) directory, for the demo/tests.
    """
    if target_repo and is_git_repo(target_repo):
        br = branch or "foreman/task"
        wt = create_worktree(target_repo, br)
        return WorkspaceHandle(Workspace(wt, test_cmd=test_cmd), br, wt, str(target_repo))

    if root is None:
        root = tempfile.mkdtemp(prefix="foreman-ws-")
    if seed_files is not None:
        ws = Workspace.seed(root, seed_files, test_cmd=test_cmd)
    else:
        ws = Workspace(root, test_cmd=test_cmd)
    return WorkspaceHandle(ws)
