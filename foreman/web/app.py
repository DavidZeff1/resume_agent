"""Foreman control panel — a browser UI a non-coder can drive.

It is a thin view over the SQLite ledger (like jobagent's UI) *plus* controls:
start a run from a form, watch the agents work via live status, and review +
merge. Runs launch in a background thread, so the page returns instantly and
auto-refreshes while the agents work. There is no route that merges or pushes to
a real remote — ``POST /task/{id}/merge`` only records the human's merge.
"""

from __future__ import annotations

import html
import json

from starlette.applications import Starlette
from starlette.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from starlette.routing import Route

from .. import repo as _repo
from ..analytics import compute_stats
from ..db import get_conn
from ..demo import BUGGY_FILES
from ..models import TaskStatus
from ..runner import default_db_path, record_merge, start_run

_RUNNING = (TaskStatus.QUEUED, TaskStatus.PLANNING, TaskStatus.IMPLEMENTING,
            TaskStatus.TESTING, TaskStatus.REVIEWING)

_CSS = """
:root{--paper:#f3f0e9;--ink:#17150f;--mut:#8d877a;--line:#d9d4c6;--card:#fbfaf6;
--cool:#2f4a78;--warm:#b5481f;--green:#3f6b3a;--mono:'SF Mono',ui-monospace,Menlo,monospace;}
*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);
font-family:-apple-system,'Helvetica Neue',Arial,sans-serif;line-height:1.55}
nav{display:flex;gap:1.4rem;align-items:center;padding:.9rem 1.6rem;border-bottom:1px solid var(--line);
background:#fff;position:sticky;top:0;flex-wrap:wrap}
nav .b{font-family:var(--mono);font-weight:700;font-size:1rem}nav .b em{color:var(--warm);font-style:normal}
nav a{color:var(--mut);text-decoration:none;font-size:.86rem}nav a:hover{color:var(--ink)}
nav .cta{margin-left:auto;background:var(--cool);color:#fff;padding:.45em 1em;border-radius:8px;font-size:.82rem}
main{max-width:1000px;margin:0 auto;padding:1.8rem 1.6rem}
h1{font-size:1.6rem;letter-spacing:-.02em;margin:.2rem 0 .4rem}
.sub{color:var(--mut);margin:0 0 1.4rem;font-size:.95rem}
h2{font-size:.95rem;margin:1.7rem 0 .6rem;font-family:var(--mono);color:var(--mut);
font-weight:600;text-transform:uppercase;letter-spacing:.08em}
.cards{display:flex;gap:.7rem;flex-wrap:wrap}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:.9rem 1.1rem;min-width:120px}
.card .n{font-size:1.5rem;font-weight:700}.card .l{font-size:.72rem;color:var(--mut)}
table{width:100%;border-collapse:collapse;font-size:.88rem}
td,th{text-align:left;padding:.55rem .4rem;border-bottom:1px solid var(--line)}
th{font-size:.7rem;text-transform:uppercase;letter-spacing:.06em;color:var(--mut)}
a.t{color:var(--cool);text-decoration:none}a.t:hover{text-decoration:underline}
.pill{font-family:var(--mono);font-size:.68rem;padding:.18em .6em;border-radius:100px;border:1px solid var(--line)}
.s-pr_ready{color:var(--green);border-color:var(--green)}
.s-needs_human{color:var(--warm);border-color:var(--warm)}
.s-merged{background:var(--green);color:#fff;border-color:var(--green)}
.s-implementing,.s-testing,.s-reviewing,.s-planning,.s-queued{color:var(--cool);border-color:var(--cool)}
pre{background:#16140f;color:#d8d2c4;padding:1rem;border-radius:10px;overflow:auto;font-size:.78rem;font-family:var(--mono)}
.mono{font-family:var(--mono);font-size:.8rem}
.btn{font-family:inherit;font-size:.9rem;background:var(--cool);color:#fff;border:none;border-radius:8px;
padding:.6em 1.2em;cursor:pointer;text-decoration:none;display:inline-block}
.btn.warm{background:var(--warm)}.btn.ghost{background:#fff;color:var(--ink);border:1px solid var(--line)}
.hero{display:flex;gap:.8rem;flex-wrap:wrap;margin:0 0 1.4rem}
form.run label{display:block;margin:.9rem 0 .3rem;font-weight:600;font-size:.9rem}
form.run input[type=text],form.run textarea,form.run select{width:100%;max-width:520px;padding:.55em .7em;
border:1px solid var(--line);border-radius:8px;font:inherit;background:#fff}
form.run fieldset{border:1px solid var(--line);border-radius:10px;margin:1rem 0;padding:.8rem 1rem}
form.run fieldset legend{font-size:.78rem;color:var(--mut);text-transform:uppercase;letter-spacing:.06em}
form.run .opt{display:block;font-weight:400;margin:.4rem 0}
.banner{padding:.8rem 1.1rem;border-radius:10px;margin:.6rem 0 1.2rem;font-size:.9rem}
.banner.run{background:rgba(47,74,120,.08);border:1px solid var(--cool);color:var(--cool)}
.banner.ok{background:rgba(63,107,58,.08);border:1px solid var(--green);color:var(--green)}
.banner.warm{background:rgba(181,72,31,.08);border:1px solid var(--warm);color:var(--warm)}
.feed{font-family:var(--mono);font-size:.78rem;color:var(--mut)}
.feed .a{color:var(--ink)}
.empty{color:var(--mut);font-style:italic}
"""


def _layout(title: str, body: str, *, refresh: bool = False) -> str:
    meta = "<meta http-equiv='refresh' content='2'>" if refresh else ""
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"{meta}<title>foreman — {html.escape(title)}</title><style>{_CSS}</style></head><body>"
        "<nav><span class='b'><em>fore</em>man</span>"
        "<a href='/'>Dashboard</a><a href='/review'>Review queue</a>"
        "<a href='/tasks'>Tasks</a><a href='/runs'>Runs &amp; cost</a><a href='/stats'>Stats</a>"
        "<a class='cta' href='/new'>+ New run</a></nav>"
        f"<main>{body}</main></body></html>"
    )


def _pill(status: str) -> str:
    return f"<span class='pill s-{html.escape(status)}'>{html.escape(status)}</span>"


def create_app(db_path: str | None = None) -> Starlette:
    db_path = db_path or str(default_db_path())

    def conn():
        return get_conn(db_path)

    # -- dashboard / control panel ------------------------------------------ #
    async def dashboard(request):
        c = conn()
        stats = compute_stats(c)
        tasks = _repo.list_tasks(c)
        any_running = any(t["status"] in _RUNNING for t in tasks)
        cards = "".join(
            f"<div class='card'><div class='n'>{v}</div><div class='l'>{html.escape(l)}</div></div>"
            for l, v in [("tasks", stats["tasks_total"]), ("PR-ready", stats["reached_pr_ready"]),
                         ("success %", stats["success_rate_pct"]),
                         ("avg turns", stats["avg_implementer_turns"]), ("cost $", stats["total_cost_usd"])]
        )
        rows = "".join(
            f"<tr><td>#{t['id']}</td>"
            f"<td><a class='t' href='/task/{t['id']}'>{html.escape(t['title'])}</a></td>"
            f"<td>{_pill(t['status'])}</td></tr>"
            for t in tasks[:8]
        ) or "<tr><td colspan=3 class='empty'>no runs yet — click “Run the sample task”.</td></tr>"
        hero = (
            "<div class='hero'>"
            "<form method='post' action='/demo' style='display:inline'>"
            "<button class='btn' type='submit'>▶ Run the sample task</button></form>"
            "<a class='btn ghost' href='/new'>+ Start a new run</a></div>"
        )
        body = (
            "<h1>Foreman</h1>"
            "<p class='sub'>A crew of AI agents writes the code; you review and merge. "
            "Start a run below and watch them work.</p>"
            f"{hero}<div class='cards'>{cards}</div>"
            f"<h2>Your runs</h2><table><tr><th>#</th><th>task</th><th>status</th></tr>{rows}</table>"
        )
        return HTMLResponse(_layout("dashboard", body, refresh=any_running))

    # -- new run form ------------------------------------------------------- #
    async def new_form(request):
        body = (
            "<h1>Start a new run</h1>"
            "<p class='sub'>Describe what you want done, pick where and who decides, then run it.</p>"
            "<form class='run' method='post' action='/new'>"
            "<label>Title</label><input type='text' name='title' required placeholder='e.g. Fix the broken add() function'>"
            "<label>Description (optional)</label><textarea name='description' rows='3' "
            "placeholder='What should the result be? Any details the agents need.'></textarea>"
            "<fieldset><legend>What should the agents work on?</legend>"
            "<label class='opt'><input type='radio' name='target' value='demo' checked> "
            "A built-in sample — fixes a planted bug, no setup needed</label>"
            "<label class='opt'><input type='radio' name='target' value='folder'> "
            "A project folder on this computer:</label>"
            "<input type='text' name='repo' placeholder='/path/to/your/project'></fieldset>"
            "<fieldset><legend>Who decides?</legend>"
            "<label class='opt'><input type='radio' name='policy' value='mock' checked> "
            "Scripted — instant and free (works with the built-in sample)</label>"
            "<label class='opt'><input type='radio' name='policy' value='llm'> "
            "Claude — real AI that writes the code (needs an API key set up)</label></fieldset>"
            "<label>AI model (only used for Claude)</label>"
            "<select name='model'><option>claude-opus-4-8</option>"
            "<option>claude-sonnet-4-6</option><option>claude-haiku-4-5</option></select>"
            "<p style='margin-top:1.2rem'><button class='btn' type='submit'>Run it ▶</button> "
            "<a class='btn ghost' href='/'>Cancel</a></p></form>"
        )
        return HTMLResponse(_layout("new run", body))

    async def new_submit(request):
        form = await request.form()
        title = (form.get("title") or "Untitled task").strip()
        description = (form.get("description") or "").strip()
        target = form.get("target") or "demo"
        policy = form.get("policy") or "mock"
        model = form.get("model") or "claude-opus-4-8"
        repo = (form.get("repo") or "").strip() if target == "folder" else ""
        seed = BUGGY_FILES if target == "demo" else None

        c = conn()
        task_id = _repo.create_task(c, title=title, description=description, source="ui")
        start_run(task_id, db_path=db_path, target_repo=repo or None,
                  policy=policy, model=model, seed_files=seed)
        return RedirectResponse(url=f"/task/{task_id}", status_code=303)

    async def run_demo(request):
        c = conn()
        task_id = _repo.create_task(c, title="Sample: fix add()",
                                   description="add(a,b) must return a+b; the test must pass.",
                                   source="demo")
        start_run(task_id, db_path=db_path, seed_files=BUGGY_FILES, policy="mock")
        return RedirectResponse(url=f"/task/{task_id}", status_code=303)

    # -- review queue ------------------------------------------------------- #
    async def review(request):
        c = conn()
        rows = "".join(
            f"<tr><td>#{t['id']}</td>"
            f"<td><a class='t' href='/task/{t['id']}'>{html.escape(t['title'])}</a></td>"
            f"<td class='mono'>{html.escape(t['pr_ref'] or '-')}</td><td>{_pill(t['status'])}</td></tr>"
            for t in _repo.list_tasks(c, status=TaskStatus.PR_READY)
        ) or "<tr><td colspan=4 class='empty'>nothing waiting — the queue is clear.</td></tr>"
        body = ("<h1>Review queue</h1><p class='sub'>Green, reviewed work — one click from merge "
                "(the only human step).</p><table>"
                f"<tr><th>#</th><th>task</th><th>branch</th><th>status</th></tr>{rows}</table>")
        return HTMLResponse(_layout("review queue", body))

    async def tasks(request):
        c = conn()
        rows = "".join(
            f"<tr><td>#{t['id']}</td>"
            f"<td><a class='t' href='/task/{t['id']}'>{html.escape(t['title'])}</a></td>"
            f"<td>{_pill(t['status'])}</td><td class='mono'>{html.escape(t['pr_ref'] or '-')}</td></tr>"
            for t in _repo.list_tasks(c)
        ) or "<tr><td colspan=4 class='empty'>no tasks yet</td></tr>"
        any_running = any(t["status"] in _RUNNING for t in _repo.list_tasks(c))
        body = ("<h1>Tasks</h1><table>"
                f"<tr><th>#</th><th>title</th><th>status</th><th>branch</th></tr>{rows}</table>")
        return HTMLResponse(_layout("tasks", body, refresh=any_running))

    async def task_detail(request):
        c = conn()
        tid = int(request.path_params["id"])
        t = _repo.get_task(c, tid)
        if not t:
            return HTMLResponse(_layout("not found", "<h1>Task not found</h1>"), status_code=404)
        running = t["status"] in _RUNNING

        if running:
            banner = f"<div class='banner run'>⏳ The agents are working… currently <b>{html.escape(t['status'])}</b>. This page refreshes itself.</div>"
        elif t["status"] == TaskStatus.PR_READY:
            banner = "<div class='banner ok'>✅ Ready for you. Review the diff below, then click merge.</div>"
        elif t["status"] == TaskStatus.MERGED:
            banner = "<div class='banner ok'>✔ Merged by you.</div>"
        else:
            banner = f"<div class='banner warm'>⚠ Needs a human.{(' ' + html.escape(t['last_error'])) if t['last_error'] else ''}</div>"

        runs = "".join(
            f"<tr><td>{html.escape(r['role'] or '')}</td><td class='mono'>{html.escape(r['model'] or '')}</td>"
            f"<td>{r['turns']}</td><td>{'' if r['tests_passed'] is None else ('pass' if r['tests_passed'] else 'fail')}</td>"
            f"<td>{html.escape(r['verdict'] or '-')}</td><td>${r['cost_usd']:.4f}</td></tr>"
            for r in _repo.list_runs(c, tid)
        ) or "<tr><td colspan=6 class='empty'>no runs yet</td></tr>"

        feed = "".join(
            f"<div><span class='a'>{html.escape(e['actor'] or '')}</span> "
            f"{html.escape(e['action'])}"
            f"{(' → ' + html.escape(json.loads(e['detail']).get('to',''))) if e['action']=='status' and e['detail'] else ''}</div>"
            for e in _repo.recent_events(c, limit=40, task_id=tid)
        ) or "<div class='empty'>no activity yet</div>"

        diff = ""
        for e in _repo.recent_events(c, limit=200, task_id=tid):
            if e["action"] in ("pr_prepared", "diff") and e["detail"]:
                diff = json.loads(e["detail"]).get("diff") or ""
                if diff:
                    break
        diff_html = f"<pre>{html.escape(diff)}</pre>" if diff else "<p class='empty'>no diff yet</p>"

        merge_btn = ""
        if t["status"] == TaskStatus.PR_READY:
            merge_btn = (f"<form method='post' action='/task/{tid}/merge'>"
                         "<button class='btn warm' type='submit'>Merge this (I approve) ✓</button></form>")

        body = (f"<h1>#{tid} · {html.escape(t['title'])}</h1>{banner}"
                f"<p class='sub mono'>branch: {html.escape(t['branch'] or '-')} · attempts: {t['attempts']}</p>"
                f"{merge_btn}"
                f"<h2>What the agents did</h2><table><tr><th>role</th><th>model</th><th>turns</th>"
                f"<th>tests</th><th>verdict</th><th>cost</th></tr>{runs}</table>"
                f"<h2>Activity</h2><div class='feed'>{feed}</div>"
                f"<h2>Proposed change (diff)</h2>{diff_html}")
        return HTMLResponse(_layout(f"task {tid}", body, refresh=running))

    async def merge(request):
        c = conn()
        tid = int(request.path_params["id"])
        try:
            record_merge(c, tid)
        except Exception as exc:
            return HTMLResponse(_layout("error",
                f"<h1>Could not merge</h1><p>{html.escape(str(exc))}</p>"
                f"<p><a class='btn ghost' href='/task/{tid}'>Back</a></p>"), status_code=400)
        return RedirectResponse(url=f"/task/{tid}", status_code=303)

    async def runs(request):
        c = conn()
        rows = "".join(
            f"<tr><td><a class='t' href='/task/{r['task_id']}'>#{r['task_id']}</a></td>"
            f"<td>{html.escape(r['role'] or '')}</td><td class='mono'>{html.escape(r['model'] or '')}</td>"
            f"<td>{r['turns']}</td><td>{r['input_tokens']}/{r['output_tokens']}</td>"
            f"<td>${r['cost_usd']:.4f}</td></tr>"
            for r in _repo.list_runs(c)
        ) or "<tr><td colspan=6 class='empty'>no runs yet</td></tr>"
        body = ("<h1>Runs &amp; cost</h1><table><tr><th>task</th><th>role</th><th>model</th>"
                f"<th>turns</th><th>tok in/out</th><th>cost</th></tr>{rows}</table>")
        return HTMLResponse(_layout("runs", body))

    async def stats(request):
        c = conn()
        rows = "".join(f"<tr><td class='mono'>{html.escape(k)}</td><td>{html.escape(str(v))}</td></tr>"
                       for k, v in compute_stats(c).items())
        return HTMLResponse(_layout("stats", f"<h1>Analytics</h1><table>{rows}</table>"))

    async def healthz(request):
        try:
            conn().execute("SELECT 1")
            return PlainTextResponse("ok")
        except Exception as exc:
            return PlainTextResponse(f"error: {exc}", status_code=500)

    return Starlette(routes=[
        Route("/", dashboard),
        Route("/new", new_form),
        Route("/new", new_submit, methods=["POST"]),
        Route("/demo", run_demo, methods=["POST"]),
        Route("/review", review),
        Route("/tasks", tasks),
        Route("/task/{id:int}", task_detail),
        Route("/task/{id:int}/merge", merge, methods=["POST"]),
        Route("/runs", runs),
        Route("/stats", stats),
        Route("/healthz", healthz),
    ])
