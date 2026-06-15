-- jobagent schema. Plain SQLite, fully inspectable (guardrail §2).
-- All DDL is idempotent (IF NOT EXISTS) so init can run any time.

-- The human's core facts. Exactly one row (id = 1). The agent may ONLY use
-- facts stored here; it must never invent profile facts (guardrail §2).
CREATE TABLE IF NOT EXISTS profile (
    id                       INTEGER PRIMARY KEY CHECK (id = 1),
    full_name                TEXT,
    email                    TEXT,
    phone                    TEXT,
    citizenship              TEXT,
    work_authorization       TEXT,
    location                 TEXT,
    languages                TEXT,   -- json array
    github_url               TEXT,
    linkedin_url             TEXT,
    portfolio_url            TEXT,
    salary_expectation_notes TEXT,   -- surfaced to human, never auto-submitted
    extra_facts              TEXT,   -- json object: any additional stored facts
    updated_at               TEXT
);

-- Human-authored resumes, one (or more) per track. The agent SELECTS one of
-- these; it never generates a resume. This removes the biggest source of error.
CREATE TABLE IF NOT EXISTS resume_variants (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    track      TEXT NOT NULL,        -- backend|frontend|devops|fullstack|data_scientist|data_analyst|...
    file_path  TEXT NOT NULL,
    notes      TEXT,
    updated_at TEXT
);

-- A library of cover letters. Either finished letters or Jinja2 templates with
-- {{ company }} / {{ role }} slots the agent may safely fill.
CREATE TABLE IF NOT EXISTS cover_letters (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    track       TEXT,
    is_template INTEGER NOT NULL DEFAULT 0,  -- 0/1 bool
    file_path   TEXT NOT NULL,
    updated_at  TEXT
);

-- The watchlist: a finite, human-defined set of company ATS boards (guardrail §2).
CREATE TABLE IF NOT EXISTS companies (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    ats_type    TEXT NOT NULL,       -- greenhouse|lever|workable|comeet|other
    board_token TEXT,                -- ATS slug (preferred for greenhouse/lever)
    board_url   TEXT,                -- explicit board URL (fallback / display)
    notes       TEXT,
    active      INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT,
    updated_at  TEXT,
    UNIQUE (name)
);

-- Discovered roles. Deduplicated by url.
CREATE TABLE IF NOT EXISTS jobs (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id       INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    external_id      TEXT,           -- the ATS's own job id
    title            TEXT NOT NULL,
    url              TEXT NOT NULL,
    ats_type         TEXT,
    location         TEXT,
    description_text TEXT,
    raw_payload_ref  TEXT,           -- path to cached raw payload (audit trail)
    date_found       TEXT,
    first_seen       TEXT,
    last_seen        TEXT,
    score            REAL,
    score_rationale  TEXT,
    suggested_track  TEXT,           -- best-matching track (set by scorer)
    status           TEXT NOT NULL DEFAULT 'discovered',
    updated_at       TEXT,
    UNIQUE (url)
);

-- A prepared application for a job. prefilled_data is what the agent could fill
-- safely from stored state; unanswered_fields is what the HUMAN must complete
-- (free-text screening, salary, anything not in state). The agent never guesses
-- the unanswered fields (guardrail §2).
CREATE TABLE IF NOT EXISTS applications (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id                INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    resume_variant_id     INTEGER REFERENCES resume_variants(id),
    cover_letter_id       INTEGER REFERENCES cover_letters(id),
    cover_letter_rendered TEXT,      -- path to the filled cover letter (if templated)
    prefilled_data        TEXT,      -- json object
    unanswered_fields     TEXT,      -- json array of {key,label,kind,reason,required}
    status                TEXT NOT NULL DEFAULT 'prepared',
    queued_at             TEXT,
    submitted_at          TEXT,
    follow_up_due         TEXT,
    notes                 TEXT,
    created_at            TEXT,
    updated_at            TEXT,
    UNIQUE (job_id)
);

-- Append-only audit log. Every state transition and side effect lands here.
CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,
    entity_type TEXT NOT NULL,       -- job|application|company|profile|pipeline|...
    entity_id   INTEGER,
    action      TEXT NOT NULL,
    detail      TEXT                 -- json object (optional)
);

CREATE INDEX IF NOT EXISTS idx_jobs_status      ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_company     ON jobs(company_id);
CREATE INDEX IF NOT EXISTS idx_apps_status      ON applications(status);
CREATE INDEX IF NOT EXISTS idx_apps_followup    ON applications(follow_up_due);
CREATE INDEX IF NOT EXISTS idx_events_entity    ON events(entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_events_ts        ON events(ts);
