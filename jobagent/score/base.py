"""Scorer interface + the result type, and backend selection."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Mapping


@dataclass
class ScoreResult:
    score: float                  # 0.0 - 1.0
    rationale: str                # short human-readable explanation
    suggested_track: str | None   # best-matching resume track

    def clamped(self) -> "ScoreResult":
        self.score = max(0.0, min(1.0, float(self.score)))
        return self


class Scorer(ABC):
    backend_name: str = "base"

    @abstractmethod
    def score(
        self, job: Mapping, profile: dict, available_tracks: list[str]
    ) -> ScoreResult:
        ...


def get_scorer(ctx) -> Scorer:
    """Select a scorer from config + availability.

    backend = auto      -> Claude if a key+SDK are present, else heuristic
    backend = heuristic -> always the deterministic offline scorer
    backend = claude    -> require Claude (raises if unavailable)
    """
    from ..logging_setup import get_logger
    from .heuristic import HeuristicScorer

    log = get_logger("score")
    backend = ctx.config.scoring.backend.lower()

    if backend == "heuristic":
        return HeuristicScorer()

    if backend in ("auto", "claude"):
        if ctx.config.has_llm:
            from .claude import ClaudeScorer

            return ClaudeScorer(model=ctx.config.scoring.model)
        if backend == "claude":
            raise RuntimeError(
                "scoring.backend='claude' but ANTHROPIC_API_KEY / anthropic SDK "
                "is not available. Set the key or use backend: auto|heuristic."
            )
        log.info("No LLM available; using the deterministic heuristic scorer.")
        return HeuristicScorer()

    raise ValueError(f"unknown scoring backend: {backend!r}")
