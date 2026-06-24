-- Foreman ledger. Plain SQLite, inspectable (the same guarantee jobagent makes).
-- All DDL is idempotent so init can run any time.

-- One row per unit of work the agents develop.
CREATE TABLE IF NOT EXISTS tasks (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    source           TEXT,                       -- issue | manual | demo
    title            TEXT NOT NULL,
    description      TEXT,
    acceptance_notes TEXT,
    branch           TEXT,                        -- the isolated git branch (if any)
    worktree_path    TEXT,
    status           TEXT NOT NULL DEFAULT 'queued',
    attempts         INTEGER NOT NULL DEFAULT 0,
    last_error       TEXT,
    pr_ref           TEXT,                        -- branch:<name> or a PR URL
    created_at       TEXT,
    updated_at       TEXT
);

-- One row per model-agent invocation: the cost/latency ledger.
CREATE TABLE IF NOT EXISTS runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id       INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    role          TEXT,                            -- implementer | reviewer
    model         TEXT,
    turns         INTEGER NOT NULL DEFAULT 0,
    input_tokens  INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd      REAL NOT NULL DEFAULT 0,
    tests_passed  INTEGER,                         -- 0/1/null
    verdict       TEXT,
    started_at    TEXT,
    ended_at      TEXT
);

-- Append-only audit log: every status change and tool call lands here.
CREATE TABLE IF NOT EXISTS events (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      TEXT NOT NULL,
    task_id INTEGER,
    actor   TEXT,
    action  TEXT NOT NULL,
    detail  TEXT                                   -- json
);

CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_runs_task    ON runs(task_id);
CREATE INDEX IF NOT EXISTS idx_events_task  ON events(task_id);
CREATE INDEX IF NOT EXISTS idx_events_ts    ON events(ts);
