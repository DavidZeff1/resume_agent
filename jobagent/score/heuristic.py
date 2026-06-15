"""Deterministic, offline, explainable scorer (the default backend).

It scores a job by how well it matches the resume *tracks* the human actually
has, plus any skills listed in the profile, with mild seniority adjustment. No
network, no model — same input always yields the same score, which makes the
whole pipeline testable without an API key.
"""

from __future__ import annotations

import re
from typing import Mapping

from ..models import KNOWN_TRACKS
from .base import ScoreResult, Scorer

# Track-distinguishing keywords (kept low-false-positive on purpose).
TRACK_KEYWORDS: dict[str, list[str]] = {
    "backend": [
        "backend", "back-end", "api", "apis", "microservice", "distributed systems",
        "golang", "python", "node.js", "rust", "grpc", "rest", "postgres", "scalability",
        "server-side",
    ],
    "frontend": [
        "frontend", "front-end", "react", "vue", "angular", "typescript",
        "javascript", "css", "web app", "next.js", "ui engineer",
    ],
    "fullstack": [
        "full stack", "full-stack", "fullstack", "end-to-end", "react", "node.js",
    ],
    "devops": [
        "devops", "sre", "site reliability", "kubernetes", "terraform", "ci/cd",
        "infrastructure", "docker", "observability", "platform engineer", "aws",
    ],
    "data_scientist": [
        "data scientist", "data science", "machine learning", "statistics",
        "statistical", "pandas", "scikit", "experimentation", "a/b test",
        "predictive", "deep learning", "nlp", "causal",
    ],
    "data_analyst": [
        "data analyst", "analytics", "dashboard", "tableau", "looker", "power bi",
        "reporting", "business intelligence", "metrics", "sql",
    ],
    "ml_engineer": [
        "ml engineer", "machine learning engineer", "mlops", "model serving",
        "pytorch", "tensorflow", "feature store", "inference", "deep learning",
    ],
    "general": ["software engineer", "developer", "engineer"],
}

_NON_TECH_HINTS = (
    "sales", "account executive", "recruiter", "marketing", "customer success",
    "support", "accountant", "legal counsel", "office manager",
)
_SENIOR = ("senior", "staff", "principal", "lead", "sr.", "sr ")
_JUNIOR = ("intern", "internship", "junior", "entry level", "entry-level", "new grad")


class HeuristicScorer(Scorer):
    backend_name = "heuristic"

    def score(
        self, job: Mapping, profile: dict, available_tracks: list[str]
    ) -> ScoreResult:
        title = (job.get("title") or "").lower()
        text = f"{title}\n{(job.get('description_text') or '').lower()}"

        tracks = available_tracks or list(KNOWN_TRACKS)
        per_track: dict[str, list[str]] = {}
        per_track_score: dict[str, float] = {}
        for track in tracks:
            hits = _matched(TRACK_KEYWORDS.get(track, []), title, text)
            per_track[track] = hits
            # Title hits weigh double; saturate around ~5 weighted hits.
            weighted = 2 * sum(1 for h in hits if h in title) + len(hits)
            per_track_score[track] = min(1.0, weighted / 6.0)

        if per_track_score:
            best_track = max(per_track_score, key=per_track_score.get)
            base = per_track_score[best_track]
            matched = per_track[best_track]
        else:
            best_track, base, matched = None, 0.0, []

        score = base
        notes: list[str] = []

        # Profile skills add a small, capped bonus.
        skills = _profile_skills(profile)
        skill_hits = _matched(skills, title, text) if skills else []
        if skill_hits:
            score = min(1.0, score + 0.05 * len(skill_hits))
            notes.append(f"profile skills: {', '.join(skill_hits[:5])}")

        # Seniority alignment (mild).
        years = _years_experience(profile)
        if any(s in title for s in _SENIOR) and years is not None and years >= 5:
            score = min(1.0, score + 0.05)
            notes.append("seniority matches")
        if any(j in title for j in _JUNIOR) and years is not None and years >= 5:
            score = max(0.0, score - 0.15)
            notes.append("role looks junior vs. experience")

        # Obvious non-engineering roles get pushed down.
        if any(h in title for h in _NON_TECH_HINTS) and base < 0.34:
            score = min(score, 0.15)
            notes.append("looks non-technical")

        if best_track:
            rationale = f"best track '{best_track}'"
            if matched:
                rationale += f" via: {', '.join(matched[:6])}"
            else:
                rationale += " (no strong keyword match)"
        else:
            rationale = "no track keywords matched"
        if best_track and available_tracks and best_track not in available_tracks:
            rationale += " [no resume for this track]"
        if notes:
            rationale += "; " + "; ".join(notes)

        return ScoreResult(score=score, rationale=rationale, suggested_track=best_track).clamped()


def _matched(keywords, title: str, text: str) -> list[str]:
    found = []
    for kw in keywords:
        k = kw.lower()
        # word-ish boundary for short tokens to avoid e.g. 'sql' in 'mysql'
        if len(k) <= 4 and " " not in k:
            if re.search(rf"(?<![a-z]){re.escape(k)}(?![a-z])", text):
                found.append(kw)
        elif k in text:
            found.append(kw)
    return found


def _profile_skills(profile: dict) -> list[str]:
    facts = profile.get("extra_facts") or {}
    raw = facts.get("skills")
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(s).strip() for s in raw if str(s).strip()]
    return [s.strip() for s in str(raw).split(",") if s.strip()]


def _years_experience(profile: dict):
    facts = profile.get("extra_facts") or {}
    for key in ("years_experience", "years", "yoe"):
        if key in facts:
            try:
                return float(facts[key])
            except (TypeError, ValueError):
                return None
    return None
