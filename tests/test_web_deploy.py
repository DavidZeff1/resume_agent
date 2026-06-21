"""Tests for the web UI + the serverless/Vercel deployment plumbing.

All offline: the libSQL compat shim is validated against stdlib sqlite3 (so the
row/cursor/transaction behaviour is proven without the Rust-built driver), and
the app is exercised with Starlette's in-process TestClient.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from jobagent import db
from jobagent.config import Config
from jobagent.context import AgentContext
from jobagent.db import _LibsqlConn, _Row
from jobagent.state import companies as companies_state
from jobagent.web.app import create_app


# --------------------------------------------------------------------------- #
# libSQL compatibility shim (drives the hosted DB path)
# --------------------------------------------------------------------------- #
def test_libsql_shim_matches_sqlite3_idioms():
    raw = sqlite3.connect(":memory:", isolation_level=None)
    conn = _LibsqlConn(raw)

    conn.executescript(
        "CREATE TABLE t (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, n INTEGER);"
    )

    # explicit BEGIN/COMMIT (exactly what db.transaction() emits)
    conn.execute("BEGIN")
    cur = conn.execute("INSERT INTO t (name, n) VALUES (?, ?)", ("alice", 5))
    assert cur.lastrowid == 1
    conn.execute("INSERT INTO t (name, n) VALUES (?, ?)", ("bob", 9))
    conn.execute("COMMIT")

    # name + positional access and dict(row), as the codebase relies on
    r = conn.execute("SELECT * FROM t WHERE name = ?", ("alice",)).fetchone()
    assert r["name"] == "alice"
    assert r[0] == 1
    assert dict(r) == {"id": 1, "name": "alice", "n": 5}

    # fetchall + cursor iteration
    assert [x["name"] for x in conn.execute("SELECT name FROM t ORDER BY id").fetchall()] == [
        "alice",
        "bob",
    ]
    assert [x["name"] for x in conn.execute("SELECT name FROM t ORDER BY id")] == [
        "alice",
        "bob",
    ]
    assert conn.execute("SELECT COUNT(*) n FROM t").fetchone()["n"] == 2


def test_row_supports_keys_and_missing_column():
    row = _Row(["a", "b"], [1, 2])
    assert row["a"] == 1 and row["b"] == 2 and row[0] == 1
    assert set(row.keys()) == {"a", "b"}
    with pytest.raises(ValueError):
        _ = row["nope"]


# --------------------------------------------------------------------------- #
# DB driver switch (env-gated)
# --------------------------------------------------------------------------- #
def test_db_url_env_selects_libsql_and_errors_clearly(monkeypatch, tmp_path):
    monkeypatch.setenv("JOBAGENT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("JOBAGENT_DB_URL", "libsql://example.turso.io")
    config = Config.load()
    # libsql-experimental isn't installed in CI/local — we should get a clear,
    # actionable error rather than a cryptic one.
    with pytest.raises(RuntimeError, match="libsql"):
        db.connect(config)


def test_default_path_is_local_sqlite(monkeypatch, tmp_path):
    monkeypatch.setenv("JOBAGENT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("JOBAGENT_DB_URL", raising=False)
    conn = db.connect(Config.load())
    try:
        assert conn.execute("SELECT 1").fetchone()[0] == 1
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# Web app: health, auth, serverless behaviour
# --------------------------------------------------------------------------- #
@pytest.fixture
def isolated_env(monkeypatch, tmp_path):
    """Point the app's per-request contexts at a throwaway DB."""
    monkeypatch.setenv("JOBAGENT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("JOBAGENT_DB_URL", raising=False)
    monkeypatch.delenv("VERCEL", raising=False)
    monkeypatch.delenv("JOBAGENT_WEB_PASSWORD", raising=False)
    yield


def test_healthz_reports_ok_and_driver(isolated_env):
    client = TestClient(create_app())
    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["driver"] == "sqlite3 (local file)"
    assert body["python"]
    assert body["serverless"] is False
    # the probe also verifies templates are present (catches a Vercel bundle miss)
    assert body["templates_ok"] is True


def test_custom_404_page(isolated_env):
    client = TestClient(create_app())
    r = client.get("/nope-not-here")
    assert r.status_code == 404
    assert "Not found" in r.text
    assert "/healthz" in r.text  # points the user at diagnostics


def test_company_add_and_remove_via_ui(isolated_env):
    client = TestClient(create_app())

    # use a name that can't collide with any template placeholder text
    name = "Wonkavator Labs"

    # add
    r = client.post(
        "/companies/add",
        data={"name": name, "ats": "greenhouse", "token": "wonkavator"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert name in client.get("/companies").text

    # remove
    with AgentContext.create(overrides={"paths": {"data_dir": os.environ["JOBAGENT_DATA_DIR"]}}) as ctx:
        cid = companies_state.list_companies(ctx.conn)[0]["id"]
    r = client.post(f"/companies/{cid}/delete", follow_redirects=False)
    assert r.status_code == 303
    assert name not in client.get("/companies").text


def test_company_add_invalid_ats_is_graceful(isolated_env):
    client = TestClient(create_app())
    r = client.post(
        "/companies/add",
        data={"name": "Bad", "ats": "not-a-real-ats", "token": "x"},
        follow_redirects=False,
    )
    # no 500 — the post_action guard turns the ValueError into a graceful
    # redirect back to /companies whose flash names what went wrong.
    assert r.status_code == 303
    loc = r.headers["location"]
    assert loc.startswith("/companies?msg=")
    assert "ats_type" in loc


def test_auth_gate_when_password_set(isolated_env, monkeypatch):
    monkeypatch.setenv("JOBAGENT_WEB_PASSWORD", "s3cret")
    monkeypatch.setenv("JOBAGENT_WEB_USER", "dave")
    client = TestClient(create_app())

    assert client.get("/").status_code == 401
    assert client.get("/", auth=("dave", "wrong")).status_code == 401
    assert client.get("/", auth=("dave", "s3cret")).status_code == 200
    # health + favicon stay open so a broken deploy is diagnosable
    assert client.get("/healthz").status_code == 200
    assert client.get("/favicon.ico").status_code == 204


def test_dashboard_warns_on_serverless_without_db(isolated_env, monkeypatch):
    monkeypatch.setenv("VERCEL", "1")  # simulate the serverless host, no DB url
    client = TestClient(create_app())
    html = client.get("/").text
    assert "will NOT persist" in html
    # the live-source checkbox is hidden on serverless
    assert "source live boards" not in html


# --------------------------------------------------------------------------- #
# Full workflow: demo seed -> prepare/skip -> submit -> outcome -> materials
# --------------------------------------------------------------------------- #
def _ctx():
    """A context against the isolated_env DB (same env every handler reads)."""
    return AgentContext.create(
        overrides={"paths": {"data_dir": os.environ["JOBAGENT_DATA_DIR"]}}
    )


def _seed_demo(client):
    assert client.post("/demo", follow_redirects=False).status_code == 303


def test_every_page_renders_after_demo(isolated_env):
    """The whole UI is reachable and 200s once there's real data behind it."""
    client = TestClient(create_app())
    _seed_demo(client)
    for path in ("/", "/jobs", "/review", "/tracker", "/materials", "/companies",
                 "/profile", "/guide"):
        assert client.get(path).status_code == 200, path
    assert client.get("/favicon.ico").status_code == 204


def test_guide_renders_without_any_data(isolated_env):
    """The help page is DB-free, so it works on a brand-new (empty) install and
    states the core guardrail."""
    client = TestClient(create_app())
    r = client.get("/guide")
    assert r.status_code == 200
    assert "How to use jobagent" in r.text
    assert "never submit" in r.text  # the guardrail is spelled out
    assert 'href="/guide"' in client.get("/").text  # linked from the nav


def test_job_prepare_override_and_skip(isolated_env):
    client = TestClient(create_app())
    _seed_demo(client)
    with _ctx() as ctx:
        skipped = ctx.conn.execute(
            "SELECT id FROM jobs WHERE status='skipped' ORDER BY id LIMIT 1"
        ).fetchone()["id"]
        queued = ctx.conn.execute(
            "SELECT id FROM jobs WHERE status='queued_for_review' ORDER BY id LIMIT 1"
        ).fetchone()["id"]

    # Prepare overrides the scorer: a skipped job is shortlisted -> tailored ->
    # prepped, landing in the review queue.
    r = client.post(f"/jobs/{skipped}/prepare", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith("/review")
    with _ctx() as ctx:
        assert ctx.conn.execute(
            "SELECT status FROM jobs WHERE id=?", (skipped,)
        ).fetchone()["status"] == "queued_for_review"

    # Skip takes a job out of consideration.
    r = client.post(f"/jobs/{queued}/skip", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith("/jobs")
    with _ctx() as ctx:
        assert ctx.conn.execute(
            "SELECT status FROM jobs WHERE id=?", (queued,)
        ).fetchone()["status"] == "skipped"


def test_tracker_submit_and_record_outcome(isolated_env):
    client = TestClient(create_app())
    _seed_demo(client)
    with _ctx() as ctx:
        app_id = ctx.conn.execute(
            "SELECT id FROM applications WHERE status='queued_for_review' ORDER BY id LIMIT 1"
        ).fetchone()["id"]

    # The HUMAN records they submitted on the real site (the agent never submits).
    r = client.post(f"/review/{app_id}/submit", follow_redirects=False)
    assert r.status_code == 303
    # It now shows up on the tracker, where outcomes get recorded.
    assert client.get("/tracker").status_code == 200
    r = client.post(
        f"/tracker/{app_id}/outcome", data={"status": "interview"}, follow_redirects=False
    )
    assert r.status_code == 303
    with _ctx() as ctx:
        job_id = ctx.conn.execute(
            "SELECT job_id FROM applications WHERE id=?", (app_id,)
        ).fetchone()["job_id"]
        assert ctx.conn.execute(
            "SELECT status FROM jobs WHERE id=?", (job_id,)
        ).fetchone()["status"] == "interview"


def test_outcome_bad_status_and_missing_app_are_graceful(isolated_env):
    client = TestClient(create_app())
    # nonexistent app + bogus status: must redirect, never 500
    r = client.post(
        "/tracker/999/outcome", data={"status": "not-a-status"}, follow_redirects=False
    )
    assert r.status_code == 303
    assert r.headers["location"].startswith("/tracker")


def test_prepare_nonexistent_job_is_graceful(isolated_env):
    client = TestClient(create_app())
    r = client.post("/jobs/999/prepare", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith("/jobs")


def test_materials_add_resume_by_path_and_delete(isolated_env):
    client = TestClient(create_app())
    r = client.post(
        "/materials/resume/add",
        data={"track": "platform", "path": "/tmp/some-resume.txt"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "platform" in client.get("/materials").text
    with _ctx() as ctx:
        rid = ctx.conn.execute(
            "SELECT id FROM resume_variants ORDER BY id LIMIT 1"
        ).fetchone()["id"]
    r = client.post(f"/materials/resume/{rid}/delete", follow_redirects=False)
    assert r.status_code == 303
    assert "platform" not in client.get("/materials").text


def test_materials_resume_needs_track_and_a_source(isolated_env):
    client = TestClient(create_app())
    # missing track -> graceful redirect
    r = client.post(
        "/materials/resume/add", data={"path": "/tmp/x.txt"}, follow_redirects=False
    )
    assert r.status_code == 303 and r.headers["location"].startswith("/materials")
    # track present but no file and no path -> graceful redirect, no 500
    r = client.post(
        "/materials/resume/add", data={"track": "backend"}, follow_redirects=False
    )
    assert r.status_code == 303 and r.headers["location"].startswith("/materials")
    with _ctx() as ctx:
        assert ctx.conn.execute("SELECT COUNT(*) n FROM resume_variants").fetchone()["n"] == 0


def test_materials_add_cover_and_delete(isolated_env):
    client = TestClient(create_app())
    r = client.post(
        "/materials/cover/add",
        data={"name": "Zeta Letter", "path": "/tmp/zeta.txt.j2", "template": "on"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "Zeta Letter" in client.get("/materials").text
    with _ctx() as ctx:
        cid = ctx.conn.execute(
            "SELECT id FROM cover_letters ORDER BY id LIMIT 1"
        ).fetchone()["id"]
    r = client.post(f"/materials/cover/{cid}/delete", follow_redirects=False)
    assert r.status_code == 303
    assert "Zeta Letter" not in client.get("/materials").text


def test_materials_resume_file_upload_is_saved(isolated_env):
    """The multipart upload branch actually writes the file under the data dir,
    so the review page can read it back (it isn't just a dangling path ref)."""
    client = TestClient(create_app())
    r = client.post(
        "/materials/resume/add",
        data={"track": "uploaded"},
        files={"file": ("my_resume.txt", b"resume bytes", "text/plain")},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "uploaded" in client.get("/materials").text
    with _ctx() as ctx:
        saved = ctx.conn.execute(
            "SELECT file_path FROM resume_variants WHERE track='uploaded'"
        ).fetchone()["file_path"]
    assert Path(saved).exists()
    assert Path(saved).read_bytes() == b"resume bytes"
