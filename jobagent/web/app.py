"""Starlette application factory and route handlers for the local web UI.

Each handler opens its own :class:`AgentContext` (one SQLite connection per
request, used within a single thread) and closes it — mirroring how every CLI
command works, so the UI shares the exact same dedup/state-machine logic.

There is intentionally no route that submits to a real ATS. ``/review/{id}/
submit`` only *records* that the human submitted, exactly like
``jobagent review submit``.
"""

from __future__ import annotations

import functools
import os
from pathlib import Path
from urllib.parse import quote_plus

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.responses import (
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Response,
)
from starlette.routing import Route
from starlette.templating import Jinja2Templates

from ..config import Config
from ..context import AgentContext
from ..db import db_info
from ..logging_setup import get_logger
from .auth import BasicAuthMiddleware, auth_credentials
from ..events import recent_events
from ..models import ApplicationStatus, JobStatus
from ..repo import (
    decode_application,
    get_application,
    get_job,
    list_applications_by_status,
    mark_application_submitted,
    set_job_status,
)
from ..state import companies as companies_state
from ..state import cover_letters as cover_state
from ..state import profile as profile_state
from ..state import resumes as resume_state
from ..util import from_json, iso_in_days

_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

def _serverless() -> bool:
    """True on a serverless host (Vercel sets VERCEL=1).

    Drives a few adjustments: live sourcing is disabled (it can exceed the
    function timeout) and a banner warns when state is not on a persistent
    (Turso) database. Read live (not cached) so it stays correct and testable.
    """
    return bool(os.environ.get("VERCEL"))


def _persistence_warning() -> str | None:
    """A banner string when data won't survive (serverless + no Turso DB)."""
    if _serverless() and not os.environ.get("JOBAGENT_DB_URL"):
        return (
            "⚠ Running on serverless storage with no database configured — data "
            "will NOT persist between requests. Set JOBAGENT_DB_URL (Turso) to fix. "
            "See /healthz."
        )
    return None


# Templates the app cannot function without — probed by /healthz so a Vercel
# file-bundling miss surfaces as a clear health failure, not a blank 500.
_CRITICAL_TEMPLATES = ("base.html", "dashboard.html", "review_detail.html")


def _templates_ok() -> tuple[bool, str | None]:
    try:
        for name in _CRITICAL_TEMPLATES:
            templates.env.get_template(name)
        return True, None
    except Exception as exc:  # e.g. TemplateNotFound if not bundled
        return False, f"{type(exc).__name__}: {exc}"


def _redirect(path: str, msg: str | None = None) -> RedirectResponse:
    """A 303 redirect with a properly URL-encoded flash message."""
    if msg:
        sep = "&" if "?" in path else "?"
        path = f"{path}{sep}msg={quote_plus(msg)}"
    return RedirectResponse(path, status_code=303)


log = get_logger("web")


def post_action(error_redirect: str):
    """Wrap a mutating handler so *any* failure becomes a friendly flash redirect.

    This is the "handle any scenario" guarantee for user actions: a bad input, an
    illegal state transition, a transient DB hiccup — none of them dead-end on a
    500. The real error is logged (and visible with JOBAGENT_WEB_DEBUG) and shown
    to the user in plain language.
    """

    def decorator(fn):
        @functools.wraps(fn)
        async def wrapper(request):
            try:
                return await fn(request)
            except Exception as exc:
                log.exception("web action %s failed", fn.__name__)
                return _redirect(error_redirect, f"Couldn’t complete that: {exc}")

        return wrapper

    return decorator

# Profile fields the edit form exposes (plain text columns only).
_PROFILE_TEXT_FIELDS = [
    ("full_name", "Full name"),
    ("email", "Email"),
    ("phone", "Phone"),
    ("location", "Location"),
    ("work_authorization", "Work authorization"),
    ("citizenship", "Citizenship"),
    ("github_url", "GitHub URL"),
    ("linkedin_url", "LinkedIn URL"),
    ("portfolio_url", "Portfolio URL"),
    ("salary_expectation_notes", "Salary expectation notes"),
]


def _company_name(ctx: AgentContext, company_id) -> str:
    c = companies_state.get_company(ctx.conn, int(company_id))
    return c["name"] if c else "?"


def _cover_text(ctx: AgentContext, a: dict, job) -> str | None:
    """Cover-letter text for the review page.

    Prefers the file the tailor stage rendered; if that file is gone (expected
    on an ephemeral filesystem like Vercel, where /tmp is wiped between cold
    starts) it re-renders the template on the fly so the letter still shows.
    """
    path = a.get("cover_letter_rendered")
    if path and Path(path).exists():
        return Path(path).read_text(encoding="utf-8")

    cover_id = a.get("cover_letter_id")
    if not (cover_id and job):
        return None
    cover = cover_state.get_cover_letter(ctx.conn, cover_id)
    if not (cover and cover["is_template"] and Path(cover["file_path"]).exists()):
        return None

    from ..tailor import documents

    profile = profile_state.get_profile(ctx.conn) or {}
    context = {
        "company": _company_name(ctx, job["company_id"]),
        "role": job["title"],
        "applicant_name": profile.get("full_name", ""),
        "applicant_email": profile.get("email", ""),
        "track_label": documents.track_label(job["suggested_track"]),
    }
    try:
        return documents.render_template(cover["file_path"], context)
    except Exception:
        return None


def _counts(ctx: AgentContext) -> dict:
    conn = ctx.conn
    jobs_by_status = {
        r["status"]: r["n"]
        for r in conn.execute(
            "SELECT status, COUNT(*) n FROM jobs GROUP BY status"
        ).fetchall()
    }
    apps_by_status = {
        r["status"]: r["n"]
        for r in conn.execute(
            "SELECT status, COUNT(*) n FROM applications GROUP BY status"
        ).fetchall()
    }
    return {
        "resumes": len(resume_state.list_resumes(conn)),
        "covers": len(cover_state.list_cover_letters(conn)),
        "companies_active": len(companies_state.list_companies(conn, active_only=True)),
        "companies_total": len(companies_state.list_companies(conn)),
        "jobs_by_status": jobs_by_status,
        "apps_by_status": apps_by_status,
        "queued": apps_by_status.get(ApplicationStatus.QUEUED_FOR_REVIEW, 0),
    }


# --------------------------------------------------------------------------- #
# routes
# --------------------------------------------------------------------------- #
async def dashboard(request):
    with AgentContext.create() as ctx:
        profile = profile_state.get_profile(ctx.conn)
        missing = profile_state.missing_core_fields(ctx.conn)
        counts = _counts(ctx)
        events = [dict(e) for e in recent_events(ctx.conn, 15)]
        for e in events:
            e["detail"] = from_json(e.get("detail"), {})
    total_jobs = sum(counts["jobs_by_status"].values())
    is_fresh = not (profile and profile.get("full_name")) and total_jobs == 0
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "active": "dashboard",
            "profile": profile,
            "missing": missing,
            "counts": counts,
            "events": events,
            "msg": request.query_params.get("msg"),
            "warning": _persistence_warning(),
            "serverless": _serverless(),
            "is_fresh": is_fresh,
        },
    )


async def healthz(request):
    """Open (no-auth) diagnostic: DB driver, connectivity, environment.

    The first thing to hit when a deploy misbehaves — it surfaces the real cause
    (e.g. a libSQL import/auth failure) as JSON instead of an opaque 500.
    """
    info = db_info(Config.load())
    info["serverless"] = _serverless()
    info["auth_enabled"] = auth_credentials() is not None
    templates_ok, templates_error = _templates_ok()
    info["templates_ok"] = templates_ok
    if templates_error:
        info["templates_error"] = templates_error
    healthy = bool(info.get("ok")) and templates_ok
    return JSONResponse(info, status_code=200 if healthy else 503)


async def favicon(request):
    return Response(status_code=204)


async def guide(request):
    """Static how-to-use page.

    Deliberately DB-free so the help is always available — even if the database
    or another page is the thing that's broken, a user can still read how the
    tool works and where to look (it points at /healthz for diagnostics).
    """
    return templates.TemplateResponse(request, "guide.html", {"active": "guide"})


async def jobs(request):
    with AgentContext.create() as ctx:
        rows = ctx.conn.execute(
            """
            SELECT j.id, j.title, j.location, j.url, j.score, j.score_rationale,
                   j.suggested_track, j.status, c.name AS company
            FROM jobs j JOIN companies c ON c.id = j.company_id
            ORDER BY (j.score IS NULL), j.score DESC, j.id
            """
        ).fetchall()
        jobs = [dict(r) for r in rows]
    return templates.TemplateResponse(
        request, "jobs.html", {"active": "jobs", "jobs": jobs}
    )


async def review_list(request):
    with AgentContext.create() as ctx:
        apps = list_applications_by_status(ctx.conn, ApplicationStatus.QUEUED_FOR_REVIEW)
        items = []
        for app in apps:
            a = decode_application(app)
            job = get_job(ctx.conn, a["job_id"])
            items.append(
                {
                    "id": a["id"],
                    "score": (job["score"] or 0) if job else 0,
                    "company": _company_name(ctx, job["company_id"]) if job else "?",
                    "title": job["title"] if job else "?",
                    "to_answer": len(a["unanswered_fields"]),
                    "required": sum(1 for u in a["unanswered_fields"] if u.get("required")),
                }
            )
    return templates.TemplateResponse(
        request,
        "review_list.html",
        {"active": "review", "items": items, "msg": request.query_params.get("msg")},
    )


async def review_detail(request):
    app_id = int(request.path_params["id"])
    with AgentContext.create() as ctx:
        app = get_application(ctx.conn, app_id)
        if app is None:
            return _redirect("/review", "No such application")
        a = decode_application(app)
        job = get_job(ctx.conn, a["job_id"])
        resume = (
            resume_state.get_resume(ctx.conn, a["resume_variant_id"])
            if a["resume_variant_id"] else None
        )
        cover_text = _cover_text(ctx, a, job)
        ctx_data = {
            "active": "review",
            "a": a,
            "job": dict(job) if job else None,
            "company": _company_name(ctx, job["company_id"]) if job else "?",
            "resume": dict(resume) if resume else None,
            "resume_missing": bool(resume and not Path(resume["file_path"]).exists()),
            "cover_text": cover_text,
            "prefilled": a["prefilled_data"],
            "unanswered": a["unanswered_fields"],
            "submitted": a["status"] == ApplicationStatus.SUBMITTED,
        }
    return templates.TemplateResponse(request, "review_detail.html", ctx_data)


@post_action("/review")
async def review_submit(request):
    """Record that the HUMAN submitted on the real site. Never submits itself."""
    app_id = int(request.path_params["id"])
    with AgentContext.create() as ctx:
        app = get_application(ctx.conn, app_id)
        if app is None:
            return _redirect("/review", "No such application")
        a = decode_application(app)
        if a["status"] != ApplicationStatus.SUBMITTED:
            follow_up = iso_in_days(ctx.config.followup.days_until_followup)
            mark_application_submitted(ctx.conn, app_id, follow_up)
            set_job_status(ctx.conn, a["job_id"], JobStatus.SUBMITTED)
            msg = f"Recorded app #{app_id} as submitted by you"
        else:
            msg = f"App #{app_id} was already submitted"
    return _redirect("/review", msg)


async def profile_view(request):
    with AgentContext.create() as ctx:
        profile = profile_state.get_profile(ctx.conn) or {}
        missing = profile_state.missing_core_fields(ctx.conn)
    return templates.TemplateResponse(
        request,
        "profile.html",
        {
            "active": "profile",
            "profile": profile,
            "fields": _PROFILE_TEXT_FIELDS,
            "missing": missing,
            "languages": ", ".join(profile.get("languages") or []),
            "facts": profile.get("extra_facts") or {},
            "msg": request.query_params.get("msg"),
        },
    )


@post_action("/profile")
async def profile_save(request):
    form = await request.form()
    updates = {f: (form.get(f) or None) for f, _ in _PROFILE_TEXT_FIELDS}
    languages_raw = form.get("languages") or ""
    languages = [s.strip() for s in languages_raw.split(",") if s.strip()]

    # extra_facts arrive as parallel fact_key/fact_value rows.
    keys = form.getlist("fact_key")
    values = form.getlist("fact_value")
    facts = {
        k.strip(): v.strip()
        for k, v in zip(keys, values)
        if k.strip()
    }

    with AgentContext.create() as ctx:
        profile_state.set_profile(
            ctx.conn,
            languages=languages or None,
            extra_facts=facts or None,
            merge_extra_facts=False,
            **updates,
        )
    return _redirect("/profile", "Profile saved")


async def companies(request):
    with AgentContext.create() as ctx:
        rows = [dict(c) for c in companies_state.list_companies(ctx.conn)]
    return templates.TemplateResponse(
        request,
        "companies.html",
        {
            "active": "companies",
            "companies": rows,
            "ats_types": sorted(companies_state.VALID_ATS),
            "msg": request.query_params.get("msg"),
        },
    )


@post_action("/companies")
async def company_add(request):
    form = await request.form()
    name = (form.get("name") or "").strip()
    ats = (form.get("ats") or "").strip()
    token = (form.get("token") or "").strip() or None
    notes = (form.get("notes") or "").strip() or None
    if not name:
        return _redirect("/companies", "A company name is required")
    with AgentContext.create() as ctx:
        companies_state.add_company(
            ctx.conn, name=name, ats_type=ats, board_token=token,
            notes=notes, active=True,
        )
    return _redirect("/companies", f"Added {name}")


@post_action("/companies")
async def company_toggle(request):
    cid = int(request.path_params["id"])
    with AgentContext.create() as ctx:
        c = companies_state.get_company(ctx.conn, cid)
        if c is not None:
            companies_state.set_active(ctx.conn, cid, active=not bool(c["active"]))
    return _redirect("/companies", "Watchlist updated")


@post_action("/companies")
async def company_delete(request):
    cid = int(request.path_params["id"])
    with AgentContext.create() as ctx:
        companies_state.delete_company(ctx.conn, cid)
    return _redirect("/companies", "Company removed")


def _pipeline_feedback(ctx: AgentContext, results: dict) -> str:
    """A human explanation of a pipeline run — especially when it prepared nothing."""
    queued = results.get("queued_for_review", 0)
    if queued:
        return f"Pipeline complete: {queued} queued for review"

    # Nothing got queued — explain the most likely reason so it's not a dead-end.
    conn = ctx.conn
    if not resume_state.list_resumes(conn):
        return "Pipeline ran, but you have no resume — add one in Materials, then run again"
    if profile_state.missing_core_fields(conn):
        missing = ", ".join(profile_state.missing_core_fields(conn))
        return f"Pipeline ran, but your profile is missing: {missing} — set it in Profile"
    total_jobs = sum(r["n"] for r in conn.execute("SELECT COUNT(*) n FROM jobs").fetchall())
    if total_jobs == 0:
        return "Pipeline ran, but there are no jobs yet — add companies and run `jobagent run` locally, or seed demo data"
    return "Pipeline ran, but no jobs scored above the shortlist threshold (nothing new to prepare)"


@post_action("/")
async def run_pipeline(request):
    """Run the recoverable pipeline once (source -> score -> tailor -> prep).

    Sourcing is resilient: per-company network errors are isolated and reported,
    so this is safe to trigger even with no network / no API key.
    """
    from ..pipeline import run_once

    form = await request.form()
    # Live sourcing hits many external boards and can blow the serverless
    # function timeout, so it is disabled on Vercel — run `jobagent run` locally
    # against the same database instead.
    serverless = _serverless()
    with_source = (form.get("source") == "on") and not serverless
    with AgentContext.create() as ctx:
        results = run_once(ctx, source=with_source)
        msg = _pipeline_feedback(ctx, results)
    if serverless and form.get("source") == "on":
        msg += " (sourcing skipped on serverless)"
    return _redirect("/review", msg)


@post_action("/")
async def run_demo(request):
    from ..demo import seed_demo

    with AgentContext.create() as ctx:
        result = seed_demo(ctx, run=True)
        queued = result.get("pipeline", {}).get("queued_for_review", 0)
    return _redirect("/review", f"Demo seeded: {queued} queued for review")


# --------------------------------------------------------------------------- #
# tracker — post-submission outcomes (the lifecycle after the human gate)
# --------------------------------------------------------------------------- #
async def tracker(request):
    from ..track import tracking

    with AgentContext.create() as ctx:
        items = tracking.list_tracked(ctx.conn)
    return templates.TemplateResponse(
        request,
        "tracker.html",
        {
            "active": "tracker",
            "items": items,
            "outcomes": list(JobStatus.OUTCOMES),
            "msg": request.query_params.get("msg"),
        },
    )


@post_action("/tracker")
async def outcome_set(request):
    from ..track import tracking

    app_id = int(request.path_params["app_id"])
    form = await request.form()
    status = (form.get("status") or "").strip()
    with AgentContext.create() as ctx:
        tracking.set_outcome(ctx, app_id, status)
    return _redirect("/tracker", f"Marked app #{app_id}: {status.replace('_', ' ')}")


# --------------------------------------------------------------------------- #
# manual job actions — let the human override the scorer
# --------------------------------------------------------------------------- #
@post_action("/jobs")
async def job_prepare(request):
    """Shortlist a single job and prepare it (tailor + prep), overriding scoring."""
    from ..prep.runner import run_prep
    from ..tailor.runner import run_tailor

    jid = int(request.path_params["id"])
    with AgentContext.create() as ctx:
        job = get_job(ctx.conn, jid)
        if job is None:
            return _redirect("/jobs", "No such job")
        if not resume_state.list_resumes(ctx.conn):
            return _redirect("/jobs", "Add a resume in Materials first — can’t prepare without one")
        set_job_status(ctx.conn, jid, JobStatus.SHORTLISTED)
        run_tailor(ctx)
        run_prep(ctx)
    return _redirect("/review", f"Prepared “{job['title']}” — see the review queue")


@post_action("/jobs")
async def job_skip(request):
    jid = int(request.path_params["id"])
    with AgentContext.create() as ctx:
        set_job_status(ctx.conn, jid, JobStatus.SKIPPED)
    return _redirect("/jobs", "Job skipped")


# --------------------------------------------------------------------------- #
# materials — resumes & cover letters (the human-authored inputs)
# --------------------------------------------------------------------------- #
async def materials(request):
    with AgentContext.create() as ctx:
        resumes = [dict(r) for r in resume_state.list_resumes(ctx.conn)]
        covers = [dict(c) for c in cover_state.list_cover_letters(ctx.conn)]
    for r in resumes:
        r["missing"] = not Path(r["file_path"]).exists()
    for c in covers:
        c["missing"] = not Path(c["file_path"]).exists()
    return templates.TemplateResponse(
        request,
        "materials.html",
        {
            "active": "materials",
            "resumes": resumes,
            "covers": covers,
            "serverless": _serverless(),
            "msg": request.query_params.get("msg"),
        },
    )


async def _material_path(ctx: AgentContext, form, kind: str) -> str:
    """Resolve a material's file: an uploaded file (saved under the data dir) or a path."""
    upload = form.get("file")
    filename = getattr(upload, "filename", None)
    if filename:
        content = await upload.read()
        uploads = ctx.config.paths.data_dir / "uploads"
        uploads.mkdir(parents=True, exist_ok=True)
        dest = uploads / Path(filename).name
        dest.write_bytes(content)
        return str(dest)
    path = (form.get("path") or "").strip()
    if path:
        return path
    raise ValueError(f"provide a {kind} file to upload or a file path")


@post_action("/materials")
async def resume_add(request):
    form = await request.form()
    track = (form.get("track") or "").strip()
    if not track:
        return _redirect("/materials", "A track is required (e.g. backend, data_scientist)")
    with AgentContext.create() as ctx:
        file_path = await _material_path(ctx, form, "resume")
        resume_state.add_resume(ctx.conn, track, file_path)
    return _redirect("/materials", f"Added {track} resume")


@post_action("/materials")
async def cover_add(request):
    form = await request.form()
    name = (form.get("name") or "").strip()
    if not name:
        return _redirect("/materials", "A name is required")
    is_template = form.get("template") == "on"
    track = (form.get("track") or "").strip() or None
    with AgentContext.create() as ctx:
        file_path = await _material_path(ctx, form, "cover letter")
        cover_state.add_cover_letter(
            ctx.conn, name, file_path, track=track, is_template=is_template
        )
    return _redirect("/materials", f"Added cover letter “{name}”")


@post_action("/materials")
async def resume_delete(request):
    rid = int(request.path_params["id"])
    with AgentContext.create() as ctx:
        resume_state.delete_resume(ctx.conn, rid)
    return _redirect("/materials", "Resume removed")


@post_action("/materials")
async def cover_delete(request):
    cid = int(request.path_params["id"])
    with AgentContext.create() as ctx:
        cover_state.delete_cover_letter(ctx.conn, cid)
    return _redirect("/materials", "Cover letter removed")


# --------------------------------------------------------------------------- #
# error pages — friendly, template-free (so they render even if templates or the
# DB are the thing that broke), and they point at /healthz for diagnosis.
# --------------------------------------------------------------------------- #
_ERROR_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<title>{title} · jobagent</title>
<style>body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
background:#f4f2ee;color:#1c1b22;display:flex;min-height:100vh;margin:0;align-items:center;
justify-content:center}}.box{{background:#fff;border:1px solid #e4e0d8;border-radius:14px;
padding:32px 36px;max-width:520px}}h1{{margin:0 0 6px;font-size:22px}}p{{color:#6b6975;line-height:1.5}}
a{{color:#4f46e5}}</style></head><body><div class="box">
<h1>{title}</h1><p>{body}</p>
<p><a href="/">← Dashboard</a> &nbsp;·&nbsp; <a href="/healthz">/healthz (diagnostics)</a></p>
</div></body></html>"""


async def not_found(request, exc):
    return HTMLResponse(
        _ERROR_PAGE.format(title="Not found", body=f"No page at <code>{request.url.path}</code>."),
        status_code=404,
    )


async def server_error(request, exc):
    return HTMLResponse(
        _ERROR_PAGE.format(
            title="Something went wrong",
            body="The server hit an unexpected error. Check <code>/healthz</code>, "
            "or set <code>JOBAGENT_WEB_DEBUG=1</code> to see the traceback.",
        ),
        status_code=500,
    )


def create_app(debug: bool | None = None) -> Starlette:
    # Debug mode (tracebacks in the response) is opt-in via env so a hosted,
    # auth-gated deployment can be debugged without a redeploy.
    if debug is None:
        debug = os.environ.get("JOBAGENT_WEB_DEBUG", "").lower() in ("1", "true", "yes")

    routes = [
        Route("/", dashboard, name="dashboard"),
        Route("/healthz", healthz, name="healthz"),
        Route("/favicon.ico", favicon, name="favicon"),
        Route("/guide", guide, name="guide"),
        Route("/jobs", jobs, name="jobs"),
        Route("/jobs/{id:int}/prepare", job_prepare, methods=["POST"], name="job_prepare"),
        Route("/jobs/{id:int}/skip", job_skip, methods=["POST"], name="job_skip"),
        Route("/review", review_list, name="review"),
        Route("/review/{id:int}", review_detail, name="review_detail"),
        Route("/review/{id:int}/submit", review_submit, methods=["POST"], name="review_submit"),
        Route("/tracker", tracker, name="tracker"),
        Route("/tracker/{app_id:int}/outcome", outcome_set, methods=["POST"], name="outcome_set"),
        Route("/materials", materials, name="materials"),
        Route("/materials/resume/add", resume_add, methods=["POST"], name="resume_add"),
        Route("/materials/resume/{id:int}/delete", resume_delete, methods=["POST"], name="resume_delete"),
        Route("/materials/cover/add", cover_add, methods=["POST"], name="cover_add"),
        Route("/materials/cover/{id:int}/delete", cover_delete, methods=["POST"], name="cover_delete"),
        Route("/profile", profile_view, name="profile"),
        Route("/profile", profile_save, methods=["POST"]),
        Route("/companies", companies, name="companies"),
        Route("/companies/add", company_add, methods=["POST"], name="company_add"),
        Route("/companies/{id:int}/toggle", company_toggle, methods=["POST"], name="company_toggle"),
        Route("/companies/{id:int}/delete", company_delete, methods=["POST"], name="company_delete"),
        Route("/run", run_pipeline, methods=["POST"], name="run"),
        Route("/demo", run_demo, methods=["POST"], name="demo"),
    ]

    # Password-gate the whole app iff JOBAGENT_WEB_PASSWORD is set (hosted use).
    middleware = []
    creds = auth_credentials()
    if creds:
        middleware.append(
            Middleware(BasicAuthMiddleware, username=creds[0], password=creds[1])
        )

    exception_handlers = {404: not_found, 500: server_error}
    return Starlette(
        debug=debug,
        routes=routes,
        middleware=middleware,
        exception_handlers=exception_handlers,
    )
