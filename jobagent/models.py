"""Status constants and the job-status state machine.

The state machine from the build plan (§4) is enforced here: transitions go
through ``next_job_status`` / ``validate_transition`` so illegal jumps raise
instead of silently corrupting state.

    discovered -> scored -> (skipped | shortlisted)
    shortlisted -> prepared -> queued_for_review
    queued_for_review -> submitted              (HUMAN action only)
    submitted -> (no_response | rejected | interview | offer)

`skipped` is terminal-but-revisitable (it may go back to scored on re-score).
"""

from __future__ import annotations


class JobStatus:
    DISCOVERED = "discovered"
    SCORED = "scored"
    SKIPPED = "skipped"
    SHORTLISTED = "shortlisted"
    PREPARED = "prepared"
    QUEUED_FOR_REVIEW = "queued_for_review"
    SUBMITTED = "submitted"
    NO_RESPONSE = "no_response"
    REJECTED = "rejected"
    INTERVIEW = "interview"
    OFFER = "offer"

    ALL = (
        DISCOVERED, SCORED, SKIPPED, SHORTLISTED, PREPARED,
        QUEUED_FOR_REVIEW, SUBMITTED, NO_RESPONSE, REJECTED, INTERVIEW, OFFER,
    )
    # Post-submission outcome statuses the human/tracker may set.
    OUTCOMES = (NO_RESPONSE, REJECTED, INTERVIEW, OFFER)


class ApplicationStatus:
    PREPARED = "prepared"
    QUEUED_FOR_REVIEW = "queued_for_review"
    SUBMITTED = "submitted"
    WITHDRAWN = "withdrawn"

    ALL = (PREPARED, QUEUED_FOR_REVIEW, SUBMITTED, WITHDRAWN)


# Allowed job-status transitions. Anything not listed is rejected.
_JOB_TRANSITIONS: dict[str, set[str]] = {
    JobStatus.DISCOVERED: {JobStatus.SCORED},
    JobStatus.SCORED: {JobStatus.SKIPPED, JobStatus.SHORTLISTED},
    JobStatus.SKIPPED: {JobStatus.SCORED, JobStatus.SHORTLISTED},  # revisitable
    JobStatus.SHORTLISTED: {JobStatus.PREPARED, JobStatus.SKIPPED},
    JobStatus.PREPARED: {JobStatus.QUEUED_FOR_REVIEW, JobStatus.SKIPPED},
    JobStatus.QUEUED_FOR_REVIEW: {JobStatus.SUBMITTED, JobStatus.SKIPPED},
    JobStatus.SUBMITTED: set(JobStatus.OUTCOMES),
    # Outcome statuses can move between each other as news arrives.
    JobStatus.NO_RESPONSE: {JobStatus.REJECTED, JobStatus.INTERVIEW, JobStatus.OFFER},
    JobStatus.INTERVIEW: {JobStatus.REJECTED, JobStatus.OFFER, JobStatus.NO_RESPONSE},
    JobStatus.REJECTED: set(),
    JobStatus.OFFER: {JobStatus.REJECTED},  # offer can still fall through
}


class InvalidTransition(ValueError):
    """Raised when an illegal job-status transition is attempted."""


def can_transition(current: str, target: str) -> bool:
    if current == target:
        return True
    return target in _JOB_TRANSITIONS.get(current, set())


def validate_transition(current: str, target: str) -> None:
    if target not in JobStatus.ALL:
        raise InvalidTransition(f"Unknown job status: {target!r}")
    if not can_transition(current, target):
        raise InvalidTransition(
            f"Illegal job-status transition: {current!r} -> {target!r}"
        )


# Tracks the resume library may be organized by (informational; not enforced).
KNOWN_TRACKS = (
    "backend", "frontend", "fullstack", "devops",
    "data_scientist", "data_analyst", "ml_engineer", "general",
)
