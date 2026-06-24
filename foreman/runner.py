"""High-level entrypoint: take a task from queued to a prepared PR.

``run_task`` creates a task and runs it to completion (used by the CLI/tests).
``start_run`` launches an already-created task in a background thread (used by
the web control panel, so clicking "Run" returns immediately and the page shows
live status). Set ``FOREMAN_SYNC=1`` to run inline instead (used by tests).
Nothing here merges or pushes — that stays a human action.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

from . import repo as _repo
from .agents import ImplementerAgent, Orchestrator, ReviewerAgent
from .blackboard import Blackboard
from .db import get_conn
from .models import TaskStatus
from .policy import MockPolicy, make_policy
from .tools import EditFile, ReadFile, RunTests, SubmitReview
from .worktree import commit_all, make_workspace

DEFAULT_DATA_DIR = Path(os.environ.get("FOREMAN_DATA_DIR", "data/foreman"))


def default_db_path() -> Path:
    return DEFAULT_DATA_DIR / "foreman.sqlite3"


def build_team(policy: str = "mock", *, model: str = "claude-opus-4-8",
               max_review_cycles: int = 2) -> Orchestrator:
    if policy == "llm":
        impl_policy = make_policy("llm", model=model)
        rev_policy = make_policy("llm", model=model)
    else:  # deterministic, offline — meaningful on the demo's seeded workspace
        from .demo import implementer_decide, reviewer_decide
        impl_policy = MockPolicy(implementer_decide)
        rev_policy = MockPolicy(reviewer_decide)
    implementer = ImplementerAgent("implementer", impl_policy,
                                   [ReadFile(), EditFile(), RunTests()])
    reviewer = ReviewerAgent("reviewer", rev_policy, [ReadFile(), SubmitReview()])
    return Orchestrator(implementer, reviewer, max_review_cycles=max_review_cycles)


def execute_task(task_id: int, *, db_path, target_repo: str | None = None,
                 test_cmd: list[str] | None = None, policy: str = "mock",
                 model: str = "claude-opus-4-8", seed_files: dict | None = None) -> str:
    """Run an already-created task to a terminal status. Robust to failures."""
    conn = get_conn(db_path or default_db_path())
    t = _repo.get_task(conn, task_id) or {}
    title = t.get("title") or "task"
    description = t.get("description") or ""
    branch = f"foreman/task-{task_id}"

    handle = make_workspace(target_repo=target_repo or None, branch=branch,
                            test_cmd=test_cmd, seed_files=seed_files)
    _repo.update_task(conn, task_id, branch=handle.branch,
                      worktree_path=str(handle.worktree_path) if handle.worktree_path else None)

    bb = Blackboard()
    bb.set("task", {"title": title, "description": description})
    bb.set("status", TaskStatus.QUEUED)
    try:
        status = build_team(policy=policy, model=model).run(handle.workspace, bb,
                                                            conn=conn, task_id=task_id)
        if status == TaskStatus.PR_READY:
            sha = commit_all(handle.worktree_path, f"[foreman] {title}") if handle.is_git else None
            if handle.is_git:
                _repo.update_task(conn, task_id, pr_ref=f"branch:{handle.branch}@{sha or '?'}")
            _repo.log_event(conn, task_id=task_id, actor="orchestrator", action="pr_prepared",
                            detail={"branch": handle.branch, "sha": sha,
                                    "diff": handle.workspace.diff()})
    except Exception as exc:                                  # surface, don't crash
        _repo.update_task(conn, task_id, last_error=str(exc))
        try:
            _repo.set_task_status(conn, task_id, TaskStatus.NEEDS_HUMAN)
        except Exception:
            pass
        _repo.log_event(conn, task_id=task_id, actor="orchestrator", action="error",
                        detail={"error": str(exc)})
    finally:
        if handle.is_git:
            handle.cleanup()
    return (_repo.get_task(conn, task_id) or {}).get("status", TaskStatus.NEEDS_HUMAN)


def run_task(*, title: str, description: str = "", source: str = "manual",
             target_repo: str | None = None, test_cmd: list[str] | None = None,
             policy: str = "mock", model: str = "claude-opus-4-8",
             seed_files: dict | None = None, db_path=None):
    """Create + run a task to completion. Returns (conn, task_id, final_status)."""
    db_path = db_path or default_db_path()
    conn = get_conn(db_path)
    task_id = _repo.create_task(conn, title=title, description=description, source=source)
    status = execute_task(task_id, db_path=db_path, target_repo=target_repo,
                          test_cmd=test_cmd, policy=policy, model=model, seed_files=seed_files)
    return conn, task_id, status


def start_run(task_id: int, *, db_path, **kwargs) -> None:
    """Launch a created task. Background thread by default; inline if FOREMAN_SYNC."""
    if os.environ.get("FOREMAN_SYNC"):
        execute_task(task_id, db_path=db_path, **kwargs)
        return
    threading.Thread(target=execute_task, kwargs={"task_id": task_id, "db_path": db_path, **kwargs},
                     daemon=True).start()


def record_merge(conn, task_id: int) -> None:
    """The human action: record that a prepared PR was merged."""
    _repo.set_task_status(conn, task_id, TaskStatus.MERGED)
    _repo.log_event(conn, task_id=task_id, actor="human", action="merged", detail=None)
