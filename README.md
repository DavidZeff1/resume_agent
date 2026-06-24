# jobagent

A **local, persistent agent that prepares job applications** — sourcing roles,
scoring them, selecting your materials, pre-filling forms, tracking status, and
drafting follow-ups — and parks each one **one click from done** in a review
queue. **You review and submit.** The agent never submits.

This implements [`job-agent-build-plan.md`](job-agent-build-plan.md). The
organizing principle is the **recoverable vs. unrecoverable asymmetry**:
everything recoverable (source → score → tailor → prep → track) is automated;
the one unrecoverable step (submit) stays human.

## Guardrails (enforced in code, not just docs)

- **Never auto-submits.** There is no submit code path that posts to a site; the
  only "submit" records that *you* submitted. The Agent-SDK loop has **no submit
  tool** at all (`jobagent/pipeline_agent.py`).
- **Never invents profile facts.** Pre-fill draws only from the stored profile
  (`jobagent/prep/fields.py`). Anything missing becomes a field for you.
- **Never auto-answers screening questions.** "Why do you want to work here",
  salary, and work-authorization specifics always route to you — enforced by
  `assert_guardrails()` (a unit test fails if this ever regresses).
- **No prohibited scraping.** Sourcing uses official JSON APIs (Greenhouse,
  Lever, Workable) and, for other boards, polite HTML parsing that **respects
  `robots.txt`**, rate-limits per host, and caches responses
  (`jobagent/source/http_client.py`). No LinkedIn/Glassdoor.
- **Inspectable state.** A single plain SQLite file plus a readable
  `config.yaml`. An append-only `events` table audits every action.

## Install

Everything the core pipeline needs (`httpx`, `PyYAML`, `Jinja2`, `python-docx`,
`beautifulsoup4`, `lxml`) is standard. The optional LLM features add `anthropic`
and `claude-agent-sdk`.

```bash
python3 -m venv .venv --system-site-packages
.venv/bin/pip install -e .            # core
.venv/bin/pip install -e '.[llm,dev]' # + Claude scoring/agent loop + pytest
pip install -r requirements.txt
```

Then either use the `jobagent` script (installed by `pip install -e .`) or
`python -m jobagent`.

The optional **web UI** (`jobagent web`) adds `starlette`, `uvicorn`, and
`python-multipart`:

```bash
.venv/bin/pip install -e '.[web]'
```

## Try it in 30 seconds (no API key, no network)

```bash
jobagent demo      # seeds a profile, the sample resumes, one company, and a
                   # handful of sample roles, then runs score → tailor → prep
jobagent web       # open the UI at http://127.0.0.1:8000
```

`jobagent demo` is fully offline and idempotent: it leaves a few applications
waiting in the review queue so you can immediately see the human gate. It never
sources live boards and never submits.

## Web UI

`jobagent web` serves a local Starlette app (default `http://127.0.0.1:8000`)
that is a thin view over the same SQLite state the CLI uses — so every guardrail
holds. It has **no submit-to-site code path**; the "record submission" button
only logs that *you* submitted, exactly like `jobagent review submit`.

| Page | What it does |
|------|--------------|
| Dashboard | Counts, profile status, recent audit log; buttons to run the pipeline or seed demo data |
| Jobs | Every discovered role, ranked by score with its rationale; **Prepare** one to override the scorer, or **Skip** it |
| Review queue | The human gate — each prepared application, one click from done |
| Application detail | Cover letter, pre-filled data, the fields **you** must complete, and the apply link |
| Tracker | Submitted applications and their outcome — record *no response / rejected / interview / offer* as you hear back |
| Materials | Add or remove résumés and cover letters (upload a file or point at a path on this machine) |
| Companies | Add / remove / activate / deactivate watchlist entries |
| Profile | Edit the core facts + extra facts the agent may use |
| Guide | A built-in how-to: the workflow, what each tab does, and the guardrails |

Every mutating action is crash-proofed: bad input or an illegal state change
becomes a clear in-page message, never a 500.

```bash
jobagent web --port 8000          # default
jobagent web --host 0.0.0.0 --no-browser
```

### Hosting it

This is a local single-user tool, so the simplest remote access is a tunnel
(Tailscale / cloudflared) to a local `jobagent web`. For a serverless deploy on
**Vercel** — which needs a hosted database (Turso/libSQL), a writable `/tmp`
data dir, and a password gate, all wired in behind env vars — see
[`DEPLOY-VERCEL.md`](DEPLOY-VERCEL.md).

## Quickstart

```bash
cp config.example.yaml config.yaml          # edit your watchlist here
cp .env.example .env                         # optional: ANTHROPIC_API_KEY for LLM scoring
jobagent init --sync-companies

# 1) Tell it about you (this is what quality depends on — build plan §5)
jobagent profile set --full-name "Your Name" --email you@example.com \
    --work-authorization "Dual US-Israeli citizen" --location "Remote" \
    --fact "skills=python,sql,api design" --fact "years_experience=6"
jobagent resume add --track backend        --file ~/resumes/backend.docx
jobagent resume add --track data_scientist --file ~/resumes/ds.docx
jobagent cover add --name "Template" --file ~/cover/template.txt.j2 --template

# 2) Add target companies (or edit config.yaml then `jobagent company sync`)
jobagent company add --name Stripe --ats greenhouse --token stripe
jobagent company add --name SomeCo --ats lever      --token someco

# 3) Run the whole recoverable pipeline with ONE command
jobagent run

# 4) The human gate
jobagent review list
jobagent review show 3      # see pre-filled data + exactly what's left for you
jobagent review submit 3    # records that YOU submitted on the real site
```

## The pipeline

```
source → score → tailor → prep →  [ REVIEW + SUBMIT (you) ]  → track → followup → schedule
└──────────── autonomous (jobagent run) ────────────┘   human gate   └──── autonomous ────┘
```

| Stage | Command | What it does |
|-------|---------|--------------|
| Source | `jobagent source` | Pull new roles from the watchlist (JSON APIs first), dedup, cache |
| Score | `jobagent score` | Rank vs. your profile + tracks; shortlist or skip with a rationale |
| Tailor | `jobagent tailor` | Pick the best stored resume + cover letter (fills template slots) |
| Prep | `jobagent prep` | Pre-fill from your profile; queue the rest for you (uses the ATS's real questions when available) |
| Review | `jobagent review list/show/submit` | **The human step.** Inspect, finish screening fields, submit |
| Track | `jobagent track set-status <app#> interview` | Record outcomes |
| Follow up | `jobagent followup run` | Draft polite follow-ups for quiet applications |
| Schedule | `jobagent schedule add-interview <app#> --when ...` | Create a calendar `.ics` |
| All-in-one | `jobagent run` | source → score → tailor → prep → followup |
| Status | `jobagent status` / `jobagent events` | Dashboard + audit log |

### Scoring backends

`scoring.backend` in `config.yaml`:
- `auto` (default) — Claude if `ANTHROPIC_API_KEY` is set and the SDK is
  installed, otherwise the deterministic **heuristic** scorer.
- `heuristic` — always offline; same input → same score (great for testing).
- `claude` — require Claude.

So the entire pipeline runs and is fully testable **without any API key**.

### Agent-SDK loop (optional)

`jobagent run --agent` drives the same stages via the Claude Agent SDK (build
plan §3), exposing each as an in-process tool — and pointedly **no submit
tool**. Needs the SDK + credentials; otherwise use plain `jobagent run`.

## Configuration

- `config.yaml` — watchlist + tunables (scoring threshold, rate limits, cache
  TTL, follow-up cadence). See `config.example.yaml`.
- `.env` — secrets only (`ANTHROPIC_API_KEY`). Gitignored. Never logged.
- State lives in `./data/` (SQLite DB, response cache, generated cover letters &
  `.ics`). Override with `paths.data_dir` or `JOBAGENT_DATA_DIR`.

Supported `ats_type`: `greenhouse`, `lever`, `workable` (JSON), `comeet`/`other`
(polite HTML / JSON-LD).

## Architecture

```
jobagent/
  config.py db.py schema.sql models.py repo.py events.py   # core + state machine
  state/        profile, resumes, cover_letters, companies # human-authored inputs
  source/       http_client (robots/rate-limit/cache) + ATS adapters
  score/        base (pluggable) + heuristic + claude
  tailor/       selection + Jinja2/docx rendering
  prep/         fields (GUARDRAILS) + greenhouse_questions
  review/       the human gate CLI
  track/        tracking + followup + schedule(.ics)
  pipeline.py   run_once (deterministic)   pipeline_agent.py (SDK loop)
  demo.py       `jobagent demo` — seed an offline example + run the pipeline
  web/          optional Starlette UI (app.py, cli.py, templates/) — `jobagent web`
  cli.py        argparse CLI (stages auto-register)
tests/          pytest, fully offline
```

## Testing

```bash
.venv/bin/python -m pytest      # 42 offline tests, no network/API key
```

The guardrail invariants have dedicated tests (`tests/test_prep_guardrails.py`)
and the whole loop has an offline integration test (`tests/test_pipeline.py`).

## Not in v1 (earned autonomy — see build plan §9)

Per-ATS auto-submit whitelists, smarter follow-ups, and analytics are
deliberately out of scope. Each must preserve every guardrail above. The success
metric is **human-touch rate**, driven down per-proven-site over time — never by
removing the human from the irreversible step up front.
