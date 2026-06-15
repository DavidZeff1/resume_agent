"""Phase 3: heuristic scoring + backend selection."""

from jobagent.score.base import get_scorer
from jobagent.score.heuristic import HeuristicScorer


def _job(title, desc=""):
    return {"title": title, "description_text": desc}


def test_heuristic_is_deterministic():
    s = HeuristicScorer()
    profile = {"extra_facts": {"skills": "python,sql"}}
    a = s.score(_job("Backend Engineer", "Python APIs and distributed systems"), profile, ["backend"])
    b = s.score(_job("Backend Engineer", "Python APIs and distributed systems"), profile, ["backend"])
    assert a.score == b.score
    assert a.suggested_track == "backend"


def test_strong_backend_role_scores_high():
    s = HeuristicScorer()
    res = s.score(
        _job("Senior Backend Engineer", "Design REST APIs, microservices, postgres, scalability"),
        {"extra_facts": {"skills": "python", "years_experience": "6"}},
        ["backend"],
    )
    assert res.score >= 0.6
    assert res.suggested_track == "backend"


def test_non_technical_role_scores_low():
    s = HeuristicScorer()
    res = s.score(
        _job("Enterprise Account Executive", "Drive sales revenue and quota attainment"),
        {"extra_facts": {}},
        ["backend"],
    )
    assert res.score < 0.6


def test_get_scorer_defaults_to_heuristic_without_key(ctx, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    ctx.config.scoring.backend = "auto"
    assert get_scorer(ctx).backend_name == "heuristic"


def test_get_scorer_claude_without_key_raises(ctx, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    ctx.config.scoring.backend = "claude"
    import pytest

    with pytest.raises(RuntimeError):
        get_scorer(ctx)
