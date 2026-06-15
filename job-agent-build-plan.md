# Autonomous Job Application Agent — Build Plan

> A spec for Claude Code. Read this whole file before writing code. The design choices here are deliberate — especially the guardrails in §2. Do not "improve" the plan by removing the human review step or widening sourcing to the open web; those constraints exist for legal, quality, and reputation reasons explained below.

---

## 1. Goal & Philosophy

Build a local, persistent agent that handles the **entire job-application pipeline except the final submit click**. The agent sources roles, scores them, selects pre-approved materials, pre-fills applications, tracks status, and drafts follow-ups — autonomously. The human reviews and submits.

The single organizing principle is the **recoverable vs. unrecoverable asymmetry**:

- **Recoverable stages** (sourcing, scoring, tailoring, tracking): a mistake just means a skipped or slightly-off job. No lasting harm. **Optimize these for autonomy and throughput.**
- **Unrecoverable stage** (submission): a botched application to a company the user wanted cannot be retracted, and this is a small, reference-driven market. **Do not optimize this for volume. Keep a human on it.**

The success metric is **not "applications submitted."** It is **human-touch rate** — the fraction of prepared applications that still need a human before submit. The system starts with that rate high (human reviews most things) and we drive it down over time by whitelisting specific, proven-clean ATS sites for true auto-submit *later*. We earn autonomy per-site; we do not assume it everywhere up front.

**The one human step in v1:** review a fully prepared, one-click-from-done application, then submit it.

---

## 2. Non-Goals & Guardrails (hard rules)

These are not suggestions. Treat them as invariants the code must enforce.

- [ ] **Never auto-submit in v1.** Every prepared application lands in a review queue. Submission is a human action. (Auto-submit for whitelisted ATSs is a future phase — see §9 — and must be explicitly opt-in per site.)
- [ ] **Never invent profile facts.** The agent may only use data from the stored state (§5). It must not improvise the user's experience, skills, work authorization, or answers about them. If a field needs info not in state, flag it for the human.
- [ ] **Never auto-answer free-text screening questions** ("why do you want to work here," salary expectations, work-authorization explanations). These go to the human every time. They are the exact place an agent fabricates a professional identity — which the resume-per-track design (§5) is specifically meant to prevent.
- [ ] **Do not scrape LinkedIn, Glassdoor, or any site whose ToS prohibits automated access.** Use official JSON APIs where they exist (Greenhouse and Lever both expose public job-board JSON — prefer these over HTML scraping). LinkedIn sourcing, if wanted, is left to the human.
- [ ] **Respect `robots.txt`, rate-limit all fetches, and cache responses.** Be a polite client. Never hammer a careers page.
- [ ] **Secrets via environment variables only.** `ANTHROPIC_API_KEY` and any others come from env/`.env` (gitignored). Never commit credentials. Never log them.
- [ ] **The watchlist is finite and human-defined.** No open-ended "scour the entire web for jobs." Sourcing reads a configured list of company ATS boards (§6.1). Open web search produces stale aggregator links and dead postings; a defined watchlist is more reliable, not just safer.
- [ ] **All state is inspectable and editable by the human.** Use a plain SQLite file plus human-readable config. No opaque stores.

---

## 3. Architecture Overview

### The loop

```
[Human, once: target-company watchlist + profile + resume variants + cover letters]
                                  │
                                  ▼
  ┌──────────────── AGENT LOOP (autonomous) ────────────────┐
  │  1. SOURCE   watch configured ATS boards for new roles    │
  │  2. SCORE    rank each job against the stored profile     │
  │  3. TAILOR   select the right resume variant + cover ltr  │
  │  4. PREP     pre-fill the application as far as possible   │
  │  5. QUEUE    push a one-click-ready item to review         │
  └──────────────────────────┬───────────────────────────────┘
                              ▼
              [Human: review + submit]  ◀── the only human step
                              │
  ┌──────────────── AGENT LOOP (autonomous again) ───────────┐
  │  6. TRACK    log submission, watch status                 │
  │  7. FOLLOW   draft follow-ups on a schedule               │
  │  8. SCHEDULE add interviews to the calendar               │
  └──────────────────────────────────────────────────────────┘
```

### Tech stack & rationale

- **Language: Python 3.10+.** Matches the user's background and the data-science library ecosystem.
- **Orchestration: Claude Agent SDK** (`pip install claude-agent-sdk`). Provides the agent loop, tool execution, and context management. The agent runs **locally** so state persists and the user keeps full control — this is the right fit over the hosted ephemeral code-execution tool, which doesn't persist between calls.
- **State: SQLite** (single file, no server, persistent, trivially inspectable). This is the agent's long-term memory.
- **HTTP: `httpx`.** For fetching ATS boards and JSON APIs.
- **HTML fallback parsing: `BeautifulSoup` / `lxml`.** Only for ATSs without a JSON API.
- **Document handling: `python-docx` + `jinja2`.** Resume variants are stored as finished files; the agent selects, it does not generate them. Cover letters may be lightly templated (e.g. inject company/role into a stored template) via Jinja2.
- **Calendar: Google Calendar** (the user already has the connector) for interview scheduling.
- **Config: a `config.yaml`** for the watchlist + tunables, kept separate from code.

Keep it a clean, modular Python package — one module per pipeline stage so each is independently testable.

---

## 4. Data Model (SQLite)

Start with this schema; evolve as needed but keep the status state machine intact.

**`profile`** (one row — the human's core facts)
`id, full_name, email, phone, citizenship, work_authorization, location, languages (json), github_url, linkedin_url, portfolio_url, salary_expectation_notes, updated_at`

**`resume_variants`** (human-authored, one per track)
`id, track (e.g. backend|frontend|devops|fullstack|data_scientist|data_analyst), file_path, notes, updated_at`

**`cover_letters`** (a library; may be plain or Jinja2-templated)
`id, name, track, is_template (bool), file_path, updated_at`

**`companies`** (the watchlist — human-defined)
`id, name, ats_type (greenhouse|lever|comeet|workable|other), board_url, notes, active (bool)`

**`jobs`** (discovered roles)
`id, company_id (fk), title, url, ats_type, description_text, raw_payload_ref, date_found, score (float), score_rationale, status, updated_at`

**`applications`**
`id, job_id (fk), resume_variant_id (fk), cover_letter_id (fk), prefilled_data (json), unanswered_fields (json), status, queued_at, submitted_at, follow_up_due, notes`

**`events`** (append-only audit log)
`id, ts, entity_type, entity_id, action, detail`

### Job status state machine

```
discovered → scored → (skipped | shortlisted)
shortlisted → prepared → queued_for_review
queued_for_review → submitted        (human action)
submitted → (no_response | rejected | interview | offer)
```

`skipped` is terminal-but-revisitable. `unanswered_fields` on an application is what tells the review UI which screening questions the human must fill — the agent never guesses these.

---

## 5. Human-Authored State (the inputs the user provides)

The agent's quality depends entirely on this being well-populated. Build a simple way (CLI command or a small local form) for the human to create and update:

1. **Profile** — the core-facts row above. Includes work authorization (the user is a dual US–Israeli citizen, so this matters on forms) and salary-expectation notes the agent can surface but not auto-submit.
2. **Resume variants** — one finished resume file per track. The human writes these. The agent's job is to pick the right one, not author it. This is the design decision that removes the biggest source of agent error.
3. **Cover letter library** — either finished letters or Jinja2 templates with `{{ company }}` / `{{ role }}` slots the agent can safely fill.
4. **Links** — GitHub, LinkedIn, portfolio (already in `profile`).
5. **Watchlist** — the list of target companies and their ATS board URLs, in `config.yaml`, loaded into the `companies` table.

---

## 6. Pipeline Stages (detail)

### 6.1 Source
Read each active company's ATS board. **Prefer official JSON endpoints** (Greenhouse Job Board API, Lever Postings API) over HTML scraping — they're stable and sanctioned. For Comeet/Workable/other, fetch + parse politely with caching and rate limits. Upsert new roles into `jobs` as `discovered`. Deduplicate by URL.

### 6.2 Score
For each `discovered` job, score against the profile and the available resume tracks. Use the Agent SDK / model to produce a `score` (0–1) and a short `score_rationale`. Above a configurable threshold → `shortlisted`; below → `skipped`. This stage is fully autonomous and risk-free (skips are recoverable).

### 6.3 Tailor
For each `shortlisted` job, select the **best-matching stored resume variant** by track and pick (or template-fill) a cover letter. No resume generation — selection only. Record which variant and letter were chosen on the `applications` row. Mark `prepared`.

### 6.4 Prep
Pre-fill the application as far as the form allows from stored state: name, contact, links, resume upload, cover letter. Populate `prefilled_data`. **Anything the agent can't safely fill** — free-text screening questions, salary fields, anything not in state — goes into `unanswered_fields`. Mark `queued_for_review`. Do **not** submit.

### 6.5 Review & Submit (human)
A simple local review surface (CLI list → detail, or a tiny local web view) shows each queued application: the chosen resume, the cover letter, the pre-filled fields, and — highlighted — the `unanswered_fields` the human must complete. Human finishes those, submits on the actual site, and marks `submitted`. (Future: whitelisted ATSs can offer a real auto-submit button. Not v1.)

### 6.6 Track, Follow up, Schedule
- Watch submitted applications; update status as info arrives (manual marking is fine in v1).
- On a configurable cadence ("submitted N days ago, no response"), draft a follow-up message for the human to review/send.
- When an interview is set, create a Google Calendar event.

---

## 7. Build Phases (incremental, each independently testable)

Build in this order. Each phase should end with something runnable and verifiable. Do **not** jump ahead to submission automation.

**Phase 0 — Scaffold.** Package structure, `config.yaml`, `.env` handling, SQLite init + schema, logging, the `events` audit log. Done when the DB initializes and config loads.

**Phase 1 — State layer.** CRUD for `profile`, `resume_variants`, `cover_letters`, `companies` via a small CLI. Done when the human can fully populate their state and read it back.

**Phase 2 — Source.** Greenhouse + Lever JSON ingestion first (most reliable), then one HTML-based ATS. Caching, rate limiting, dedup. Done when running `source` populates `jobs` with `discovered` roles from the watchlist.

**Phase 3 — Score.** Scoring + rationale via the Agent SDK; threshold → shortlist/skip. Done when `jobs` get scored and split correctly, with readable rationales.

**Phase 4 — Tailor.** Resume-variant selection + cover-letter selection/templating. Done when shortlisted jobs get the right variant attached on an `applications` row.

**Phase 5 — Prep + Review.** Pre-fill `prefilled_data`, populate `unanswered_fields`, build the human review surface. Done when a human can open a queued application, see everything pre-filled, see exactly what's left, and mark it submitted.

**Phase 6 — Track + Follow + Schedule.** Status tracking, follow-up drafting, calendar integration. Done when the post-submission loop runs and follow-ups/interviews are handled.

**Phase 7 — Tie the loop together.** Wrap stages 6.1–6.4 + 6.6–6.8 into the autonomous Agent SDK loop, leaving 6.5 as the human gate. Done when one command runs the whole recoverable pipeline end to end and parks ready applications in the review queue.

---

## 8. Definition of Done (v1)

- Human populates profile, per-track resumes, cover letters, and a company watchlist.
- One command runs the autonomous pipeline: sources from the watchlist, scores, shortlists, selects materials, pre-fills, and queues review-ready applications.
- The human review surface clearly shows pre-filled data and the precise set of fields/questions left to answer.
- Post-submission tracking, scheduled follow-up drafts, and calendar interview events work.
- No auto-submission anywhere. No fabricated profile facts. No prohibited scraping.
- State is a plain, inspectable SQLite file plus readable config.

---

## 9. Future — Earned Autonomy (explicitly out of scope for v1)

Once the system is proven, we shrink the human-touch rate deliberately:

- **Per-ATS auto-submit whitelist.** For specific ATS types that have proven clean and low-risk (stable forms, no CAPTCHA, no surprise free-text fields), add an opt-in true auto-submit path. Still never for applications that have any `unanswered_fields`.
- **Smarter follow-up automation** once the human trusts the drafts.
- **Analytics** on response/interview rates by track, company, and resume variant — to feed back into scoring.

Each of these is an additive phase that must preserve every guardrail in §2.

---

### A note on the philosophy, for whoever builds this

The temptation will be to make this "apply to everything, fully hands-off." Resist it. The volume firehose underperforms targeted, clean applications in this market even on its own terms, and the one unrecoverable step — submission — is exactly where a confident wrong answer does lasting damage. The whole architecture is built to give the human maximum leverage on the recoverable 90% while keeping them on the irreversible 10%. Build to that line.
