# Deploying jobagent to Vercel

`jobagent` was built as a **local, single-user tool** with persistent local
state. Vercel is **serverless** (no long-lived process, ephemeral read-only
filesystem), so deploying there needs three adaptations — all already wired
into this repo behind environment variables:

1. **Database** — a local SQLite file does not persist between invocations, so
   state is moved to **Turso** (libSQL, a SQLite-compatible hosted database).
   Activated by `JOBAGENT_DB_URL`.
2. **Writable paths** — only `/tmp` is writable on Vercel. `JOBAGENT_DATA_DIR`
   points there (the entrypoint defaults it to `/tmp/jobagent`).
3. **Auth** — the UI shows personal profile data, so a public URL must be
   gated. Setting `JOBAGENT_WEB_PASSWORD` turns on HTTP Basic auth.

The local app and the CLI are unchanged: none of this activates unless the
corresponding env var is set, and `python -m pytest` (49 tests) still passes.

**If anything goes wrong, the deploy tells you why** rather than showing an
opaque 500:
- **`/healthz`** (no login required) returns JSON with the active DB driver,
  whether it connects, whether the HTML templates bundled correctly, the Python
  version, and the data dir.
- If the app fails to even start (a missing dependency, bad DB config), every
  page returns the **actual Python traceback** as plain text — see
  [Diagnostics](#diagnostics--troubleshooting).

> ⚠️ **One piece I could not run in this sandbox:** the `libsql-experimental`
> driver needs a prebuilt wheel — available for Linux CPython 3.9–3.12 (what
> Vercel runs) but not the sandbox's Python 3.14. The compatibility shim around
> it (`jobagent/db.py`) is unit-tested against stdlib sqlite3; the live Turso
> connection should be confirmed on first deploy via `/healthz`.

---

## Files added for Vercel

| File | Purpose |
|------|---------|
| `api/index.py` | ASGI entrypoint Vercel serves (`app`) + startup-failure diagnostic |
| `vercel.json` | Explicit `builds` (Python) + `routes` (all paths → the app) + `includeFiles` |
| `api/requirements.txt` | Deps next to the entrypoint, where `@vercel/python` installs from |
| `requirements.txt` | Same deps at the root (belt-and-suspenders) |
| `.python-version` | Pins Vercel to Python **3.12** (the version `libsql-experimental` has wheels for) |
| `.vercelignore` | Keeps local state / tests / venv out of the bundle |

---

## Step 1 — Create a Turso database

```bash
# install the Turso CLI (https://docs.turso.tech), then:
turso auth login
turso db create jobagent
turso db show jobagent --url            # -> libsql://jobagent-<org>.turso.io
turso db tokens create jobagent         # -> a long auth token
```

The schema is created automatically on first connect (the app runs the
idempotent `schema.sql` via `init_db`), so there is nothing to migrate.

## Step 2 — Deploy

```bash
npm i -g vercel
vercel            # first run links/creates the project
```

In **Vercel → Project → Settings → Environment Variables**, add:

| Name | Value | Required |
|------|-------|----------|
| `JOBAGENT_DB_URL` | `libsql://jobagent-<org>.turso.io` | yes (persistence) |
| `JOBAGENT_DB_AUTH_TOKEN` | *(the token from step 1)* | yes |
| `JOBAGENT_DATA_DIR` | `/tmp/jobagent` | yes |
| `JOBAGENT_WEB_PASSWORD` | *(a strong password)* | yes (public URL) |
| `JOBAGENT_WEB_USER` | *(defaults to `admin`)* | optional |
| `JOBAGENT_WEB_DEBUG` | `1` to show tracebacks in-page while debugging | optional |

> **Python version (handled for you):** the committed `.python-version` file
> pins Vercel to **3.12**, the version `libsql-experimental` ships wheels for —
> so you don't set anything. As a safety net, that dependency also carries a
> `python_version < "3.13"` marker: if the pin is ever ignored (Vercel's default
> is drifting toward 3.14), the build still **succeeds** — it simply skips the
> Turso driver and falls back to ephemeral storage, which `/healthz` flags. The
> running version is shown as the `python` field at `/healthz`.

Then redeploy (`vercel --prod`).

## Step 3 — First use

1. Visit `/healthz` first — you want `{"ok": true, "driver": "libsql/turso", ...}`.
2. Open the app, log in, then click **Seed demo data** on the dashboard (or set
   up your profile). State now lives in Turso and persists across cold starts.

---

## Diagnostics & troubleshooting

| Symptom | Likely cause → fix |
|---------|--------------------|
| Page shows a Python **traceback** starting "jobagent failed to start" | App couldn't construct. Read the traceback: usually `ModuleNotFoundError` (a dep didn't install) or a DB config error. |
| `/healthz` shows `"ok": false` with a libsql error | `JOBAGENT_DB_URL`/`JOBAGENT_DB_AUTH_TOKEN` is wrong. |
| Turso vars are set but `/healthz` still shows `"driver": "sqlite3 (local file)"` | The `libsql` import was skipped because Vercel ran Python 3.13+ (check the `python` field). Make sure `.python-version` (= `3.12`) is committed and the deploy picked it up. |
| `/healthz` shows `"driver": "sqlite3 (local file)"` and `"ephemeral": true` | `JOBAGENT_DB_URL` isn't set — data won't persist. Add the Turso vars. |
| `/healthz` shows `"templates_ok": false` | The HTML templates didn't bundle. Confirm `includeFiles` in `vercel.json` and that `.vercelignore` isn't stripping `jobagent/`. |
| `401` on every page | Auth is on (`JOBAGENT_WEB_PASSWORD` set) — log in. `/healthz` and `/favicon.ico` stay open by design. |
| Dashboard shows a red "data will NOT persist" banner | You're on serverless with no DB. Set `JOBAGENT_DB_URL`. |

Runtime logs (full tracebacks for in-page 500s) are in the Vercel dashboard
under the deployment's **Logs**, or via `vercel logs <url>`.

---

## Caveats on the hosted path

- **Live sourcing is disabled on Vercel automatically.** Pulling many ATS boards
  can exceed the serverless time limit, so the "source live boards" option is
  hidden and forced off on Vercel (`VERCEL` env). Best workflow: run
  `jobagent run` locally against the same Turso DB (export the same env vars in
  your shell), and use the hosted UI to **review and record submissions**. The
  **Run pipeline** button on Vercel still re-scores/tailors/preps existing jobs.
- **Generated cover-letter files are ephemeral.** The review page re-renders the
  cover letter from its template when the file is gone, so it always displays —
  but downloadable `.docx` files written to `/tmp` won't persist.
- **Uploaded materials are ephemeral too.** A résumé/cover letter uploaded
  through the **Materials** page lands in `/tmp` and may vanish on a cold start
  (its DB row survives, but the file behind it won't). For durable hosted
  materials, commit the files to the repo and add them *by path* instead. The
  Materials page shows a banner saying as much when running on Vercel.
- **Still never submits.** There is no submit-to-site code path here either; the
  "record submission" button only logs that *you* submitted.

## Foreman (mounted at `/foreman`) — view/demo only

The Foreman control panel is mounted alongside job·agent at **`/foreman`** (also
linked from the job·agent nav). It defaults its state to `/tmp` and runs
synchronously, set via two env vars that already default in `api/index.py` (so no
Vercel config is required):

| Variable | Default | Why |
|---|---|---|
| `FOREMAN_DATA_DIR` | `/tmp/foreman` | Only `/tmp` is writable on Vercel. |
| `FOREMAN_SYNC` | `1` | Vercel freezes the function after the response, so Foreman's default background-thread worker would never make progress; this runs each task inline instead. |

On cold start the function **seeds one finished offline sample** (a `python
check.py` truth signal — no pytest, git, network, or API key) so the dashboard,
runs, stats, and diff render with real content.

What does **not** work on Vercel, by design — Foreman is built to run locally:

- **No persistence across requests.** The ledger lives in ephemeral `/tmp` and
  isn't shared between serverless instances, so a run you start may not be visible
  after the redirect. Only the cold-start sample is reliably present.
- **Real (`llm`) runs and real git repos don't work.** They need a long-lived
  process, the `anthropic` SDK + key, `git worktree`, and a persistent disk —
  none of which exist on Vercel. They degrade gracefully to `needs_human`.

**To actually run Foreman**, use it locally — double-click `Foreman.command`, or
`python -m foreman web` — where it has a real filesystem, git, and a persistent
ledger.

## Alternative: run it locally against Turso (no Vercel)

If the serverless constraints get in the way, you get the same shared-state
benefit with none of them by pointing the local app at Turso:

```bash
export JOBAGENT_DB_URL=libsql://jobagent-<org>.turso.io
export JOBAGENT_DB_AUTH_TOKEN=...
.venv/bin/pip install libsql-experimental   # needs a wheel for your Python
jobagent web
```

Or skip the hosted DB entirely and keep it fully local — that is the design's
happy path, and what `jobagent web` does out of the box.
