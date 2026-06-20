"""Starlette application factory and route handlers for the local web UI.

Each handler opens its own :class:`AgentContext` (one SQLite connection per
request, used within a single thread) and closes it — mirroring how every CLI
command works, so the UI shares the exact same dedup/state-machine logic.

There is intentionally no route that submits to a real ATS. ``/review/{id}/
submit`` only *records* that the human submitted, exactly like
``jobagent review submit``.
"""

from __future__ import annotations

from pathlib import Path

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.responses import RedirectResponse
from starlette.routing import Route
from starlette.templating import Jinja2Templates

from ..context import AgentContext
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
        },
    )


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
            return RedirectResponse("/review?msg=No+such+application", status_code=303)
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


async def review_submit(request):
    """Record that the HUMAN submitted on the real site. Never submits itself."""
    app_id = int(request.path_params["id"])
    with AgentContext.create() as ctx:
        app = get_application(ctx.conn, app_id)
        if app is None:
            return RedirectResponse("/review?msg=No+such+application", status_code=303)
        a = decode_application(app)
        if a["status"] != ApplicationStatus.SUBMITTED:
            follow_up = iso_in_days(ctx.config.followup.days_until_followup)
            mark_application_submitted(ctx.conn, app_id, follow_up)
            set_job_status(ctx.conn, a["job_id"], JobStatus.SUBMITTED)
            msg = f"Recorded app+%23{app_id}+as+submitted+by+you"
        else:
            msg = f"App+%23{app_id}+was+already+submitted"
    return RedirectResponse(f"/review?msg={msg}", status_code=303)


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
    return RedirectResponse("/profile?msg=Profile+saved", status_code=303)


async def companies(request):
    with AgentContext.create() as ctx:
        rows = [dict(c) for c in companies_state.list_companies(ctx.conn)]
    return templates.TemplateResponse(
        request,
        "companies.html",
        {"active": "companies", "companies": rows, "msg": request.query_params.get("msg")},
    )


async def company_toggle(request):
    cid = int(request.path_params["id"])
    with AgentContext.create() as ctx:
        c = companies_state.get_company(ctx.conn, cid)
        if c is not None:
            companies_state.set_active(ctx.conn, cid, active=not bool(c["active"]))
    return RedirectResponse("/companies?msg=Watchlist+updated", status_code=303)


async def run_pipeline(request):
    """Run the recoverable pipeline once (source -> score -> tailor -> prep).

    Sourcing is resilient: per-company network errors are isolated and reported,
    so this is safe to trigger even with no network / no API key.
    """
    from ..pipeline import run_once

    form = await request.form()
    with_source = form.get("source") == "on"
    with AgentContext.create() as ctx:
        results = run_once(ctx, source=with_source)
        queued = results.get("queued_for_review", 0)
    return RedirectResponse(
        f"/review?msg=Pipeline+run+complete:+{queued}+queued+for+review", status_code=303
    )


async def run_demo(request):
    from ..demo import seed_demo

    with AgentContext.create() as ctx:
        result = seed_demo(ctx, run=True)
        queued = result.get("pipeline", {}).get("queued_for_review", 0)
    return RedirectResponse(
        f"/review?msg=Demo+seeded:+{queued}+queued+for+review", status_code=303
    )


def create_app(debug: bool = False) -> Starlette:
    routes = [
        Route("/", dashboard, name="dashboard"),
        Route("/jobs", jobs, name="jobs"),
        Route("/review", review_list, name="review"),
        Route("/review/{id:int}", review_detail, name="review_detail"),
        Route("/review/{id:int}/submit", review_submit, methods=["POST"], name="review_submit"),
        Route("/profile", profile_view, name="profile"),
        Route("/profile", profile_save, methods=["POST"]),
        Route("/companies", companies, name="companies"),
        Route("/companies/{id:int}/toggle", company_toggle, methods=["POST"], name="company_toggle"),
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

    return Starlette(debug=debug, routes=routes, middleware=middleware)
