"""Offline tests for the Foreman agent architecture.

These prove the multi-agent loop, the coordination, the state machine, and the
guardrails are real — no network, no API key (same philosophy as jobagent's
offline suite).
"""

from __future__ import annotations

import pytest

from foreman.agents import Orchestrator
from foreman.blackboard import Blackboard
from foreman.demo import BUGGY_FILES, TASK, build_team, seed_workspace
from foreman.environment import PathEscape, Workspace
from foreman.models import InvalidTransition, TaskStatus, validate_transition
from foreman.tools import EditFile, RunTests, UnsafeCommand, assert_safe_command


def make_ws(tmp_path) -> Workspace:
    return Workspace.seed(tmp_path, BUGGY_FILES)


# -- state machine ---------------------------------------------------------- #
def test_state_machine_allows_legal_path():
    validate_transition(TaskStatus.QUEUED, TaskStatus.PLANNING)
    validate_transition(TaskStatus.TESTING, TaskStatus.REVIEWING)
    validate_transition(TaskStatus.REVIEWING, TaskStatus.PR_READY)
    validate_transition(TaskStatus.PR_READY, TaskStatus.MERGED)


def test_state_machine_rejects_illegal_jump():
    with pytest.raises(InvalidTransition):
        validate_transition(TaskStatus.QUEUED, TaskStatus.MERGED)
    with pytest.raises(InvalidTransition):
        validate_transition(TaskStatus.MERGED, TaskStatus.IMPLEMENTING)


def test_needs_human_is_reachable_from_any_nonterminal():
    validate_transition(TaskStatus.IMPLEMENTING, TaskStatus.NEEDS_HUMAN)
    with pytest.raises(InvalidTransition):
        validate_transition(TaskStatus.MERGED, TaskStatus.NEEDS_HUMAN)


# -- guardrails ------------------------------------------------------------- #
def test_path_confinement(tmp_path):
    ws = make_ws(tmp_path)
    with pytest.raises(PathEscape):
        ws.safe_path("../escape.py")


def test_command_allowlist():
    assert_safe_command("python check.py")            # fine
    for bad in ("rm -rf /", "python a && rm b", "git push origin main", "curl http://x"):
        with pytest.raises(UnsafeCommand):
            assert_safe_command(bad)


def test_agents_have_no_merge_or_push_tool():
    team = build_team()
    for agent in (team.implementer, team.reviewer):
        assert "merge" not in agent.tools
        assert "push" not in agent.tools
    # the implementer can edit/test but not merge; the reviewer is read-only + review
    assert set(team.implementer.tools) == {"read_file", "edit_file", "run_tests"}
    assert set(team.reviewer.tools) == {"read_file", "submit_review"}


# -- the truth signal ------------------------------------------------------- #
def test_run_tests_is_an_objective_gate(tmp_path):
    ws = make_ws(tmp_path)
    bb = Blackboard()
    RunTests().run(ws, bb)
    assert bb.get("test_result")["passed"] is False     # starts red
    EditFile().run(ws, bb, path="calc.py", find="a - b", replace="a + b")
    RunTests().run(ws, bb)
    assert bb.get("test_result")["passed"] is True       # green only after a real fix


# -- the full multi-agent loop ---------------------------------------------- #
def test_full_loop_reaches_pr_ready(tmp_path):
    ws = make_ws(tmp_path)
    bb = Blackboard()
    bb.set("task", TASK)
    bb.set("status", TaskStatus.QUEUED)

    final = build_team().run(ws, bb)

    assert final == TaskStatus.PR_READY                  # one click from merge
    assert bb.get("test_result")["passed"] is True
    assert bb.get("review")["verdict"] == "approve"
    assert ws.read_text("calc.py") == "def add(a, b):\n    return a + b\n"
    # the loop is fully audited
    assert any(e.action == "status" for e in bb.events)
    assert any(e.action == "tests" for e in bb.events)
