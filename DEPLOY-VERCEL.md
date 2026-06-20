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
corresponding env var is set, and `python -m pytest` (42 tests) still passes.

> ⚠️ **One thing I could not verify in this environment:** the `libsql-experimental`
> driver needs a prebuilt wheel, which exists for Linux CPython 3.9–3.12 (what
> Vercel runs) but not for the local sandbox's Python 3.14. The compatibility
> shim around it (`jobagent/db.py`) was validated against stdlib sqlite3, but
> the live Turso connection itself should be confirmed on your first deploy.

---

## Files added for Vercel

| File | Purpose |
|------|---------|
| `api/index.py` | ASGI entrypoint Vercel serves (`app`) |
| `vercel.json` | Routes every path to the function; `maxDuration` |
| `requirements.txt` | Includes `starlette`, `python-multipart`, `libsql-experimental` |
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

In **Vercel → Project → Settings**:

- **General → Node/Build**: ensure the **Python version is 3.12** (so the
  `libsql-experimental` wheel resolves).
- **Environment Variables** (add all of these):

  | Name | Value |
  |------|-------|
  | `JOBAGENT_DB_URL` | `libsql://jobagent-<org>.turso.io` |
  | `JOBAGENT_DB_AUTH_TOKEN` | *(the token from step 1)* |
  | `JOBAGENT_DATA_DIR` | `/tmp/jobagent` |
  | `JOBAGENT_WEB_PASSWORD` | *(a strong password)* |
  | `JOBAGENT_WEB_USER` | *(optional; defaults to `admin`)* |

Then redeploy (`vercel --prod`).

## Step 3 — First use

Open the deployment URL, log in with the basic-auth credentials, then click
**Seed demo data** on the dashboard (or set up your profile + companies). State
now lives in Turso and persists across requests and cold starts.

---

## Caveats on the hosted path

- **Live sourcing is best run locally.** Pulling many ATS boards can exceed a
  serverless function's time limit (and Vercel's hobby tier caps duration). The
  **Run pipeline** button defaults to *not* sourcing live boards; leave that box
  unchecked on Vercel. Best workflow: run `jobagent run` locally against the same
  Turso DB (set the same env vars in your shell), and use the hosted UI purely to
  **review and record submissions**.
- **Generated cover-letter files are ephemeral.** The review page re-renders the
  cover letter from its template when the file is gone, so it always displays —
  but downloadable `.docx` files written to `/tmp` won't persist.
- **Still never submits.** There is no submit-to-site code path here either; the
  "record submission" button only logs that *you* submitted.

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
