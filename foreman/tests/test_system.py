"""Offline tests for the ledger, orchestration loop, worktree isolation,
analytics, and the web UI. Git- and starlette-dependent tests skip cleanly."""

from __future__ import annotations

import shutil
import subprocess

import pytest

from foreman.agent import Action
from foreman.agents import ImplementerAgent, Orchestrator, ReviewerAgent
from foreman.analytics import compute_stats
from foreman.blackboard import Blackboard
from foreman.demo import BUGGY_FILES, implementer_decide
from foreman.environment import Workspace
from foreman.models import TaskStatus
from foreman.policy import MockPolicy, estimate_cost
from foreman.repo import get_task, list_runs, recent_events
from foreman.runner import record_merge, run_task
from foreman.tools import EditFile, ReadFile, RunTests, SubmitReview


# -- ledger + runner -------------------------------------------------------- #
def test_run_task_persists_to_ledger(tmp_path):
    db = tmp_path / "f.sqlite3"
    conn, tid, status = run_task(title="fix add", description="add must add",
                                 policy="mock", seed_files=BUGGY_FILES, db_path=str(db))
    assert status == TaskStatus.PR_READY
    assert get_task(conn, tid)["status"] == "pr_ready"
    runs = list_runs(conn, tid)
    assert any(r["role"] == "implementer" and r["tests_passed"] == 1 for r in runs)
    assert any(r["role"] == "reviewer" and r["verdict"] == "approve" for r in runs)
    assert recent_events(conn, task_id=tid)                 # audit trail persisted

    stats = compute_stats(conn)
    assert stats["tasks_total"] == 1
    assert stats["reached_pr_ready"] == 1
    assert stats["success_rate_pct"] == 100.0


def test_record_merge_is_the_human_gate(tmp_path):
    db = tmp_path / "f.sqlite3"
    conn, tid, status = run_task(title="fix add", policy="mock",
                                 seed_files=BUGGY_FILES, db_path=str(db))
    assert status == TaskStatus.PR_READY
    record_merge(conn, tid)                                  # the one human action
    assert get_task(conn, tid)["status"] == TaskStatus.MERGED


# -- the bounded review-cycle loop ------------------------------------------ #
def test_review_change_request_then_approve(tmp_path):
    state = {"n": 0}

    def reviewer_fn(agent, percept):
        state["n"] += 1
        if state["n"] == 1:
            return [Action("submit_review", {"verdict": "request_changes",
                                             "findings": ["needs a docstring"]})]
        return [Action("submit_review", {"verdict": "approve", "findings": []})]

    impl = ImplementerAgent("implementer", MockPolicy(implementer_decide),
                            [ReadFile(), EditFile(), RunTests()])
    rev = ReviewerAgent("reviewer", MockPolicy(reviewer_fn), [ReadFile(), SubmitReview()])
    orch = Orchestrator(impl, rev, max_review_cycles=2)

    ws = Workspace.seed(tmp_path, BUGGY_FILES)
    bb = Blackboard()
    bb.set("status", TaskStatus.QUEUED)
    status = orch.run(ws, bb)

    assert status == TaskStatus.PR_READY
    assert state["n"] == 2                                   # reviewed twice (loop ran)


def test_review_loop_is_bounded(tmp_path):
    def always_changes(agent, percept):
        return [Action("submit_review", {"verdict": "request_changes", "findings": ["x"]})]

    impl = ImplementerAgent("implementer", MockPolicy(implementer_decide),
                            [ReadFile(), EditFile(), RunTests()])
    rev = ReviewerAgent("reviewer", MockPolicy(always_changes), [ReadFile(), SubmitReview()])
    orch = Orchestrator(impl, rev, max_review_cycles=2)

    ws = Workspace.seed(tmp_path, BUGGY_FILES)
    bb = Blackboard()
    bb.set("status", TaskStatus.QUEUED)
    status = orch.run(ws, bb)
    assert status == TaskStatus.NEEDS_HUMAN              # never loops forever


# -- cost accounting -------------------------------------------------------- #
def test_cost_estimate():
    assert estimate_cost("claude-opus-4-8", 1_000_000, 0) == 5.0
    assert estimate_cost("claude-sonnet-4-6", 0, 1_000_000) == 15.0
    assert estimate_cost("mock", 1_000_000, 1_000_000) == 0.0


# -- git worktree isolation + PR prep --------------------------------------- #
@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_worktree_isolation_keeps_main_clean(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    for name, content in BUGGY_FILES.items():
        (repo / name).write_text(content)

    def g(*a):
        subprocess.run(["git", *a], cwd=repo, capture_output=True, text=True, check=True)

    g("init", "-q")
    g("config", "user.email", "t@t")
    g("config", "user.name", "t")
    g("add", "-A")
    g("commit", "-q", "-m", "init")

    db = tmp_path / "f.sqlite3"
    conn, tid, status = run_task(title="fix add in repo", target_repo=str(repo),
                                 policy="mock", db_path=str(db))

    assert status == TaskStatus.PR_READY
    t = get_task(conn, tid)
    assert t["branch"] == f"foreman/task-{tid}"
    assert t["pr_ref"] and t["pr_ref"].startswith("branch:")

    # the fix lives on the branch; main is untouched (the irreversible boundary held)
    branches = subprocess.run(["git", "branch", "--list", t["branch"]], cwd=repo,
                              capture_output=True, text=True).stdout
    assert t["branch"] in branches
    main_calc = subprocess.run(["git", "show", "HEAD:calc.py"], cwd=repo,
                               capture_output=True, text=True).stdout
    assert "a - b" in main_calc                         # main still has the bug


# -- the web UI ------------------------------------------------------------- #
def test_web_ui_serves_and_merges(tmp_path):
    pytest.importorskip("starlette")
    from starlette.testclient import TestClient

    from foreman.web.app import create_app

    db = tmp_path / "f.sqlite3"
    conn, tid, status = run_task(title="web task", policy="mock",
                                 seed_files=BUGGY_FILES, db_path=str(db))
    client = TestClient(create_app(db_path=str(db)))

    assert client.get("/healthz").text == "ok"
    assert client.get("/").status_code == 200
    assert "web task" in client.get("/tasks").text
    assert client.get(f"/task/{tid}").status_code == 200
    assert "web task" in client.get("/review").text          # it's in the queue

    client.post(f"/task/{tid}/merge")                        # human merges via UI
    assert get_task(conn, tid)["status"] == TaskStatus.MERGED


def test_web_control_panel_starts_a_run(tmp_path, monkeypatch):
    pytest.importorskip("starlette")
    monkeypatch.setenv("FOREMAN_SYNC", "1")          # run inline -> deterministic
    from starlette.testclient import TestClient

    from foreman.db import get_conn
    from foreman.repo import list_tasks
    from foreman.web.app import create_app

    db = tmp_path / "f.sqlite3"
    client = TestClient(create_app(db_path=str(db)))

    assert client.get("/new").status_code == 200      # the form renders
    assert client.post("/demo").status_code == 200    # one-click sample run

    conn = get_conn(str(db))
    tasks = list_tasks(conn)
    assert len(tasks) == 1
    assert tasks[0]["status"] == TaskStatus.PR_READY   # ran to completion

    # the full "new run" form also drives a run
    client.post("/new", data={"title": "via form", "target": "demo",
                              "policy": "mock", "model": "claude-opus-4-8"})
    assert any(t["title"] == "via form" and t["status"] == TaskStatus.PR_READY
               for t in list_tasks(conn))
