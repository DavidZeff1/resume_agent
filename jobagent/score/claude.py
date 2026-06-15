"""Optional Claude-backed scorer (used when ANTHROPIC_API_KEY + SDK present).

The profile + instructions go in a cached system block (stable across all jobs
in a run), and only the job varies per call, so repeated scoring in one run hits
the prompt cache. The model only *scores fit* — it is never asked to invent
candidate facts (guardrail §2).
"""

from __future__ import annotations

import json
import re
from typing import Mapping

from ..logging_setup import get_logger
from ..util import truncate
from .base import ScoreResult, Scorer

log = get_logger("score.claude")

_INSTRUCTIONS = """\
You score how well ONE job posting fits a candidate, to decide whether to
prepare an application. Reply with ONLY a JSON object, no prose:

  {"score": <float 0..1>, "suggested_track": <string or null>, "rationale": <string, <=2 sentences>}

Scoring guidance:
- score = fit between the job and the candidate's available resume tracks,
  stated skills, seniority, and constraints (work authorization, location).
- Calibrate: >=0.8 strong fit, ~0.5 borderline, <0.3 poor fit / wrong field.
- suggested_track MUST be one of the candidate's available tracks (or null if
  none fit).
- Never invent facts about the candidate; score only on what is provided.
"""


def _profile_block(profile: dict, available_tracks: list[str]) -> str:
    keep = (
        "full_name", "location", "work_authorization", "citizenship",
        "languages", "salary_expectation_notes",
    )
    lines = ["CANDIDATE PROFILE:"]
    for k in keep:
        v = profile.get(k)
        if v:
            lines.append(f"  {k}: {v}")
    facts = profile.get("extra_facts") or {}
    for k, v in facts.items():
        lines.append(f"  {k}: {v}")
    lines.append(f"AVAILABLE RESUME TRACKS: {', '.join(available_tracks) or '(none)'}")
    return "\n".join(lines)


class ClaudeScorer(Scorer):
    backend_name = "claude"

    def __init__(self, model: str, client=None) -> None:
        self.model = model
        if client is None:
            import anthropic

            client = anthropic.Anthropic()
        self._client = client

    def score(
        self, job: Mapping, profile: dict, available_tracks: list[str]
    ) -> ScoreResult:
        system = [
            {
                "type": "text",
                "text": _INSTRUCTIONS + "\n\n" + _profile_block(profile, available_tracks),
                "cache_control": {"type": "ephemeral"},
            }
        ]
        user = (
            f"JOB TITLE: {job.get('title')}\n"
            f"LOCATION: {job.get('location') or 'n/a'}\n\n"
            f"DESCRIPTION:\n{truncate(job.get('description_text'), 6000)}"
        )
        msg = self._client.messages.create(
            model=self.model,
            max_tokens=400,
            temperature=0,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        text = "".join(b.text for b in msg.content if getattr(b, "type", None) == "text")
        data = _parse_json(text)
        return ScoreResult(
            score=float(data.get("score", 0.0)),
            rationale=str(data.get("rationale", "")).strip()[:500] or "(no rationale)",
            suggested_track=(data.get("suggested_track") or None),
        ).clamped()


def _parse_json(text: str) -> dict:
    """Extract the first JSON object from the model's reply, tolerantly."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
    log.warning("could not parse model JSON; defaulting to neutral score. Got: %s", text[:200])
    return {"score": 0.5, "suggested_track": None, "rationale": "unparseable model output"}
