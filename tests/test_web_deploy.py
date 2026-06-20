"""Tests for the web UI + the serverless/Vercel deployment plumbing.

All offline: the libSQL compat shim is validated against stdlib sqlite3 (so the
row/cursor/transaction behaviour is proven without the Rust-built driver), and
the app is exercised with Starlette's in-process TestClient.
"""

from __future__ import annotations

import os
import sqlite3

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
    # no 500 — a redirect carrying a clear error message
    assert r.status_code == 303
    assert "Could+not+add" in r.headers["location"]


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
