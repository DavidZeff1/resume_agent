"""Phase 0/1: DB init, transactions, and state CRUD."""

import pytest

from jobagent.db import transaction
from jobagent.state import companies as companies_state
from jobagent.state import cover_letters as cover_state
from jobagent.state import profile as profile_state
from jobagent.state import resumes as resume_state


def test_schema_tables_exist(ctx):
    tables = {
        r["name"]
        for r in ctx.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    for t in ("profile", "resume_variants", "cover_letters", "companies", "jobs",
              "applications", "events"):
        assert t in tables


def test_transaction_rolls_back(ctx):
    companies_state.add_company(ctx.conn, "Keep", "lever", board_token="keep")
    with pytest.raises(RuntimeError):
        with transaction(ctx.conn):
            ctx.conn.execute(
                "INSERT INTO companies (name, ats_type) VALUES ('Rollback','lever')"
            )
            raise RuntimeError("boom")
    names = {c["name"] for c in companies_state.list_companies(ctx.conn)}
    assert "Keep" in names and "Rollback" not in names


def test_profile_partial_update_and_languages(ctx):
    profile_state.set_profile(ctx.conn, full_name="Ada", email="a@x.io")
    profile_state.set_profile(ctx.conn, location="Remote", languages=["English", "Hebrew"])
    p = profile_state.get_profile(ctx.conn)
    assert p["full_name"] == "Ada"          # preserved across partial update
    assert p["location"] == "Remote"
    assert p["languages"] == ["English", "Hebrew"]


def test_profile_extra_facts_merge(ctx):
    profile_state.set_profile(ctx.conn, extra_facts={"skills": "python"})
    profile_state.set_profile(ctx.conn, extra_facts={"yoe": "6"})
    p = profile_state.get_profile(ctx.conn)
    assert p["extra_facts"] == {"skills": "python", "yoe": "6"}


def test_profile_rejects_unknown_field(ctx):
    with pytest.raises(ValueError):
        profile_state.set_profile(ctx.conn, nonsense="x")


def test_missing_core_fields(ctx):
    assert "full_name" in profile_state.missing_core_fields(ctx.conn)
    profile_state.set_profile(
        ctx.conn, full_name="A", email="a@x.io",
        work_authorization="citizen", location="Remote",
    )
    assert profile_state.missing_core_fields(ctx.conn) == []


def test_company_upsert_by_name(ctx):
    a = companies_state.add_company(ctx.conn, "Acme", "greenhouse", board_token="acme")
    b = companies_state.add_company(ctx.conn, "Acme", "lever", board_token="acme2")
    assert a == b  # same row updated
    rows = companies_state.list_companies(ctx.conn)
    assert len(rows) == 1 and rows[0]["ats_type"] == "lever"


def test_company_requires_token_or_url(ctx):
    with pytest.raises(ValueError):
        companies_state.add_company(ctx.conn, "NoBoard", "greenhouse")


def test_resume_and_cover_crud(ctx, tmp_path):
    f = tmp_path / "r.txt"
    f.write_text("x")
    rid = resume_state.add_resume(ctx.conn, "Backend", str(f), notes="primary")
    assert resume_state.get_resume(ctx.conn, rid)["track"] == "backend"  # normalized
    cid = cover_state.add_cover_letter(ctx.conn, "C", str(f), is_template=True)
    assert cover_state.get_cover_letter(ctx.conn, cid)["is_template"] == 1
    resume_state.delete_resume(ctx.conn, rid)
    assert resume_state.get_resume(ctx.conn, rid) is None
