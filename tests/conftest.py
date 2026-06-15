"""Shared pytest fixtures + helpers (all offline; no network, no API key)."""

from __future__ import annotations

from pathlib import Path

import pytest

from jobagent.context import AgentContext
from jobagent.repo import upsert_job
from jobagent.state import companies as companies_state
from jobagent.state import cover_letters as cover_state
from jobagent.state import profile as profile_state
from jobagent.state import resumes as resume_state


@pytest.fixture
def ctx(tmp_path):
    c = AgentContext.create(overrides={"paths": {"data_dir": str(tmp_path / "data")}})
    yield c
    c.close()


class FakeClient:
    """Stand-in for PoliteClient for adapter tests."""

    def __init__(self, json_payload=None, text_payload=None):
        self._json = json_payload
        self._text = text_payload
        self.calls = []

    def get_json(self, url, sanctioned_api=True, force=False):
        self.calls.append(("json", url, sanctioned_api))
        return self._json

    def get(self, url, sanctioned_api=False, force=False):
        self.calls.append(("get", url, sanctioned_api))

        class _R:
            text = self._text

        return _R()


@pytest.fixture
def fake_client():
    return FakeClient


def seed_min_state(ctx, tmp_path: Path) -> int:
    """Profile + a backend resume + a cover template + one company. Returns company id."""
    resume_file = tmp_path / "resume_backend.txt"
    resume_file.write_text("backend resume placeholder", encoding="utf-8")
    cover_file = tmp_path / "cover.txt.j2"
    cover_file.write_text(
        "Dear {{ company }} team, I'm applying for {{ role }}. — {{ applicant_name }}\n",
        encoding="utf-8",
    )
    profile_state.set_profile(
        ctx.conn,
        full_name="Test User",
        email="test@example.com",
        work_authorization="Dual US-Israeli citizen",
        location="Remote",
        extra_facts={"skills": "python,sql,api design", "years_experience": "6"},
    )
    resume_state.add_resume(ctx.conn, "backend", str(resume_file))
    cover_state.add_cover_letter(ctx.conn, "Tmpl", str(cover_file), track="backend", is_template=True)
    return companies_state.add_company(
        ctx.conn, name="Acme", ats_type="lever", board_token="acme"
    )


def seed_job(ctx, company_id: int, title: str, description: str, url: str | None = None) -> int:
    job_id, _ = upsert_job(
        ctx.conn,
        company_id,
        title=title,
        url=url or f"https://jobs.lever.co/acme/{title.replace(' ', '-').lower()}",
        ats_type="lever",
        description_text=description,
    )
    return job_id
