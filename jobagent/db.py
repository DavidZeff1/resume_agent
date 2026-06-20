"""SQLite access: connection setup, schema init, and a transaction helper.

The DB is a single plain file (guardrail §2: inspectable, editable). We enable
foreign keys and WAL so concurrent reads during a long pipeline run are safe.
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .config import Config

_SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def connect(config: Config) -> sqlite3.Connection:
    """Open a connection to the configured database.

    Default (and all local/CLI use): a tuned connection to the local SQLite
    file. If ``JOBAGENT_DB_URL`` is set (a Turso/libSQL ``libsql://…`` URL), we
    instead connect to that remote database — this is what makes the app work
    on an ephemeral filesystem like Vercel, where a local SQLite file would not
    persist between requests. See :func:`_connect_libsql`.
    """
    config.paths.data_dir.mkdir(parents=True, exist_ok=True)

    db_url = os.environ.get("JOBAGENT_DB_URL")
    if db_url:
        return _connect_libsql(config, db_url)

    conn = sqlite3.connect(config.paths.db_path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def _connect_libsql(config: Config, db_url: str):
    """Connect to a Turso/libSQL database as a stdlib-sqlite3 work-alike.

    Uses an *embedded replica*: a local SQLite file (in the writable data dir,
    e.g. ``/tmp`` on Vercel) kept in sync with the remote primary. Reads are
    served locally; writes are forwarded to the primary. We sync on connect so
    each request sees fresh data. The returned object is wrapped so the rest of
    the codebase keeps using ``row["col"]`` / ``dict(row)`` / ``.lastrowid`` /
    explicit ``BEGIN/COMMIT`` exactly as with stdlib sqlite3.

    NOTE: this path requires the ``libsql-experimental`` package (prebuilt
    wheels exist for CPython 3.9–3.12 on Linux, which is what Vercel runs).
    """
    try:
        import libsql_experimental as libsql  # type: ignore
    except ImportError as exc:  # pragma: no cover - only hit in the hosted path
        raise RuntimeError(
            "JOBAGENT_DB_URL is set but 'libsql-experimental' could not be "
            "imported. On Vercel this almost always means the build used a "
            "Python version with no prebuilt wheel — pin Python 3.12. Locally: "
            "pip install libsql-experimental."
        ) from exc

    auth_token = os.environ.get("JOBAGENT_DB_AUTH_TOKEN")
    if not auth_token:
        # Turso databases are private; a missing token yields cryptic auth
        # failures later, so fail loudly and early instead.
        raise RuntimeError(
            "JOBAGENT_DB_URL is set but JOBAGENT_DB_AUTH_TOKEN is missing. "
            "Create one with: turso db tokens create <db>."
        )

    config.paths.data_dir.mkdir(parents=True, exist_ok=True)
    replica_path = str(config.paths.data_dir / "replica.db")
    raw = libsql.connect(replica_path, sync_url=db_url, auth_token=auth_token)
    try:
        raw.sync()  # pull the latest from the primary before serving reads
    except Exception:
        # First-ever connect against an empty primary may have nothing to pull;
        # init_db will create the schema locally and the replica forwards it.
        pass
    return _LibsqlConn(raw)


def db_info(config: Config) -> dict:
    """A connectivity self-check for the /healthz endpoint.

    Reports which driver is active and whether a trivial query succeeds, without
    raising — so the health page can render the error text instead of 500-ing.
    """
    import sys

    using_libsql = bool(os.environ.get("JOBAGENT_DB_URL"))
    info: dict = {
        "driver": "libsql/turso" if using_libsql else "sqlite3 (local file)",
        "python": sys.version.split()[0],
        "data_dir": str(config.paths.data_dir),
        "ephemeral": (not using_libsql) and str(config.paths.data_dir).startswith("/tmp"),
        "ok": False,
    }
    try:
        conn = get_conn(config)
        try:
            conn.execute("SELECT 1").fetchone()
            info["ok"] = True
        finally:
            conn.close()
    except Exception as exc:
        info["error"] = f"{type(exc).__name__}: {exc}"
    return info


# --------------------------------------------------------------------------- #
# libSQL compatibility shim
#
# Makes a libSQL connection behave like the stdlib sqlite3 connection the rest
# of the codebase expects: rows support both ``row[i]`` and ``row["col"]`` and
# ``dict(row)``; ``execute`` returns a cursor exposing ``fetchone/fetchall`` and
# ``lastrowid`` and is iterable. The shim is intentionally tiny and only used on
# the hosted path — the local sqlite3 path above is untouched.
# --------------------------------------------------------------------------- #
class _Row:
    """A sqlite3.Row work-alike: int- and name-indexable, dict()-able."""

    __slots__ = ("_cols", "_vals")

    def __init__(self, cols: list[str], vals: list):
        self._cols = cols
        self._vals = vals

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._vals[key]
        return self._vals[self._cols.index(key)]

    def keys(self):
        return list(self._cols)

    def __iter__(self):
        return iter(self._vals)

    def __len__(self):
        return len(self._vals)


class _Cursor:
    def __init__(self, cur):
        self._cur = cur

    def _cols(self) -> list[str]:
        desc = self._cur.description
        return [d[0] for d in desc] if desc else []

    def fetchone(self):
        row = self._cur.fetchone()
        if row is None:
            return None
        return _Row(self._cols(), list(row))

    def fetchall(self):
        cols = self._cols()
        return [_Row(cols, list(r)) for r in self._cur.fetchall()]

    def __iter__(self):
        return iter(self.fetchall())

    @property
    def lastrowid(self):
        return self._cur.lastrowid

    @property
    def rowcount(self):
        return getattr(self._cur, "rowcount", -1)


class _LibsqlConn:
    def __init__(self, raw):
        self._raw = raw

    def execute(self, sql: str, params: tuple = ()):  # noqa: D401
        return _Cursor(self._raw.execute(sql, params))

    def executescript(self, script: str):
        # libSQL's executescript exists; fall back to statement-splitting if not.
        if hasattr(self._raw, "executescript"):
            return self._raw.executescript(script)
        for stmt in (s.strip() for s in script.split(";")):
            if stmt:
                self._raw.execute(stmt)
        return None

    def commit(self):
        return self._raw.commit()

    def close(self):
        return self._raw.close()


def init_db(conn: sqlite3.Connection) -> None:
    """Create all tables/indexes if absent (idempotent)."""
    conn.executescript(_SCHEMA_PATH.read_text(encoding="utf-8"))


def get_conn(config: Config) -> sqlite3.Connection:
    """Connect and ensure the schema exists — the common entry point."""
    conn = connect(config)
    init_db(conn)
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Run a block atomically. Commits on success, rolls back on error.

    We open connections in autocommit mode (isolation_level=None) and manage
    explicit BEGIN/COMMIT here so multi-write operations are all-or-nothing.
    """
    conn.execute("BEGIN")
    try:
        yield conn
    except Exception:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")
