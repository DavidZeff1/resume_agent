"""Task status + the state machine the orchestrator drives.

Mirrors jobagent's ``models.py``: transitions go through ``validate_transition``
so an illegal jump raises instead of silently corrupting state. The one
human-only edge is ``pr_ready -> merged``; ``needs_human`` is the safe sink for
anything the recoverable loop can't finish.

    queued -> planning -> implementing -> testing
    testing -> reviewing            (tests green)
    testing -> implementing         (tests red, retry)
    reviewing -> pr_ready           (approved)
    reviewing -> implementing       (changes requested)
    pr_ready -> merged              (HUMAN action only)
    <any non-terminal> -> needs_human
"""

from __future__ import annotations


class TaskStatus:
    QUEUED = "queued"
    PLANNING = "planning"
    IMPLEMENTING = "implementing"
    TESTING = "testing"
    REVIEWING = "reviewing"
    PR_READY = "pr_ready"
    MERGED = "merged"
    NEEDS_HUMAN = "needs_human"

    ALL = (
        QUEUED, PLANNING, IMPLEMENTING, TESTING,
        REVIEWING, PR_READY, MERGED, NEEDS_HUMAN,
    )
    TERMINAL = (MERGED, NEEDS_HUMAN)


_TRANSITIONS: dict[str, set[str]] = {
    TaskStatus.QUEUED: {TaskStatus.PLANNING},
    TaskStatus.PLANNING: {TaskStatus.IMPLEMENTING},
    TaskStatus.IMPLEMENTING: {TaskStatus.TESTING},
    TaskStatus.TESTING: {TaskStatus.REVIEWING, TaskStatus.IMPLEMENTING},
    TaskStatus.REVIEWING: {TaskStatus.PR_READY, TaskStatus.IMPLEMENTING},
    TaskStatus.PR_READY: {TaskStatus.MERGED},
    TaskStatus.MERGED: set(),
    TaskStatus.NEEDS_HUMAN: set(),
}


class InvalidTransition(ValueError):
    """Raised when an illegal task-status transition is attempted."""


def can_transition(current: str, target: str) -> bool:
    if current == target:
        return True
    # The safety sink is reachable from any non-terminal state.
    if target == TaskStatus.NEEDS_HUMAN and current not in TaskStatus.TERMINAL:
        return True
    return target in _TRANSITIONS.get(current, set())


def validate_transition(current: str, target: str) -> None:
    if target not in TaskStatus.ALL:
        raise InvalidTransition(f"unknown task status: {target!r}")
    if not can_transition(current, target):
        raise InvalidTransition(f"illegal transition: {current!r} -> {target!r}")
