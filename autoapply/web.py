"""Review page. The only place a plan gets approved_by_user and a Submit gets clicked."""
from __future__ import annotations

import csv
import io
import json
import os
import re
import shutil
import time
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile, WebSocket
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from jinja2 import Environment, FileSystemLoader
from starlette.concurrency import run_in_threadpool

from . import stats
from .db import DB
from .fillers import fill, submit

env = Environment(loader=FileSystemLoader(Path(__file__).parent / "templates"), autoescape=True)
env.globals["ws_label"] = "Work search"


def _planner_checked() -> str:
    """When the Your next step rules were compiled, for the "laws change" line on the planner pages (2026-11-24)."""
    try:
        from . import next_steps
        d = next_steps.load_facts().get("checked") or ""
        return datetime.strptime(d, "%Y-%m-%d").strftime("%B %-d, %Y") if d else ""
    except Exception:  # a broken rules file must not take the whole app down
        return ""


env.globals["planner_checked"] = _planner_checked()
env.globals["strptime"] = lambda s: datetime.strptime(str(s)[:10], "%Y-%m-%d").date()
env.filters["hue"] = lambda s: sum(ord(c) * (i + 7) for i, c in enumerate((s or "?").lower())) % 360  # a steady colour per company
# "confirmed" (the stored kind) read as a confirmed interview; it only means the application was received.
# "rejected" reads as "closed" in lists: the count stays exact, it just stops leading (VOICE.md).
env.filters["label"] = lambda k: {"confirmed": "received", "rejected": "closed", "no reply": "no reply yet"}.get(k, k)
from .goals import _lower_words as _lw  # noqa: E402  "DUA" stays "DUA" in a sentence (2026-11-24)
env.filters["lower_words"] = _lw
env.filters["pillcls"] = lambda k: {"rejected": "closed"}.get(k, k)  # a closed application is a neutral fact, not an alarm (red)




def _dayname(iso: str, today: str) -> str:
    """A board card's due day, said the way a person would: Today, Tomorrow, Thu (this week), else Nov 24."""
    try:
        d, t = date.fromisoformat(str(iso)[:10]), date.fromisoformat(str(today)[:10])
    except ValueError:
        return str(iso or "")
    n = (d - t).days
    if n < 0:
        return d.strftime("%b %-d")
    return "Today" if n == 0 else "Tomorrow" if n == 1 else d.strftime("%a") if n < 7 else d.strftime("%b %-d")


env.filters["dayname"] = _dayname
SHORTLIST = 5  # roles on "Ready for you" each day: the prepared ones plus the next best to triage
DECLINE_REASONS = {"fit": "not a fit", "location": "location", "pay": "pay", "applied": "already applied", "other": "other"}

def page(name: str, **ctx) -> HTMLResponse:
    return HTMLResponse(env.get_template(name).render(**ctx))


def _app_version() -> str:
    from importlib.metadata import PackageNotFoundError, version
    try:
        return version("autoapply")
    except PackageNotFoundError:
        return ""



KIND_LABELS = [("confirmed", "Received (auto-reply)"), ("interview", "Interview"), ("rejected", "Rejection"), ("offer", "Offer"),
               ("recruiter", "Recruiter"), ("agency", "Unemployment agency"), ("ignored", "Not job-related")]


def state_rules() -> dict:
    """Every state's work-search rule (autoapply/work_search_states.yaml), with when and where it was checked."""
    import yaml
    data = yaml.safe_load((Path(__file__).parent / "work_search_states.yaml").read_text())
    for r in data["states"].values():
        ww = r.get("waiting_week")  # text in the checked data ("One unpaid week...", "None"), a bool in older files
        r["waiting_week_note"] = (ww if isinstance(ww, str) and not ww.strip().lower().startswith("none")
                                  else (r.get("waiting_week_note") or "") if ww is True else "")
        r["waiting_week"] = bool(r["waiting_week_note"]) if isinstance(ww, str) else bool(ww)
        r["checked"] = str(r.get("checked") or data["checked"])
        r.setdefault("source", data["source"])
    return data["states"]


def general_notes() -> list[str]:
    """Caveats that apply in every state (taxes, severance, reporting part-time pay...)."""
    import yaml
    return yaml.safe_load((Path(__file__).parent / "work_search_states.yaml").read_text()).get("general") or []


def my_state(settings) -> dict | None:
    """The rule for the state picked on the Setup page, or None."""
    code = str((settings.raw.get("work_search") or {}).get("state") or "").upper()
    rules = state_rules()
    return {"code": code, **rules[code]} if code in rules else None


def work_search(settings) -> tuple[str, int]:
    """Label and weekly minimum for the work-search log, e.g. {label: DUA, weekly_minimum: 3}."""
    ws = settings.raw.get("work_search") or {}
    return ws.get("label", "Work search"), int(ws.get("weekly_minimum", 3))


def _field_label(name: str) -> str:
    """A question label back from a form field name. Browsers send a quote in a field name as %22 and a line break as
    %0D%0A (the HTML form encoding), so a label like 'By selecting "I agree", ...' came back with %22 and never matched
    the question again: the answer was saved but never filled, and the consent check stayed red."""
    return name.replace("%22", '"').replace("%0D%0A", "\n").replace("%0A", "\n").replace("%0D", "\r")


def make_app(settings, db: DB) -> FastAPI:
    app = FastAPI()
    shots = settings.data_dir / "screenshots"
    headless = settings.raw.get("browser", {}).get("headless", True)

    from . import auth
    from .prefs import Prefs as _Prefs
    throttle = auth.Throttle()
    if auth.load(settings.data_dir):
        auth.ensure_token(_Prefs(settings.data_dir, settings.raw))  # the `autoapply finish` key, if it went missing

    def _denied(request: Request, to: str):
        """A browser page load goes to `to` (303); anything else (fetch, curl, finish) gets 401."""
        if request.method == "GET" and "text/html" in request.headers.get("accept", ""):
            return RedirectResponse(to, status_code=303)
        return HTMLResponse("sign in first", status_code=401)

    from . import adminpal, demo
    demo_limit = demo.Limiter()
    palstack_cfg = None if demo.ON else adminpal.install(app, settings, db, _app_version())  # off unless connected

    @app.middleware("http")
    async def gate(request: Request, call_next):
        # Sign-in is required everywhere once the account exists, and the account is created first.
        from urllib.parse import quote
        path = request.url.path
        if demo.ON:  # public demo: no sign-in, and only the posts that stay inside the demo's own database
            if path in demo.NO_PAGES:
                return RedirectResponse("/", status_code=303)
            if request.method not in ("GET", "HEAD"):
                try:
                    size = int(request.headers.get("content-length") or 0)
                except ValueError:
                    size = demo.MAX_BODY + 1
                if size > demo.MAX_BODY:
                    return HTMLResponse("too large for the demo", status_code=413)
                if not demo_limit.take(demo.visitor(request.headers, request.client.host if request.client else None)):
                    return HTMLResponse("slow down a little: the demo allows a few changes a minute", status_code=429)
                if not demo.post_allowed(path):
                    if request.headers.get("x-in-page"):
                        return JSONResponse(demo.off_json(), status_code=403)
                    back = demo.back_from_referer(request.headers.get("referer", ""), request.url.netloc)
                    return HTMLResponse(demo.off_page(back), status_code=403)
            return await call_next(request)
        if path.startswith(("/bundle/", "/file/", "/finished/")) and auth.token_ok(request.query_params.get("t")):
            return await call_next(request)  # `autoapply finish` on the laptop: machine key, no cookie
        if path in ("/login", "/logout", "/account"):
            return await call_next(request)  # these routes check for themselves
        if palstack_cfg and path == adminpal.STATS_PATH:
            return await call_next(request)  # adminPal's signed call: the route checks the signature, not a cookie
        if path.startswith("/static/"):
            return await call_next(request)  # the app's own font: the sign-in page needs it too
        account = auth.load(settings.data_dir)
        if auth.valid_cookie(account, request.cookies.get(auth.COOKIE)):
            return await call_next(request)
        if not account:
            t = request.query_params.get("t")
            return _denied(request, "/account" + (f"?t={quote(t)}" if t else ""))
        nxt = path + (f"?{request.url.query}" if request.url.query else "")
        return _denied(request, "/login" + (f"?next={quote(nxt)}" if nxt != "/" else ""))

    def _bare(name: str, status: int = 200, **ctx) -> HTMLResponse:
        # Pre-sign-in pages: no base.html and no inner page() (it would show the nav and the waiting-email count).
        return HTMLResponse(env.get_template(name).render(**ctx), status_code=status)

    def _signed_in(to: str, account: dict) -> RedirectResponse:
        r = RedirectResponse(to, status_code=303)
        r.set_cookie(auth.COOKIE, auth.make_cookie(account), max_age=auth.MAX_AGE, httponly=True, samesite="lax", path="/")
        r.delete_cookie("aa")  # the old token cookie
        return r

    def _upgrade_proof(request: Request, t: str = "") -> bool:
        """An install that had a dashboard password (AUTOAPPLY_TOKEN) and no account yet: only its owner may
        create the account, shown by the old cookie or the old ?t= link. A fresh install has no token yet."""
        if not auth.machine_token():
            return True
        return auth.token_ok(t) or auth.token_ok(request.cookies.get("aa"))

    @app.get("/account")
    def account_page(request: Request, t: str = ""):
        if auth.load(settings.data_dir):
            return RedirectResponse("/", status_code=303)
        return _bare("login.html", mode="create", t=t, proof=_upgrade_proof(request, t), min_len=auth.MIN_LEN)

    @app.post("/account")
    def account_create(request: Request, email: str = Form(""), password: str = Form(""), again: str = Form(""), t: str = Form("")):
        if auth.load(settings.data_dir):
            return RedirectResponse("/", status_code=303)
        if not _upgrade_proof(request, t):
            return _bare("login.html", 403, mode="create", t="", proof=False, min_len=auth.MIN_LEN)
        if err := auth.check_new(email, password, again):
            return _bare("login.html", 400, mode="create", t=t, proof=True, min_len=auth.MIN_LEN, err=err, email=email)
        auth.save(settings.data_dir, email, password)
        auth.ensure_token(_Prefs(settings.data_dir, settings.raw))
        return _signed_in("/", auth.load(settings.data_dir))

    @app.get("/login")
    def login_page(request: Request, next: str = "/"):  # noqa: A002
        account = auth.load(settings.data_dir)
        if not account:
            return RedirectResponse("/account", status_code=303)
        if auth.valid_cookie(account, request.cookies.get(auth.COOKIE)):
            return RedirectResponse(auth.safe_next(next), status_code=303)
        return _bare("login.html", mode="login", next=auth.safe_next(next))

    @app.post("/login")
    def login(request: Request, email: str = Form(""), password: str = Form(""), next: str = Form("/")):  # noqa: A002
        account = auth.load(settings.data_dir)
        if not account:
            return RedirectResponse("/account", status_code=303)
        ip = request.client.host if request.client else "?"
        nxt = auth.safe_next(next)
        if throttle.blocked(ip):
            return _bare("login.html", 429, mode="login", next=nxt, email=email,
                         err="Too many wrong tries. Wait a few minutes, then try again.")
        if not auth.verify(account, email, password):
            throttle.fail(ip)
            return _bare("login.html", 401, mode="login", next=nxt, email=email, err="That email and password don't match.")
        throttle.clear(ip)
        return _signed_in(nxt, account)

    static_root = (Path(__file__).parent / "static").resolve()

    @app.get("/static/{name:path}")
    def static_file(name: str):
        """Files shipped with the app (the Figtree font). Nothing outside autoapply/static is served."""
        p = (static_root / name).resolve()
        if static_root not in p.parents or not p.is_file():
            raise HTTPException(404)
        return FileResponse(p, headers={"cache-control": "public, max-age=31536000, immutable"})

    @app.post("/logout")
    def logout():
        r = RedirectResponse("/login", status_code=303)
        r.delete_cookie(auth.COOKIE, path="/")
        return r

    def load(job_id):
        job = db.get(job_id)
        if not job:
            raise HTTPException(404)
        return job, json.loads(job["plan_json"] or "{}"), json.loads(job["report_json"] or "{}")

    ws_label, _ = work_search(settings)
    env.globals["ws_label"] = ws_label

    from fastapi.exceptions import RequestValidationError
    from starlette.exceptions import HTTPException as StarletteHTTPException
    from fastapi.exception_handlers import http_exception_handler, request_validation_exception_handler
    ERRORS = {400: "That request didn't make sense to the server.", 404: "That page or role isn't here any more.",
              405: "That link only works from a button on the page.", 422: "Something the form needs was missing.",
              500: "Something broke on our side."}

    def _wants_html(request: Request) -> bool:
        return "text/html" in request.headers.get("accept", "")

    def _error_page(request: Request, code: int, detail: str = ""):
        """Forms sent from a page (base.html's script sets X-In-Page) get JSON for the in-app dialog;
        a page opened straight from a link gets the app's error page."""
        if request.headers.get("x-in-page"):
            return JSONResponse({"code": code, "title": ERRORS.get(code, "That didn't work."), "detail": detail}, status_code=code)
        back = request.headers.get("referer") or "/"
        return HTMLResponse(env.get_template("error.html").render(code=code, title=ERRORS.get(code, "That didn't work."),
                                                                  detail=detail, back=back), status_code=code)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException):
        """Browser page loads get an app page with a way back, in-page forms the dialog; fetch and curl keep FastAPI's JSON."""
        if exc.status_code < 400 or not (_wants_html(request) or request.headers.get("x-in-page")):
            return await http_exception_handler(request, exc)
        detail = exc.detail if isinstance(exc.detail, str) and exc.detail not in ("Not Found", "Bad Request", "Method Not Allowed") else ""
        return _error_page(request, exc.status_code, detail)

    @app.exception_handler(RequestValidationError)
    async def _form_error(request: Request, exc: RequestValidationError):
        if not (_wants_html(request) or request.headers.get("x-in-page")):
            return await request_validation_exception_handler(request, exc)
        missing = [str(e.get("loc", ["", ""])[-1]) for e in exc.errors()]
        return _error_page(request, 422, ("Missing: " + ", ".join(missing)) if missing else "")

    @app.exception_handler(Exception)
    async def _crash(request: Request, exc: Exception):
        """A bug on our side: the person sees a plain message (dialog or page), the traceback goes to the log."""
        import logging
        logging.getLogger("autoapply.web").exception("unhandled error on %s", request.url.path)
        if not (_wants_html(request) or request.headers.get("x-in-page")):
            return JSONResponse({"detail": "Internal Server Error"}, status_code=500)
        return _error_page(request, 500, str(exc)[:200])

    def log_week() -> dict | None:
        """This week's applications and the weekly minimum, for the Log an application line ("This makes 4 of 5")."""
        try:
            start = stats.week_start(date.today())
            n = sum(1 for j in db.by_status("submitted") if (j["submitted_at"] or "")[:10] >= start.isoformat())
            return {"n": n, "min": work_search(settings)[1], "start": start.isoformat()}
        except Exception:  # a missing figure must never break a page; the line just does not show
            return None

    def page(name: str, **ctx) -> HTMLResponse:  # noqa: F811  every page shows the waiting-email badge
        from . import setup
        from . import demo
        return HTMLResponse(env.get_template(name).render(demo=demo.ON, pending_count=len(db.pending_events()),
                                                          setup_left=len(setup.status(settings, os.environ)["left"]),
                                                          log_week=log_week(), palstack_report=bool(palstack_cfg),
                                                          ws_claiming=(settings.raw.get("work_search") or {}).get("claiming", True) is not False,
                                                          app_version=_app_version(), **ctx))

    @app.get("/")
    def index(request: Request, msg: str = "", log: str = "", planned: str = ""):
        if settings.raw.get("_from_example"):
            return RedirectResponse("/welcome?step=0", status_code=303)  # fresh install: your situation first
        from . import prep
        from . import next_steps, nudges
        todo = nudges.compute(db, settings.data_dir, settings=settings)
        # The weekly work-search count lives in Your week on the dashboard (11/18); the claim-day item stays a to-do.
        todo = [n for n in todo if not (n.get("kind") == "worksearch" and "behind-" in str(n.get("key")))]
        for n in todo:
            n["compose"] = nudges.gmail_compose_url(n)
        from . import coachday
        coach_card = coachday.card(db, settings, datetime.now().date())
        if coach_card:
            todo = coachday.order_todo(todo, coach_card["followups"])
        from .prefs import Prefs
        from .rank import shortlist
        s = stats.compute(db, floor=work_search(settings)[1])
        more = shortlist(db, settings, SHORTLIST - len(s["ready"]), skip_companies=[j["company"] for j in s["ready"]])
        from . import skills as sk
        if sk.enabled(Prefs(settings.data_dir, settings.raw)) and (my := sk.mine(settings)):
            for j in [*s["ready"], *more]:
                j["skill_line"] = sk.summary(sk.for_job(db.get(j["id"]) or j, my))
        now = datetime.now()
        s["now_hour"] = now.hour
        from . import jobsources
        from . import pal
        src = jobsources.overview(settings, db)
        first_name = (settings.profile or {}).get("first_name", "")
        cel = pal.celebration(db, s, now.date(), label=work_search(settings)[0])
        from . import coach
        ivs = prep.interviews(db, settings.data_dir)
        greet = pal.greeting(db, s, now, first_name)
        wk_plan = coach.plan(db, s["this_week_start"])
        back = greet["state"] == "back"
        from . import goals
        today_goals = goals.today_view(db, now.date())
        last_wk = None if back else goals.last_week(db, now.date())
        lookback = last_wk and goals.lookback(db, now.date())
        met = goals.new_met(db, now.date())
        week_met = next((m for m in met if m["scope"] == "week"), None)
        if week_met and not cel:  # the whole weekly plan met: the horizon's light spreads, once
            cel = {"key": week_met["key"], "kind": "week", "title": "Your plan for the week, done",
                   "line": "You set it and you followed through."}
        # The week planner has its own page (/plan, "Your week" in the nav) since 2026-11-17 (JSP-05): on the dashboard
        # it made the page 3,500px tall. The dashboard asks once a week, in a pop-up, when there is no plan yet.
        popup_key = f"pal:plan-popup:{s['this_week_start'].isoformat()}"
        states = db.nudge_states()
        plan_popup = (not back and not wk_plan and popup_key not in states
                      and f"pal:plan-skip:{s['this_week_start'].isoformat()}" not in states)
        if plan_popup:
            db.set_nudge(popup_key, "done")  # shown once; it goes, and the page stays in the nav
        from . import setup
        st = setup.status(settings, os.environ)
        # Steps that can't run yet aren't failures (install walk 2026-11-22): no email means no email sync, sorting or
        # brief; setup left undone means the daily run is waiting, not broken.
        not_set_up = set() if st["steps"]["gmail"]["done"] else {"sync-email", "classify", "brief"}
        if st["left"]:
            not_set_up.add("daily")
        resp = page("index.html", not_set_up=not_set_up, s=s, shortlist=more, decline_reasons=DECLINE_REASONS, src_on=src["on"], src_total=src["total"], first_name=first_name,
                    interviews=ivs[:6], greet=greet, plan=wk_plan, plan_popup=plan_popup,
                    offer=None if back else coach.offer(db, settings, s, now.date(), bool(ivs)), back=back, if_then=coach.if_then(db),
                    celebrate=cel, rejection=pal.rejection_note(db, now.date()), week_left=_week_left(s, now.date()),
                    todo=todo, coach_card=coach_card, nav="dashboard", running=run_state(), picks_default=Prefs(settings.data_dir, settings.raw).limits()["picks_per_day"],
                    msg=msg[:300], open_log=log == "1", today=now.date().isoformat(),
                    today_goals=today_goals, just_met={m["key"] for m in met},
                    so_far=goals.summary(today_goals["week_so_far"]) if today_goals else "",
                    week_panel=coachday.with_panel(goals.panel(db, now.date(), s["this_week"], s["dua_floor"], work_search(settings)[0]), coach_card),
                    start=_start(s, now.date()), last_wk=last_wk, sit_sign=next_steps.sign(settings, db, now.date()),
                    last_wk_summary=goals.summary(last_wk["did"]) if last_wk else "")
        if request.headers.get("x-in-page"):
            return resp  # an in-page form post following its redirect: the page the person sees comes next, so keep these
        if cel:
            db.set_nudge(cel["key"], "done")  # each celebration plays once
        for m in met:  # each met moment animates once; a week moment waits while a bigger celebration plays
            if m["scope"] != "week" or (cel and cel["key"] == m["key"]):
                db.set_nudge(m["key"], "done")
        return resp

    def _start(s: dict, today: date) -> dict | None:
        """Your week's one place to start: the board's next due step, then the weekly minimum as a checklist line."""
        from . import board
        b = board.summary(db)
        left = max(0, s["dua_floor"] - s["this_week"])
        week = _week_left(s, today) if left else ""
        nxt = b["next"]
        if nxt:
            when = _dayname(nxt["due"], today.isoformat())
            text = f"{nxt['next_step'].rstrip('.')}, for {nxt['company']}" + (f" ({when.lower() if when in ('Today', 'Tomorrow') else when})" if when else "") + "."
            return {"text": f"{text} {week}".strip(), "href": f"/leads?view=board&card={quote(nxt['key'], safe='')}", "cta": "Open board"}
        if b["n"]:
            return {"text": f"{b['n']} role{'' if b['n'] == 1 else 's'} on your board. {week}".strip(), "href": "/leads?view=board", "cta": "Open board"}
        if week:
            return {"text": week, "href": "/leads", "cta": "Find a role"}
        return None

    def _week_left(s: dict, today: date) -> str:
        """The pal's line for the weekly minimum: quiet when covered, one gentle line when short."""
        left = max(0, s["dua_floor"] - s["this_week"])
        if not left:
            return "This week is covered."
        when = "today" if today.weekday() == 5 else "by Saturday"  # weeks run Sunday to Saturday
        return f"{'One more' if left == 1 else f'{left} more'} {when} covers this week."

    @app.post("/applications/log")
    def log_application(request: Request, company: str = Form(""), title: str = Form(""), url: str = Form(""),
                        applied_on: str = Form(""), via: str = Form(""), pay: str = Form(""), back: str = Form("")):
        """One application you sent outside the tool. The same role is never added twice.
        back: the page it was sent from (the "Applied elsewhere" popup and the Applied buttons); the result
        shows there in the in-app dialog. Without it, the dashboard's Your applications section as before."""
        from . import history
        back = back if back.startswith("/") and not back.startswith("//") else ""
        try:
            r = history.log(db, company, title, url=url.strip(), applied_on=applied_on.strip(), via=via, pay=pay.strip())
        except ValueError as e:
            if back or request.headers.get("x-in-page"):
                raise HTTPException(400, str(e))
            return RedirectResponse(f"/leads?view=applied&log=1&msg={quote(str(e))}#applications", status_code=303)
        from . import leads
        leads.link_tracked(db)  # a lead for this role now shows as applied
        db.conn.execute("update leads set status = 'applied' where job_id = ?", (r["id"],))
        db.conn.commit()
        j = r["job"]
        if r["status"] == "already":
            when = (j.get("submitted_at") or "")[:10]
            msg = f"You already have {j['title']} at {j['company']}{' from ' + when if when else ''}, so nothing was added."
        else:
            msg = (f"Added {j['title']} at {j['company']}. That's one more door you knocked on. It counts toward your week,"
                   " and the daily run won't offer that role again.")
        if back:
            path, _, frag = back.partition("#")
            return RedirectResponse(f"{path}{'&' if '?' in path else '?'}done={quote(msg)}{'#' + frag if frag else ''}", status_code=303)
        return RedirectResponse(f"/leads?view=applied&msg={quote(msg)}#applications", status_code=303)

    @app.post("/applications/import")
    async def import_applications(file: UploadFile = File(...)):
        from . import history
        raw = await file.read()
        if len(raw) > 2_000_000:
            return RedirectResponse(f"/leads?view=applied&msg={quote('That file is over 2 MB. Export just the tracker sheet as CSV.')}#applications",
                                    status_code=303)
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("latin-1")
        before = len(db.by_status("submitted"))
        try:
            added = history.import_csv(db, text)
        except Exception as e:  # a malformed CSV shouldn't take the dashboard down
            return RedirectResponse(f"/leads?view=applied&msg={quote('Could not read that CSV: ' + str(e)[:120])}#applications", status_code=303)
        marked = len(db.by_status("submitted")) - before - added
        msg = (f"Imported {added} application{'' if added == 1 else 's'}"
               + (f", and marked {marked} the tool already knew as applied" if marked > 0 else "")
               + ". Ones you already had were left as they were." if added or marked > 0 else
               "Nothing new in that file: every application in it is already here, or it has no Company, Position and Date Applied columns.")
        return RedirectResponse(f"/leads?view=applied&msg={quote(msg)}#applications", status_code=303)

    @app.get("/applications.csv")
    def export_applications():
        from . import history
        return Response(history.export_csv(db), media_type="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="applications-{date.today().isoformat()}.csv"'})

    @app.post("/pal/seen")
    def pal_seen(key: str = Form(...), back: str = Form("/")):
        """Puts away a pal note (a rejection note) so it doesn't come back."""
        from . import auth as _auth
        if key.startswith(("pal:", "celebrate:")):
            db.set_nudge(key, "done")
        return RedirectResponse(_auth.safe_next(back), status_code=303)

    def start_run(**extra) -> bool:
        """Starts scripts/daily.sh in the background (manual trigger). False when a run is already going."""
        import subprocess
        from . import demo
        if demo.ON or run_state():  # the demo never starts the agent
            return False
        script = Path(__file__).resolve().parent.parent / "scripts" / "daily.sh"
        log = open(settings.data_dir / "cron.log", "a")
        env = {**os.environ, "AUTOAPPLY_TRIGGER": "manual", **extra}
        subprocess.Popen(["bash", str(script)], cwd=script.parent.parent, env=env, stdout=log, stderr=subprocess.STDOUT,
                         stdin=subprocess.DEVNULL, start_new_session=True)  # keeps going if the dashboard restarts
        time.sleep(0.5)  # let it take the lock so the page shows it running
        return True

    @app.post("/decline/{job_id:path}")
    def decline(job_id: str, reason: str = Form("other")):
        """Decline from the dashboard: the role leaves Ready for you, and the reason counts in Stats."""
        job, _, _ = load(job_id)
        label = DECLINE_REASONS.get(reason, "other")
        if job["status"] in ("new", "filled", "prepped"):
            db.update(job_id, status="skipped", error=f"Declined ({label})")
            from . import coach
            coach.touch(db)
            from . import leads
            leads.ensure(db)
            db.conn.execute("update leads set status = 'dismissed' where job_id = ?", (job_id,))
            db.conn.commit()
        return RedirectResponse("/#ready-h", status_code=303)

    @app.post("/prepare/{job_id:path}")
    def prepare(job_id: str, mode: str = Form("apply"), back: str = Form("/")):
        """Prepare one role now: tailor and fill it, or with mode=prep only tailor and write the answers (the person
        applies by hand). Uses credits; never submits."""
        job, _, _ = load(job_id)
        from . import coach
        coach.touch(db)
        prep = mode == "prep"
        extra = {"AUTOAPPLY_PREP_ONLY": "1"} if prep else {}
        if job["status"] in (("new",) if prep else ("new", "prepped")) and start_run(AUTOAPPLY_JOB=job_id, AUTOAPPLY_PICKS="1", **extra):
            db.log_run("run-now", True, f"{'prep only' if prep else 'prepare'}: {job_id}")
        from .auth import safe_next
        return RedirectResponse(safe_next(back) or "/", status_code=303)

    def run_state() -> dict | None:
        """The run going right now, from daily.sh's lock and progress line; None when nothing runs."""
        import fcntl
        lock = settings.data_dir / "daily" / ".lock"
        if not lock.exists():
            return None
        with open(lock) as f:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(f, fcntl.LOCK_UN)
                return None  # nobody holds it
            except OSError:
                pass
        try:
            started, trigger, step = (settings.data_dir / "daily" / ".running").read_text().strip().split("\t", 2)
            at = datetime.fromisoformat(started).strftime("%-I:%M %p").lower()
        except (OSError, ValueError):
            started, trigger, step, at = "", "", "", ""
        return {"at": at, "trigger": trigger, "step": step, "label": step_label(step)}

    def step_label(step: str) -> str:
        """daily.sh's progress line in plain words: "apply greenhouse:acme:1" -> "tailoring and filling Acme · Data Engineer"."""
        stage, _, job_id = (step or "").partition(" ")
        what = ""
        if job_id:
            j = db.get(job_id)
            what = f"{j['company']} · {j['title']}" if j else job_id
        return {"alerts": "looking the role up on the company's own site", "rank": "ranking today's roles",
                "apply": f"tailoring and filling {what}".strip(), "prep": f"writing an interview prep pack {what}".strip(),
                "letter": f"rewriting the cover letter {what}".strip(), "drafts": "drafting replies",
                "refilling": f"refilling the form {what}".strip()}.get(stage, step)

    @app.get("/runs")
    def runs_page():
        """The run log: what every run did, step by step, from data/daily/*.md."""
        from . import runlog
        return page("runs.html", days=runlog.recent(settings.data_dir), running=run_state(), nav="dashboard")

    @app.post("/run")
    def run_now(picks: int = Form(2)):
        """Run now: the same pass as the morning run, for more applications today. Never submits anything."""
        from . import coach
        coach.touch(db)
        if start_run(AUTOAPPLY_PICKS=str(max(1, min(5, picks)))):
            db.log_run("run-now", True, f"started with {picks} role{'s' if picks != 1 else ''}")
        return RedirectResponse("/", status_code=303)

    @app.post("/nudge/{key}")
    def nudge_action(key: str, action: str = Form(...), days: int = Form(3)):
        from datetime import timedelta
        if key.startswith("task-") and key[5:].isdigit() and action == "done":  # an unemployment to-do you added
            from . import benefits
            benefits.set_task(db, int(key[5:]), done=True)
            return RedirectResponse("/#todo", status_code=303)
        if action == "done":
            db.set_nudge(key, "done")
            from . import coach
            coach.touch(db)
        elif action == "snooze":
            db.set_nudge(key, "snoozed", (datetime.now() + timedelta(days=max(1, min(days, 30)))).strftime("%Y-%m-%d %H:%M"))
        elif action == "reopen":
            db.set_nudge(key, None)
        else:
            raise HTTPException(400)
        return RedirectResponse("/#todo", status_code=303)

    @app.post("/outreach/{key}")
    def outreach_action(key: str, action: str = Form(...), email: str = Form(""), link: str = Form(""), draft: str = Form("")):
        """The coach's suggestion of someone to reach. The person sends from their own mail; this only keeps track."""
        from pathlib import Path as _P
        from . import coach, coachday
        coachday.ensure(db)
        row = db.conn.execute("select * from outreach where key = ?", (key,)).fetchone()
        if not row:
            raise HTTPException(404)
        row = dict(row)
        if action in ("sent", "not_now"):
            coachday.set_state(db, key, action)
            if action == "sent":
                coach.touch(db)
        elif action == "dismiss":
            coachday.set_state(db, key, "dismissed")
        elif action == "save":
            if draft.strip() and row["draft_path"]:
                p = _P(row["draft_path"])
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(draft.strip()[:5000] + "\n")
            if email.strip() or link.strip():
                coach.set_contact_email(db, row["person"], row["company"] or "", email.strip()[:120], link.strip()[:300])
                db.conn.execute("update outreach set email = ? where key = ?", (email.strip()[:120] or row["email"], key))
                db.conn.commit()
        else:
            raise HTTPException(400)
        return RedirectResponse("/#coach", status_code=303)

    @app.post("/coach/undo-skip/{job_id:path}")
    def coach_undo_skip(job_id: str):
        j = db.get(job_id)
        if j and j["status"] == "skipped" and str(j.get("error") or "").startswith("coach:"):
            db.update(job_id, status="new", error=None)
        return RedirectResponse("/#coach", status_code=303)

    @app.get("/plan")
    def plan_redirect(request: Request):
        """Your week became Your coach (2026-11-21); old links and the morning email still land."""
        q = request.url.query
        return RedirectResponse("/coach" + (f"?{q}" if q else ""), status_code=303)

    @app.get("/coach")
    def plan_page(request: Request, planned: str = ""):
        """Your coach: the morning coach's note and today's steps, things for later this week, then the person's own
        plan for the week, the days, and last week's look-back. The plan stays optional and theirs: a suggested week is
        saved only when they press Use this plan."""
        from . import coach, coachday, goals, nudges, pal
        now = datetime.now()
        s = stats.compute(db, floor=work_search(settings)[1])
        greet = pal.greeting(db, s, now, (settings.profile or {}).get("first_name", ""))
        back = greet["state"] == "back"
        wk_plan = coach.plan(db, s["this_week_start"])
        last_wk = None if back else goals.last_week(db, now.date())
        lookback = last_wk and goals.lookback(db, now.date())
        today_goals = goals.today_view(db, now.date())
        # A goal or day met plays its moment here, where Done is pressed (11/18); the whole week's stays for the dashboard.
        met = [m for m in goals.new_met(db, now.date()) if m["scope"] != "week"]
        coach_card = coachday.card(db, settings, now.date())
        suggested = None if wk_plan else coachday.suggested_totals(coach_card)
        wk_panel = goals.panel(db, now.date(), s["this_week"], s["dua_floor"], work_search(settings)[0])
        aim_rows, people_rows = coachday.page_rows(coach_card)
        resp = page("plan.html", nav="coach", coach_card=coach_card, suggested=bool(suggested),
                    aim_rows=aim_rows, people_rows=people_rows, week_strip=wk_panel["strip"],
                    week_nums=coachday.week_numbers(db, now.date()), ws_label=work_search(settings)[0],
                    today_label=f"{now.strftime('%A')} {now.day} {now.strftime('%B')}",
                    suggested_why=(coach_card or {}).get("week", {}).get("why", "") if suggested else "", s=s, back=back, plan=wk_plan, plan_line=coach.plan_line(wk_plan),
                    today_goals=today_goals, so_far=goals.summary(today_goals["week_so_far"]) if today_goals else "",
                    just_met={m["key"] for m in met},
                    plan_days=coach.DAYS,
                    plan_open=bool(suggested) or (not wk_plan and f"pal:plan-skip:{s['this_week_start'].isoformat()}" not in db.nudge_states()),
                    lookback=lookback, planned=planned == "1",
                    goal_totals=goals.week_goals(wk_plan) or suggested or goals.prefill(db, s["this_week_start"]),
                    week_iso=s["this_week_start"].isoformat(),
                    g_summary=("Goals: " + goals.summary(goals.week_goals(wk_plan))) if goals.week_goals(wk_plan) else "",
                    last_wk=last_wk, last_wk_summary=goals.summary(last_wk["did"]) if last_wk else "",
                    lookback_summary=goals.summary(lookback["did"]) if lookback else "")
        if not request.headers.get("x-in-page"):
            for m in met:
                db.set_nudge(m["key"], "done")
        return resp

    @app.post("/plan")
    async def plan_save(request: Request):
        """The person's own plan for this week. Optional; nothing reminds them of it (reminders added nothing in the trial)."""
        from . import coach
        f = await request.form()
        week = stats.week_start(date.today())
        if f.get("action") == "skip":
            db.set_nudge(f"pal:plan-skip:{week.isoformat()}", "done")
            return RedirectResponse("/plan", status_code=303)
        from . import goals
        totals = goals.parse_totals(f)
        roles, days, contact = str(f.get("roles") or ""), [str(d) for d in f.getlist("days")], str(f.get("contact") or "")
        if any(totals.values()):  # weekly goals: spread over the chosen days (goals.py)
            goals.save_week(db, week, totals, days, roles, contact, date.today())
        else:
            coach.save_plan(db, week, roles, days, contact)
        return RedirectResponse("/plan?planned=1", status_code=303)

    @app.post("/plan/day")
    async def plan_day(request: Request):
        """One day of the weekly goals: mark time done, change it, move what's left to the next chosen day, or
        change a day's planned amounts (the week's total follows)."""
        from . import goals
        f = await request.form()
        action, goal = str(f.get("action") or ""), str(f.get("goal") or "")
        if action == "set":
            try:
                week = stats.week_start(date.fromisoformat(str(f.get("week") or "")))
            except ValueError:
                raise HTTPException(400)
            day = str(f.get("day") or "")
            vals = {g: f.get(f"v_{g}") for g in goals.GOALS if f.get(f"v_{g}") is not None}
            if not vals:
                raise HTTPException(400)
            for g, v in vals.items():
                if goals.set_day(db, week, day, g, v if g == "apps" else goals.parse_totals({f"{g}_h": v})[g]) is None:
                    raise HTTPException(400)
            return RedirectResponse("/plan", status_code=303)
        try:
            day = date.fromisoformat(str(f.get("day") or ""))
        except ValueError:
            raise HTTPException(400)
        if action in ("done", "adjust"):
            if goal not in goals.TIMED:
                raise HTTPException(400)
            mins = None
            if action == "adjust":
                mins = f.get("minutes")
                if mins is None:
                    mins = goals.parse_totals({f"{goal}_h": f.get("hours")})[goal]
            goals.mark_done(db, day, goal, mins)
        elif action == "move":
            if goal not in goals.GOALS:
                raise HTTPException(400)
            goals.move_to_tomorrow(db, day, goal)
        else:
            raise HTTPException(400)
        return RedirectResponse("/#today", status_code=303)

    @app.post("/plan/note")
    def plan_note(week: str = Form(""), text: str = Form("")):
        """The optional look-back note: what helped, or what got in the way. Only the person sees it."""
        from . import goals
        try:
            wk = stats.week_start(date.fromisoformat(week))
        except ValueError:
            raise HTTPException(400)
        goals.save_note(db, wk, text)
        return RedirectResponse("/plan", status_code=303)

    @app.post("/contacts")
    def contact_action(action: str = Form("add"), name: str = Form(""), company: str = Form(""), how: str = Form(""),
                       note: str = Form(""), at: str = Form(""), cid: int = Form(0), counts: str = Form(""),
                       email: str = Form(""), link: str = Form("")):
        """People you got in touch with about work. They go in the work-search log as networking contacts."""
        from . import coach
        if action == "add":
            coach.add_contact(db, name, company, how, note, at, email.strip(), link.strip())
        elif action == "delete" and cid:
            coach.delete_contact(db, cid)
        elif action == "counts":
            coach.set_note(db, "ws:count_contacts", counts == "yes")
        return RedirectResponse("/dua?tab=people#people", status_code=303)

    @app.get("/reflect")
    def reflect_page(back: str = ""):
        from . import auth as _auth
        from . import coach
        return page("reflect.html", values=coach.VALUES, past=coach.reflections(db), back=_auth.safe_next(back) if back else "",
                    nav="dashboard")

    @app.post("/reflect")
    async def reflect_save(request: Request):
        from . import auth as _auth
        from . import coach
        f = await request.form()
        coach.save_reflection(db, [str(v) for v in f.getlist("values")], str(f.get("text") or ""))
        for k in ("pal:values:early", "pal:values:interview"):
            db.set_nudge(k, "done")
        back = str(f.get("back") or "")
        return RedirectResponse(_auth.safe_next(back) if back else "/reflect#saved", status_code=303)

    @app.get("/letters")
    def letters_page(saved: str = "", ex: str = "", err: str = ""):
        """How your cover letters sound: choices with sample lines, words to avoid, notes, letters you liked."""
        from . import voice
        from .setup import base_resume
        return page("letters.html", nav="settings", v=voice.load(db), tones=voice.TONES, lengths=voice.LENGTHS,
                    openings=voice.OPENINGS, samples={f"{t}|{o}": s for (t, o), s in voice.SAMPLES.items()},
                    mine=voice.examples(settings), has_resume=bool(base_resume(settings.root)),
                    ex_done=ex == "1", ex_err=err[:200],
                    common=voice.COMMON_AVOID, recent=voice.recent(settings.data_dir), saved=bool(saved))

    @app.post("/letters/examples")
    async def letters_examples():
        """Writes the style examples from your own resume: one model call, saved for next time."""
        from . import voice
        try:
            await run_in_threadpool(voice.build_examples, settings, _Prefs(settings.data_dir, settings.raw), db)
        except RuntimeError as e:
            return RedirectResponse("/letters?err=" + quote(str(e)[:200]) + "#examples", status_code=303)
        return RedirectResponse("/letters?ex=1#examples", status_code=303)

    @app.post("/letters")
    async def letters_save(request: Request):
        from . import voice
        f = await request.form()
        voice.save(db, settings.root, str(f.get("tone") or ""), str(f.get("length") or ""), str(f.get("opening") or ""),
                   str(f.get("avoid") or ""), str(f.get("notes") or ""), [str(f.get("sample1") or ""), str(f.get("sample2") or "")])
        return RedirectResponse("/letters?saved=1", status_code=303)

    @app.post("/letter/{job_id:path}")
    def letter_rewrite(job_id: str, note: str = Form(""), keep: str = Form("")):
        """Rewrite this one cover letter with your note, then refill the form. Uses credits; never submits."""
        from . import coach, voice
        job, plan, _ = load(job_id)
        note = " ".join(note.split())[:600]
        slug = voice.letter_slug(plan, settings.data_dir)
        if job["status"] == "submitted" or not slug:
            return RedirectResponse(f"/job/{job_id}", status_code=303)
        coach.touch(db)
        coach.set_note(db, f"letter-note:{job_id}", {"note": note, "at": datetime.now().strftime("%Y-%m-%d %H:%M")})
        if keep and note:
            voice.add_note(db, settings.root, note)
        if start_run(AUTOAPPLY_JOB=job_id, AUTOAPPLY_LETTER="1"):
            db.log_run("run-now", True, f"letter: {job_id}")
            db.update(job_id, error=None)
        else:
            db.update(job_id, error="Another run is going. Try the rewrite again when it finishes.")
        return RedirectResponse(f"/job/{job_id}#letter", status_code=303)

    @app.post("/setback")
    def setback_save(if_then: str = Form(""), key: str = Form("")):
        """Saves the person's own plan for a hard day; shown again with the next rejection."""
        from . import coach
        coach.set_note(db, "setback:ifthen", " ".join(if_then.split())[:200] or None)
        if key.startswith("pal:setback:"):
            db.set_nudge(key, "done")
        return RedirectResponse("/", status_code=303)

    @app.post("/titles/add")
    def titles_add(title: str = Form(...), back: str = Form("/skills#titles")):
        """Adds a related title to the search (config.yaml), so the next run looks for it too."""
        from . import auth as _auth
        from . import setup
        setup.add_title(settings, title)
        return RedirectResponse(_auth.safe_next(back), status_code=303)

    @app.get("/leads")
    def leads_page(show: str = "open", more: int = 0, view: str = "inbox", card: str = "", moved: str = "", done: str = "",
                   msg: str = "", log: str = ""):
        """Leads has three views (11/18): Inbox (every lead, as before), Board (the roles you starred, worked step by
        step) and Applied (the applications list that used to sit on the dashboard)."""
        from . import board, leads
        from .prefs import Prefs
        from .rank import Applied
        view = view if view in ("inbox", "board", "applied") else "inbox"
        cols = board.columns(db)
        n_board = sum(len(c["cards"]) for c in cols)
        common = dict(nav="leads", view=view, n_board=n_board, running=run_state(), done=done[:300], msg=(msg or done)[:300], open_log=log == "1",
                      today=date.today().isoformat())
        if view == "board":
            every = [k for c in cols for k in c["cards"]]
            for k in every:
                k["want_lookup"] = (leads.get(db, k["key"]) or {}).get("want_lookup")
            sel = next((k for k in every if k["key"] == card), every[0] if every else None)
            return page("leads.html", cols=cols, sel=sel, card=card, moved=moved, steps=board.STEPS,
                        n_inbox=len(leads.rows(db, "new")) + len(leads.rows(db, "starred")),
                        s=stats.compute(db, floor=work_search(settings)[1]), **common)
        if view == "applied":
            s = stats.compute(db, floor=work_search(settings)[1])
            return page("leads.html", s=s, n_inbox=len(leads.rows(db, "new")) + len(leads.rows(db, "starred")), **common)
        every = leads.rows(db)
        applied = Applied(db, int(Prefs(settings.data_dir, settings.raw).data.get("reapply_after_days", 90) or 0))
        for r in every:
            r["applied"] = "" if r["job_id"] else applied.why(r["company"], r["title"], r["url"])
        groups = {"open": [r for r in every if r["status"] in ("new", "starred") and not r["job_id"]],
                  "starred": [r for r in every if r["status"] == "starred"],
                  "tracked": [r for r in every if r["job_id"]],
                  "dismissed": [r for r in every if r["status"] == "dismissed"], "all": every}
        show = show if show in groups else "open"
        tabs = [("open", "To look at"), ("starred", "Starred"), ("tracked", "Tracked"), ("dismissed", "Dismissed"), ("all", "All")]
        from . import jobsources
        src = jobsources.overview(settings, db)
        cap = 2000 if more else 150  # 500 rows made the page 1.2-1.4 MB (11/10)
        shown = groups[show][:cap]
        leads.explain_scores(shown, settings)  # what each score is made of, for the hover on the Score column
        return page("leads.html", rows=shown, total=len(groups[show]), more=more, tabs=[(k, l, len(groups[k])) for k, l in tabs], show=show,
                    src=src, src_open=src["on"] < src["total"] // 2 or not every, n_inbox=len(groups["open"]),
                    s=stats.compute(db, floor=work_search(settings)[1]), **common)

    # ---------- the board (11/18): plain forms, each back to the board with the card still picked ----------

    def _to_board(key: str, moved: bool = False, msg: str = "") -> RedirectResponse:
        q = f"/leads?view=board&card={quote(key, safe='')}" + ("&moved=" + quote(key, safe="") if moved else "")
        return RedirectResponse(q + (f"&done={quote(msg)}" if msg else ""), status_code=303)

    @app.post("/board/add")
    def board_add(key: str = Form(""), job: str = Form(""), back: str = Form("")):
        from . import board
        if job:
            j = db.get(job)
            if not j:
                raise HTTPException(404)
            key = board.add_job(db, j)
        elif not board.add(db, key):
            raise HTTPException(404)
        if back.startswith("/") and not back.startswith("//"):
            return RedirectResponse(back, status_code=303)
        return _to_board(key, moved=True)

    @app.post("/board/remove")
    def board_remove(key: str = Form(...)):
        from . import board
        board.remove(db, key)
        return RedirectResponse("/leads?view=board", status_code=303)

    @app.post("/board/move")
    def board_move(key: str = Form(...), col: int = Form(...)):
        from . import board
        board.move(db, key, col)
        return _to_board(key, moved=True)

    @app.post("/board/step")
    def board_step(key: str = Form(...), i: int = Form(...)):
        from . import board
        try:
            board.toggle_step(db, key, i)
        except ValueError:
            raise HTTPException(400)
        return _to_board(key)

    @app.post("/board/save")
    def board_save(key: str = Form(...), next_step: str = Form(""), due: str = Form(""), notes: str = Form("")):
        from . import board
        board.save(db, key, next_step, due, notes)
        return _to_board(key)

    @app.post("/board/lookup")
    def board_lookup(key: str = Form(...)):
        from . import leads
        leads.want_lookup(db, key)
        return _to_board(key)

    @app.post("/board/applied")
    def board_applied(key: str = Form(...)):
        """The card's role, logged as sent (the same path as Applied elsewhere); the card leaves for Applied."""
        from . import board, history, leads
        r = leads.get(db, key)
        if not r:
            raise HTTPException(404)
        try:
            done = history.log(db, r["company"], r["title"], url=(r.get("apply_url") or r.get("url") or "").strip())
        except ValueError as e:
            raise HTTPException(400, str(e))
        leads.link_tracked(db)
        db.conn.execute("update leads set status = 'applied', job_id = coalesce(job_id, ?) where key = ?", (done["id"], key))
        db.conn.commit()
        board.sync_stars(db)
        j = done["job"]
        msg = (f"Sent. Added {j['title']} at {j['company']} to Applied. That's one more door you knocked on, and it counts "
               "toward your week.") if done["status"] != "already" else f"{j['title']} at {j['company']} was already in Applied."
        return RedirectResponse(f"/leads?view=board&done={quote(msg)}", status_code=303)

    @app.post("/leads/apply")
    def lead_apply(key: str = Form(...)):
        """Auto apply on one lead: find it on the company's site, tailor and fill. Never submits."""
        from . import leads
        if not leads.get(db, key) or run_state():
            return RedirectResponse("/leads", status_code=303)
        leads.set_status(db, key, "starred")
        if start_run(AUTOAPPLY_LEAD=key, AUTOAPPLY_PICKS="1"):
            db.log_run("run-now", True, f"auto apply: {key}")
        return RedirectResponse("/leads?show=starred", status_code=303)

    # ---------- Paste a job (11/10): any job post in, one page per job ----------

    NEEDS_TEXT = ("This site needs the post's text: open the job, select the whole page (Cmd+A or Ctrl+A), "
                  "copy the whole page and paste it here.")

    @app.post("/leads/paste")
    def lead_paste(text: str = Form("")):
        """Reads a pasted job post (or a link) and shows the fields to check before anything is saved."""
        from . import paste
        text = text[:200000]
        if paste.is_link(text):
            from . import demo
            fields = None if demo.ON else paste.from_link(text.strip())  # the demo never fetches a URL
            if not fields:
                return page("lead.html", nav="leads", mode="confirm", fields={"url": text.strip()}, problem=NEEDS_TEXT, pasted="")
        else:
            fields = paste.read(text)
        problem = "" if fields.get("title") and fields.get("company") else "Couldn't tell the company or the title: fill them in below."
        return page("lead.html", nav="leads", mode="confirm", fields=fields, problem=problem, pasted=text)

    @app.post("/leads/add")
    def lead_add(company: str = Form(""), title: str = Form(""), location: str = Form(""), work_setting: str = Form(""),
                 pay: str = Form(""), url: str = Form(""), description: str = Form("")):
        from . import leads
        fields = {"company": company, "title": title, "location": location, "work_setting": work_setting, "pay": pay,
                  "url": url, "description": description[:20000]}
        try:
            r = leads.add_pasted(db, settings, fields)
        except ValueError as e:
            return page("lead.html", nav="leads", mode="confirm", fields=fields, problem=str(e), pasted="")
        if r["result"] == "applied":
            msg = f"Nothing added: you {r['why']}."
            return RedirectResponse(f"/leads?done={quote(msg)}#leads", status_code=303)
        return RedirectResponse(f"/lead/{quote(r['key'], safe='')}", status_code=303)

    def _lead_or_404(key: str) -> dict:
        from . import leads
        r = leads.get(db, key)
        if not r:
            raise HTTPException(404)
        return r

    @app.get("/lead/{key:path}")
    def lead_page(key: str):
        """A lead's own page. Once it has a job (a run found or built one), the job's page is the lead's page."""
        r = _lead_or_404(key)
        if r["job_id"] and db.get(r["job_id"]):
            return RedirectResponse(f"/job/{quote(r['job_id'], safe=':')}", status_code=303)
        from .prefs import Prefs
        from .rank import Applied
        applied = Applied(db, int(Prefs(settings.data_dir, settings.raw).data.get("reapply_after_days", 90) or 0))
        r["applied"] = applied.why(r["company"], r["title"], r["url"])
        return page("lead.html", nav="leads", mode="view", lead=r, running=run_state())

    @app.post("/lead/{key:path}/run")
    def lead_run(key: str, mode: str = Form("apply")):
        """Prep only or Auto apply on one lead. Uses credits; never submits."""
        from . import leads
        _lead_or_404(key)
        if not run_state():
            leads.set_status(db, key, "starred")
            extra = {"AUTOAPPLY_PREP_ONLY": "1"} if mode == "prep" else {}
            if start_run(AUTOAPPLY_LEAD=key, AUTOAPPLY_PICKS="1", **extra):
                db.log_run("run-now", True, f"{'prep only' if mode == 'prep' else 'auto apply'}: {key}")
        return RedirectResponse(f"/lead/{quote(key, safe='')}", status_code=303)

    @app.post("/lead/{key:path}/description")
    def lead_description(key: str, text: str = Form("")):
        from . import leads, paste
        _lead_or_404(key)
        body = text.strip()
        if body and ("about the job" in body.lower() or body.startswith("Company, ")):
            body = paste.read(body)["description"] or body  # a whole LinkedIn page: keep only the job's text
        leads.set_description(db, key, body)
        return RedirectResponse(f"/lead/{quote(key, safe='')}", status_code=303)

    @app.post("/leads")
    def lead_action(key: str = Form(...), status: str = Form(...), show: str = Form("open")):
        from . import board, leads
        try:
            leads.set_status(db, key, status)
        except ValueError:
            raise HTTPException(400)
        if status == "starred":
            board.add(db, key)  # a star is the way onto the board
        else:
            board.remove(db, key)
        return RedirectResponse(f"/leads?show={show}#leads", status_code=303)

    @app.get("/stats")
    def stats_page(tab: str = "overview"):
        from . import next_steps
        d = stats.detail(db, floor=work_search(settings)[1])
        return page("stats.html", s=d, head=stats.headline(d), nav="stats", plan_progress=next_steps.progress(db, date.today()),
                    tab=tab if tab in ("overview", "pipeline", "sources", "work") else "overview")

    @app.get("/skills")
    def skills_page():
        from . import learn
        from . import coach
        lp = learn.plan(db)
        if lp and lp.get("status") == "writing" and lp.get("made_at", "") < (datetime.now() - timedelta(minutes=4)).strftime("%Y-%m-%d %H:%M"):
            lp = {**lp, "status": "failed"}  # the thread died (a restart): say so instead of waiting forever
        return page("learn.html", g=learn.gaps(db, settings), lp=lp, nav="skills", related=coach.related_titles(db, settings),
                    my_titles=(settings.raw.get("search") or {}).get("title_keywords") or [])

    @app.post("/skills/plan")
    async def skills_plan(request: Request):
        """A learning plan for up to three picked skills: one model call, in a thread; the page shows it when it lands."""
        from . import learn
        picks = [str(x) for x in (await request.form()).getlist("picks")]
        try:
            learn.start_plan(db, settings, picks)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return RedirectResponse("/skills#plan", status_code=303)

    @app.post("/skills/plan/start")
    def skills_plan_start():
        """Start this plan: its skills move to Learning now (the same mark as before), and the plan says it started."""
        from . import coach, learn
        lp = learn.plan(db)
        if not lp or lp.get("status") != "ready":
            raise HTTPException(400)
        for sk in lp.get("skills") or []:
            learn.set_mark(settings, sk, "learning")
        coach.set_note(db, learn.PLAN_KEY, {**lp, "started": datetime.now().strftime("%Y-%m-%d")})
        return RedirectResponse("/skills#plan", status_code=303)

    @app.post("/skills/mark")
    def skills_mark(skill: str = Form(...), mark: str = Form(...)):
        from . import learn
        learn.set_mark(settings, skill, mark)
        return RedirectResponse("/skills", status_code=303)

    @app.get("/job/{job_id:path}")
    def job_page(job_id: str):
        job, plan, report = load(job_id)
        shown = {k: v for k, v in plan.items() if k not in ("approved", "approved_by_user")}
        files = [(slot, Path(p).name) for slot, p in plan.get("files", {}).items() if _servable(p)]
        from . import preflight
        checks = preflight.for_job(db, settings, job_id) if job["status"] != "submitted" else []
        from . import coach, voice
        rs = run_state()
        letter = {"can": job["status"] != "submitted" and bool(voice.letter_slug(plan, settings.data_dir)),
                  "last": coach.note(db, f"letter-note:{job_id}"),
                  "busy": bool(rs and rs["step"] in (f"letter {job_id}", f"refilling {job_id}")),
                  "voice_set": voice.is_set(db)}
        from . import finish, leads, notesdoc
        leads.ensure(db)
        lead = db.conn.execute("select * from leads where job_id = ?", (job_id,)).fetchone()
        docx = {slot: Path(p).with_suffix(".docx").name for slot, p in plan.get("files", {}).items()
                if _servable(str(Path(p).with_suffix(".docx")))}
        return page("job.html", nav="", job=job, report=report, approved={_field_label(k): v for k, v in (plan.get("approved") or {}).items()}, files=files, checks=checks,
                    blocked=preflight.blocking(checks), letter=letter, lead=dict(lead) if lead else None,
                    answers=plan.get("answers", {}), docx=docx, running=rs, apply_link=_web_link(job.get("apply_url") or job.get("url") or ""),
                    notes=(n := _notes(plan)), notes_html=notesdoc.render(n), plan_text=json.dumps(shown, indent=1),
                    handoff=(h := _handoff(job, report)), command_ran=finish.ran(db, job_id),
                    finish_box=_finish_box(health_check=bool(h) or job["status"] == "filled", with_version=False),
                    sent_mark=finish.last_mark(db, job_id) if job["status"] == "submitted" else None)

    def _finish_box(health_check: bool = True, with_version: bool = True) -> dict | None:
        """Finish it here, for a page: None when it's off; else whether the browser box answers (and its version)."""
        from . import finish_here
        box = finish_here.box_from(settings)
        if not box:
            return None
        h = finish_here.health(box, timeout=1.5, with_version=with_version) if health_check else {"ok": False, "version": "", "error": ""}
        return {**h, "app_version": _app_version()}

    def _web_link(u: str) -> str:
        from .history import _web
        return _web(u)

    def _handoff(job: dict, report: dict) -> str:
        # The bot never works around captchas: when one blocks Submit, you finish on the site.
        if job["status"] == "submitted" or report.get("submitted"):
            return ""
        errs = " ".join(str(e.get("error", "")) for e in report.get("errors", []))
        if "captcha" in errs.lower():
            return "The site showed a captcha, which only you can answer."
        if report.get("submit_clicked"):
            return "Submit was pressed but no confirmation came back."
        return ""

    @app.get("/shot/{job_id:path}")
    def shot(job_id: str):
        job, _, _ = load(job_id)
        p = Path(job["screenshot"] or "")
        if not p.exists() or shots.resolve() not in p.resolve().parents:
            raise HTTPException(404)
        return FileResponse(p)

    def _servable(p: str) -> bool:
        # Only files inside data/ or materials/: the answers box is editable, so never serve arbitrary paths.
        try:
            rp = Path(p).resolve()
        except (OSError, TypeError):
            return False
        roots = [settings.data_dir.resolve(), (settings.root / "materials").resolve()]
        return rp.is_file() and any(r in rp.parents for r in roots)

    def _notes(plan: dict) -> str:
        # notes.md sits next to the tailored resume (data/applications/<slug>/notes.md).
        for p in plan.get("files", {}).values():
            n = Path(p).parent / "notes.md"
            if _servable(str(n)):
                return n.read_text()[:20000]
        return ""

    @app.get("/notes/{job_id:path}")
    def notes_page(job_id: str):
        """The application's notes as a page of their own, to read through or print."""
        from . import notesdoc
        job, plan, _ = load(job_id)
        body = notesdoc.render(_notes(plan))
        if not body:
            raise HTTPException(404)
        return page("notes.html", job=job, body=body, nav="", posting_link=_web_link(job.get("url") or ""))

    def _lead_status(job_id: str):
        from . import leads
        leads.ensure(db)
        row = db.conn.execute("select status from leads where job_id = ?", (job_id,)).fetchone()
        return row[0] if row else None

    def _set_lead(job_id: str, status):
        if status is not None:
            db.conn.execute("update leads set status = ? where job_id = ?", (status, job_id))
            db.conn.commit()

    @app.get("/job-status/{job_id:path}")
    def job_status(job_id: str):
        """The job page asks this every few seconds, so a mark from `autoapply-finish` shows without a reload."""
        job, _, _ = load(job_id)
        return {"status": job["status"], "submitted_at": job.get("submitted_at")}

    def _mark_finished(job_id: str, confirmation: str) -> dict:
        """The site's confirmation page showed (`autoapply finish`, or Finish it here): mark it sent with the command.
        Undo is on the job page."""
        from . import finish
        job, _, _ = load(job_id)
        if job["status"] == "submitted":
            return {"ok": True, "already": True}
        before = {"status": job["status"], "lead": _lead_status(job_id)}
        db.update(job_id, status="submitted", error=None, submitted_at=time.strftime("%Y-%m-%d %H:%M"))
        _set_lead(job_id, "applied" if before["lead"] is not None else None)
        finish.mark(db, job_id, "auto", before, (confirmation or "")[:200])
        _record_applied(job, None)
        return {"ok": True}

    @app.post("/finished/{job_id:path}")
    async def finished(job_id: str, request: Request):
        try:
            conf = str((await request.json()).get("confirmation") or "")
        except ValueError:
            conf = ""
        return _mark_finished(job_id, conf)

    @app.get("/bundle/{job_id:path}")
    def bundle(job_id: str):
        """Everything `autoapply finish` needs to fill the form in a visible browser on your laptop."""
        job, plan, _ = load(job_id)
        plan = {**plan, "approved_by_user": False}
        from . import finish
        finish.record_run(db, job_id)  # only `autoapply-finish` asks for this: count each run
        slots = {slot: Path(p).name for slot, p in plan.get("files", {}).items() if _servable(p)}
        return {"job": {k: job[k] for k in ("id", "company", "title", "url", "apply_url")},
                "plan": plan, "files": slots, "profile": settings.profile}

    @app.get("/file/{slot}/{job_id:path}")
    def doc(slot: str, job_id: str, kind: str = ""):
        _, plan, _ = load(job_id)
        p = plan.get("files", {}).get(slot)
        if p and kind == "docx":
            p = str(Path(p).with_suffix(".docx"))  # the Word copy build-docs writes next to each PDF
        if not p or not _servable(p):
            raise HTTPException(404)
        return FileResponse(p)

    # ---- Finish it here: the form filled in the browser on this server, finished from any browser ----
    from . import finish_here
    _box = finish_here.box_from(settings)
    app.state.finish_sessions = finish_here.Sessions(_box) if _box else None
    from . import remake as _remake
    app.state.remaker = _remake.Remaker()

    def _sessions():
        if not finish_here.box_from(settings) or app.state.finish_sessions is None:
            raise HTTPException(404)
        return app.state.finish_sessions

    @app.websocket("/finish-here/vnc")
    async def finish_here_vnc(ws: WebSocket):
        """The live picture. The HTTP sign-in middleware does not run for WebSockets, so this checks for itself:
        signed in, opened by a page on this same site, and a session that is open."""
        from urllib.parse import urlsplit
        from . import vnc_relay
        if not auth.valid_cookie(auth.load(settings.data_dir), ws.cookies.get(auth.COOKIE)):
            return await ws.close(code=4401)
        origin = urlsplit(ws.headers.get("origin") or "").netloc
        if not origin or origin != ws.headers.get("host"):
            return await ws.close(code=4403)
        box, ss = finish_here.box_from(settings), app.state.finish_sessions
        if not box or ss is None or ss.status()["state"] not in ("filling", "ready", "sent"):
            return await ws.close(code=4404)
        await vnc_relay.relay(ws, box.host, box.vnc_port, ss.touch)

    @app.get("/finish-here/status")
    def finish_here_status():
        return _sessions().status()

    @app.post("/finish-here/paste")
    def finish_here_paste(text: str = Form("")):
        return {"ok": _sessions().paste(text)}

    @app.post("/finish-here/close")
    def finish_here_close():
        ss = _sessions()
        job_id = ss.status()["job_id"]
        ss.close()
        return RedirectResponse(f"/job/{quote(job_id, safe=':/')}" if job_id else "/", status_code=303)

    @app.post("/finish-here/{job_id:path}")
    def finish_here_start(job_id: str):
        ss = _sessions()
        from . import finish
        job, plan, _ = load(job_id)
        plan = {**plan, "approved_by_user": False,
                "files": {k: p for k, p in (plan.get("files") or {}).items() if _servable(p)}}
        r = ss.start(job_id, plan, settings.profile, mark_sent=_mark_finished)
        if "busy" in r:  # the same job: back to its view; another job: say one is open
            extra = "" if r["busy"] == job_id else "?busy=1"
            return RedirectResponse(f"/finish-here/{quote(r['busy'], safe=':/')}{extra}", status_code=303)
        finish.record_run(db, job_id)  # counted like a run of the laptop command
        return RedirectResponse(f"/finish-here/{quote(job_id, safe=':/')}", status_code=303)

    @app.get("/finish-here/{job_id:path}")
    def finish_here_page(job_id: str, busy: int = 0):
        st = _sessions().status()
        job, _, _ = load(job_id)
        return page("finish_here.html", job=job, st=st, busy=bool(busy), here=st["job_id"] == job_id)

    proof_root = settings.data_dir / "proof"

    def _record_applied(job: dict, shot: str | None):
        """An 'applied' event for the DUA record, with the after-submit screenshot as proof."""
        when = datetime.now()
        proof = None
        if shot and Path(shot).exists():
            from .emailsync import week_dir
            d = week_dir(proof_root, when)
            d.mkdir(parents=True, exist_ok=True)
            proof = str(d / f"{when:%Y-%m-%d_%H%M}_{job['company']}_applied.png")
            shutil.copy(shot, proof)
        db.add_event({"id": f"applied:{job['id']}", "job_id": job["id"], "kind": "applied", "at": f"{when:%Y-%m-%d %H:%M}",
                      "sender": job["company"], "subject": f"Applied: {job['title']}", "proof": proof})

    @app.post("/sync")
    async def sync_now():
        from . import emailsync
        try:
            counts = await run_in_threadpool(emailsync.sync, db, 7, settings.raw.get("recruiter_senders"), proof_root)
            db.log_run("sync-email", True, ", ".join(f"{v} {k}" for k, v in counts.items()) or "nothing new")
            try:
                from . import leads
                await run_in_threadpool(leads.sync, db, settings, 3)
            except Exception:
                pass
            from .cli import _review
            await run_in_threadpool(_review, settings, db)
        except Exception as e:  # shown on the strip; no traceback page
            db.log_run("sync-email", False, str(e))
        return RedirectResponse("/emails" if db.pending_events() else "/", status_code=303)

    @app.get("/emails")
    def emails(show: str = ""):
        from . import classify
        every = [e for e in db.events(all=True) if e["kind"] != "applied"]
        groups = {"waiting": [e for e in every if not e["reviewed"]],
                  "replies": [e for e in every if e["reviewed"] and e["kind"] in ("confirmed", "interview", "rejected", "offer")],
                  "recruiter": [e for e in every if e["reviewed"] and e["kind"] == "recruiter"],
                  "agency": [e for e in every if e["reviewed"] and e["kind"] == "agency"],
                  "ignored": [e for e in every if e["reviewed"] and e["kind"] == "ignored"],
                  "all": every}
        if show not in groups:  # land on the first tab with something in it
            show = next((k for k in ("waiting", "replies", "recruiter") if groups[k]), "all")
        tabs = [("waiting", "Not sorted", len(groups["waiting"])), ("replies", "Replies", len(groups["replies"])),
                ("recruiter", "Recruiters", len(groups["recruiter"])), ("agency", "Unemployment", len(groups["agency"])), ("ignored", "Not job-related", len(groups["ignored"])),
                ("all", "All", len(every))]
        from .prefs import PROVIDERS, Prefs
        chain = Prefs(settings.data_dir, settings.raw).chain()
        name = " then ".join(PROVIDERS[p]["name"] for p in chain[:2]) if chain else "Nobody (no model source is on)"
        jobs = sorted(db.by_status("submitted", "filled", "prepped"), key=lambda j: (j["company"] or "").lower())
        return page("emails.html", rows=groups[show][:300], tabs=tabs, show=show, kinds=KIND_LABELS, jobs=jobs,
                    classifier=name, nav="emails")

    @app.post("/emails/{event_id:path}")
    def set_email(event_id: str, kind: str = Form(""), job: str = Form(""), show: str = Form("waiting")):
        from . import emailsync
        ev = db.get_event(event_id)
        if not ev:
            raise HTTPException(404)
        job_id = None if job == "none" else (job or "")
        if not kind:  # only the application changed on an unsorted email
            if ev["reviewed"]:
                kind = ev["kind"]
            else:
                db.set_event(event_id, job_id=job_id or None)
                return RedirectResponse(f"/emails?show={show}", status_code=303)
        try:
            emailsync.review_event(db, event_id, kind, job_id=job_id, manual=True)
            from . import coach
            coach.touch(db)
        except (KeyError, ValueError) as e:
            raise HTTPException(400, str(e))
        return RedirectResponse(f"/emails?show={show}", status_code=303)

    @app.get("/prep/{job_id:path}")
    def prep_page(job_id: str):
        from . import prep
        job = db.get(job_id)
        if not job:
            raise HTTPException(404)
        row = next((r for r in prep.interviews(db, settings.data_dir) if r["job"]["id"] == job_id), None)
        p = prep.prep_path(settings.data_dir, job)
        body = prep.render(p.read_text()) if p.exists() else ""
        from . import coach
        return page("prep.html", job=job, row=row, body=body, nav="dashboard", reflected=bool(coach.reflections(db)))

    @app.get("/settings")
    def settings_page(msg: str = "", tab: str = "sorting"):
        if tab == "setup":
            return RedirectResponse("/setup", status_code=303)
        tab = tab if tab in ("sorting", "daily", "costs") else "sorting"
        from . import classify
        from .prefs import DAILY_AGENTS, DAILY_MODELS, PROVIDERS, Prefs
        pr = Prefs(settings.data_dir, settings.raw)
        st = classify.status(pr)
        orm = classify.openrouter_models(settings.data_dir / "openrouter-models.json") if (
            "openrouter" in pr.data["enabled"] or pr.key("OPENROUTER_API_KEY") or pr.data.get("daily_via") == "openrouter") else []
        u30 = db.usage_summary(30)
        daily = u30.get("daily") or {}
        sorting = [v for k, v in u30.items() if k != "daily"]
        costs = {"daily_runs": daily.get("calls") or 0, "daily_tokens": daily.get("tokens") or 0, "daily_est": daily.get("est") or 0,
                 "sort_est": sum(v.get("est") or 0 for v in sorting),
                 "sort_cost": sum(v.get("cost") or 0 for v in sorting) + (daily.get("cost") or 0),
                 "month_spend": db.spend_this_month(), "by_day": db.usage_by_day(14), "by_stage": db.usage_by_stage(30)}
        from . import skills as sk
        return page("settings.html", finish_box=_finish_box(), agent_login=_agent_login_ctx(), skills_est=sk.estimate(pr), skills_text=sk.to_lines(sk.mine(settings)),
                    skills_usage=db.conn.execute("select count(*), coalesce(sum(tokens),0), coalesce(sum(cost),0), coalesce(sum(emails),0)"
                                                 " from usage where source = 'skills' and at >= date('now', '-30 day')").fetchone(),
                    p=pr.data, prefs=pr, providers=PROVIDERS, status=st, usage=u30, costs=costs,
                    or_models=orm, daily_models=DAILY_MODELS, daily_agents=DAILY_AGENTS, key_hint=pr.key_hint("OPENROUTER_API_KEY"),
                    pending=len(db.pending_events()), msg=msg, nav="settings", tab=tab,
                    daily=pr.daily(db.spend_this_month()), or_default=__import__("autoapply.prefs", fromlist=["x"]).DAILY_OR_DEFAULT)

    @app.post("/settings")
    async def save_settings(request: Request):
        from . import classify
        from .prefs import DAILY_AGENTS, DAILY_MODELS, PROVIDERS, Prefs
        f = await request.form()
        pr = Prefs(settings.data_dir, settings.raw)
        d = pr.data
        if "sources_form" in f or any(k.startswith("on::") for k in f):  # only the Email sorting tab has these
            d["enabled"] = [p for p in d["order"] if f.get(f"on::{p}")]
        for p in PROVIDERS:
            if f"model::{p}" in f:
                d["models"][p] = str(f.get(f"model::{p}") or "").strip()[:120]
        try:
            d["min_confidence"] = max(0.0, min(1.0, float(f.get("min_confidence") or d["min_confidence"])))
        except ValueError:
            pass
        if "daily_model" in f and f.get("daily_model", "") in DAILY_MODELS:
            d["daily_model"] = f.get("daily_model", "")
        if f.get("daily_agent") in DAILY_AGENTS:
            d["daily_agent"] = f.get("daily_agent")
        if "daily_light_model" in f and f.get("daily_light_model", "") in DAILY_MODELS:
            d["daily_light_model"] = f.get("daily_light_model", "")
        for k in ("daily_codex_light_model", "daily_or_light_model", "quick_pick_model"):
            if k in f:
                v = str(f.get(k) or "").strip()
                if re.fullmatch(r"[A-Za-z0-9._:/~\[\]-]{0,120}", v):
                    d[k] = v
        if "discover_weekday" in f:
            try:
                d["discover_weekday"] = max(0, min(7, int(f.get("discover_weekday"))))
            except (TypeError, ValueError):
                pass
        if f.get("daily_via") in ("subscription", "openrouter"):
            d["daily_via"] = f.get("daily_via")
        if "daily_or_model" in f:
            om = str(f.get("daily_or_model") or "").strip()
            if re.fullmatch(r"[A-Za-z0-9._:/~\[\]-]{0,120}", om):  # also ends up on daily.sh's command line
                d["daily_or_model"] = om
        if "daily_codex_model" in f:
            cm = str(f.get("daily_codex_model") or "").strip()
            if re.fullmatch(r"[A-Za-z0-9._:/-]{0,80}", cm):  # it ends up on daily.sh's command line
                d["daily_codex_model"] = cm
        for k, lo, hi in (("picks_per_day", 0, 5), ("prep_per_day", 0, 5), ("drafts_per_day", 0, 20)):
            try:
                d[k] = max(lo, min(hi, int(f.get(k, d[k]))))
            except (TypeError, ValueError):
                pass
        if "one_call_form" in f:
            d["apply_one_call"] = bool(f.get("apply_one_call"))
        if "coach_form" in f:
            d["coach"] = bool(f.get("coach"))
            for k in ("coach_model", "drafts_model"):
                if f.get(k) in ("sonnet", "haiku", "opus"):
                    d[k] = f.get(k)
        if "skills_form" in f:
            d["skills_enabled"] = bool(f.get("skills_enabled"))
            if f.get("skills_via") in ("openrouter", "claude"):
                d["skills_via"] = f.get("skills_via")
            sm = str(f.get("skills_model") or "").strip()
            if re.fullmatch(r"[A-Za-z0-9._:/~\[\]-]{0,120}", sm):
                d["skills_model"] = sm
            try:
                d["skills_per_day"] = max(5, min(100, int(f.get("skills_per_day") or 25)))
            except ValueError:
                pass
            if "my_skills" in f:
                from . import skills as sk
                sk.save_mine(settings, sk.parse_lines(str(f.get("my_skills") or "")))
        if "limits_form" in f:
            d["prep_enabled"], d["drafts_enabled"] = bool(f.get("prep_enabled")), bool(f.get("drafts_enabled"))
        try:
            d["monthly_api_budget"] = max(0.0, min(1000.0, float(f.get("monthly_api_budget", d["monthly_api_budget"]))))
        except (TypeError, ValueError):
            pass
        url = str(f.get("ollama_url") or "").strip()
        if url.startswith(("http://", "https://")):
            d["ollama_url"] = url.rstrip("/")
        key = str(f.get("openrouter_key") or "").strip()
        if key:
            pr.set_key("OPENROUTER_API_KEY", key)
        if f.get("forget_key"):
            pr.set_key("OPENROUTER_API_KEY", "")
        action, msg = str(f.get("action") or "save"), "Saved."
        verb, _, name = action.partition(":")
        if name in d["order"] and verb in ("up", "down"):
            i = d["order"].index(name)
            j = i - 1 if verb == "up" else i + 1
            if 0 <= j < len(d["order"]):
                d["order"][i], d["order"][j] = d["order"][j], d["order"][i]
            msg = f"Moved {PROVIDERS[name]['name']} {verb}."
        pr.save()
        if verb == "test" and name in PROVIDERS:
            r = await run_in_threadpool(classify.test_source, name, pr)
            d.setdefault("tests", {})[name] = {**r, "at": datetime.now().strftime("%b %-d, %-I:%M %p"), "model": pr.model(name)}
            pr.save()
            msg = ""
        elif verb == "skills" and name in ("build", "tag"):
            from . import skills as sk
            try:
                if name == "build":
                    n = await run_in_threadpool(sk.build_from_resume, settings, pr, db)
                    msg = f"Drafted {n} skills from your resume. Check and edit the list, then Save."
                else:
                    r = await run_in_threadpool(sk.tag, db, settings, pr, None)
                    msg = r.get("skipped") or f"Listed skills for {r['tagged']} of {r['roles']} roles."
            except Exception as e:
                msg = f"Skill matching: {str(e)[:160]}"
        elif verb == "sort":
            from .cli import _review
            r = await run_in_threadpool(_review, settings, db)
            n = sum(v for k, v in r.items() if k not in ("left", "errors", "error"))
            msg = f"Sorted {n} email{'' if n == 1 else 's'}; {r.get('left', 0)} still waiting." + (
                " Failed: " + "; ".join(f"{k}: {v[:80]}" for k, v in r["errors"].items()) if r.get("errors") else "")
        from urllib.parse import quote
        tab = str(f.get("tab") or "sorting")
        tab = tab if tab in ("sorting", "daily", "costs") else "sorting"
        return RedirectResponse(f"/settings?tab={tab}&msg={quote(msg)}" + (f"#src-{name}" if name else ""), status_code=303)

    # ---- first-run setup: writes profile.yaml, config.yaml, materials/ and data/secrets.json ----
    def _back(msg: str, anchor: str = "", form=None, ok: bool = True) -> RedirectResponse:
        """Back to Setup, or, for a form sent from the welcome flow (`wizard` = its step), on to the next step when it
        worked and back to the same step when it didn't."""
        from urllib.parse import quote
        wiz = str((form or {}).get("wizard") or "")
        if wiz.isdigit() and 1 <= int(wiz) <= 5:
            n = int(wiz) + 1 if ok else int(wiz)
            return RedirectResponse(f"/welcome?step={'done' if n > 5 else n}&{'msg' if ok else 'err'}={quote(msg)}",
                                    status_code=303)
        return RedirectResponse(f"/setup?msg={quote(msg)}" + (f"#{anchor}" if anchor else ""), status_code=303)

    @app.get("/welcome")
    def welcome(step: str = "1", msg: str = "", err: str = ""):
        """A stranger's first run, one short step at a time (2026-11-22). Each step's form posts to the Setup route
        that saves it, with `wizard` = the step; the Setup page keeps everything else."""
        from . import agentlogin, setup
        st = setup.status(settings, os.environ)
        n = step if step == "done" else (step if step.isdigit() and 0 <= int(step) <= 5 else "1")
        ws = settings.raw.get("work_search") or {}
        prof = settings.profile or {}
        account = auth.load(settings.data_dir) or {}
        return page("welcome.html", nav="settings", step=n, st=st["steps"], left=st["left"], msg=msg[:300], err=err[:300],
                    states=sorted(state_rules().items()), ws=ws, prof=prof, account_email=account.get("email", ""),
                    titles=", ".join((settings.raw.get("search") or {}).get("title_keywords") or []),
                    us_only=(settings.raw.get("search") or {}).get("us_only", True),
                    agent_login=_agent_login_ctx(), plan_rows=_plan_rows() if n == "done" else [])

    # ---- Your next step (2026-11-24): welcome step 0, and Settings > Something changed? ----
    def _plan_rows(limit: int = 3) -> list[dict]:
        from . import benefits
        rows = sorted((t for t in benefits.tasks(db) if t["kind"] == "next_step"), key=lambda t: t["due"] or "9999")[:limit]
        for t in rows:
            t["when"] = datetime.strptime(t["due"], "%Y-%m-%d").strftime("%a, %b %-d") if t["due"] else "Any time"
        return rows

    def _situation_answers(f, door: str) -> dict:
        today = date.today().isoformat()
        cov = str(f.get("coverage") or "unknown")
        last = str(f.get("last_day") or "") or None
        return {"v": 1, "path": ("told" if last and last > today else "laid_off") if door == "laid" else "change",
                "last_day": last, "state": str(f.get("state") or "") or None,
                "coverage_end": (str(f.get("coverage_date") or "") or "unknown") if cov == "date" else cov,
                "age": str(f.get("age") or "unknown"), "size": str(f.get("size") or "unknown"),
                "has": [str(x) for x in f.getlist("has")] if hasattr(f, "getlist") else [],
                "vest_date": str(f.get("vest_date") or "") or None, "bonus_date": str(f.get("bonus_date") or "") or None,
                "noncompete": str(f.get("noncompete") or "unknown")}

    def _questions(mode: str, door: str, answers: dict | None = None, err: str = "", imported: bool = False):
        return page("situation_questions.html", nav="settings", mode=mode, door=door, answers=answers or {},
                    states=sorted(state_rules().items()), err=err, imported=imported)

    @app.get("/situation")
    def situation(err: str = ""):
        return page("situation.html", nav="settings", err=err[:300])

    @app.post("/situation/start")
    async def situation_start(request: Request):
        """A door was picked. A plan file or code from the website fills the questions in; nothing is saved yet."""
        from . import next_steps, setup
        f = await request.form()
        mode = "settings" if f.get("mode") == "settings" else "welcome"
        door = str(f.get("door") or "laid")
        if door == "looking":
            if next_steps._fresh(settings):
                setup.save_claim(settings, False)
            return RedirectResponse("/welcome?step=1&msg=" + quote("Got it. Straight to the search."), status_code=303)
        if door == "working":
            return page("situation_confirm.html", nav="settings", door="working", code="", changes=[
                "Turn off the unemployment side: the work-search log and claim reminders. Your log's history stays.",
                "Remove the unfinished steps from your plan. The ones you finished stay as a record."])
        door = "change" if door == "change" else "laid"
        answers, imported = None, False
        up = f.get("planfile")
        raw = (await up.read()) if getattr(up, "filename", "") else b""
        code = str(f.get("code") or "").strip()
        try:
            if raw:
                answers = next_steps.validate_answers(json.loads(raw.decode("utf-8", "replace")), state_rules())
            elif code:
                answers = next_steps.decode_code(code, state_rules())
        except (ValueError, json.JSONDecodeError) as e:
            msg = str(e) if isinstance(e, ValueError) and not isinstance(e, json.JSONDecodeError) else "That plan file didn't read right. Try saving it again from the planner."
            if mode == "settings":
                return page("situation.html", nav="settings", err=msg)
            return RedirectResponse("/welcome?step=0&err=" + quote(msg), status_code=303)
        if answers:
            imported = True
            door = "change" if answers["path"] == "change" else "laid"
        return _questions(mode, door, answers, imported=imported)

    @app.post("/situation/answers")
    async def situation_answers(request: Request):
        """Fresh install from the welcome flow: the plan goes straight onto the to-do list. Anyone else sees the
        confirm screen first."""
        from . import next_steps
        f = await request.form()
        mode = "settings" if f.get("mode") == "settings" else "welcome"
        door = "change" if f.get("door") == "change" else "laid"
        a = _situation_answers(f, door)
        try:
            a = next_steps.validate_answers(a, state_rules())
            if a["path"] in ("laid_off", "told") and not a["state"]:
                raise ValueError("Pick the state you worked in, so the claim steps and the work-search log follow its rules.")
            if mode == "welcome" and next_steps._fresh(settings):
                r = next_steps.import_plan(settings, db, a, date.today())
                env.globals["ws_label"] = (settings.raw.get("work_search") or {}).get("label", "Work search")
                return RedirectResponse("/welcome?step=1&msg=" + quote(
                    f"Your plan is on your to-do list: {r['added']} steps. Now, the search."), status_code=303)
            changes = next_steps.preview_changes(settings, db, a, date.today())
        except ValueError as e:
            return _questions(mode, door, a if isinstance(a, dict) else None, err=str(e))
        return page("situation_confirm.html", nav="settings", door=door, code=next_steps.encode_code(a), changes=changes)

    @app.post("/situation/confirm")
    async def situation_confirm(request: Request):
        from . import demo, next_steps
        f = await request.form()
        try:
            if f.get("door") == "working":
                r = next_steps.back_to_work(settings, db, set_claim=not demo.ON)  # the public demo is shared: the claim stays
                msg = "Good news. The unemployment side is off" + (f", and {r['removed']} steps are cleared." if r["removed"] else ".")
            else:
                a = next_steps.decode_code(str(f.get("code") or ""), state_rules())
                r = next_steps.import_plan(settings, db, a, date.today(), replace=True, set_claim=not demo.ON)
                msg = f"Done. {r['added']} steps are on your to-do list."
        except ValueError as e:
            return page("situation.html", nav="settings", err=str(e))
        env.globals["ws_label"] = (settings.raw.get("work_search") or {}).get("label", "Work search")
        return RedirectResponse("/?msg=" + quote(msg), status_code=303)

    @app.post("/welcome/about")
    async def welcome_about(request: Request):
        """Step 2: who you are, and whether you're claiming unemployment (the benefits side is off unless you are)."""
        from . import setup
        f = dict(await request.form())
        f["wizard"] = "2"
        if not (str(f.get("first_name") or "").strip() and str(f.get("last_name") or "").strip()
                and "@" in str(f.get("email") or "")):
            return _back("First name, last name and email are needed.", form=f, ok=False)
        claiming = str(f.get("claiming") or "") == "yes"
        state = str(f.get("state") or "").upper()
        if claiming and state not in state_rules():
            return _back("Pick your state, so the work-search log follows its rule.", form=f, ok=False)
        setup.save_profile(settings, f)
        setup.name_placeholder_resume(settings.root, settings.profile.get("last_name", ""))
        setup.save_claim(settings, claiming, state, state_rules().get(state))
        env.globals["ws_label"] = (settings.raw.get("work_search") or {}).get("label", "Work search")
        return _back("Saved." + (" Your work-search log follows your state's rule." if claiming else ""), form=f)

    @app.post("/welcome/roles")
    async def welcome_roles(request: Request):
        from . import setup
        f = dict(await request.form())
        f["wizard"] = "3"
        if not setup._lines(str(f.get("titles") or "")):
            return _back("Add at least one job title to look for.", form=f, ok=False)
        n = len(setup.save_roles(settings, str(f.get("titles") or ""), bool(f.get("us_only"))))
        return _back(f"Saved {n} title{'' if n == 1 else 's'} to look for.", form=f)

    @app.post("/welcome/ai-done")
    async def welcome_ai_done():
        """Step 4 finished: a resume that waited for the AI is put in shape now."""
        from . import resume_rebuild, setup
        if not setup.agent_ready(setup._prefs(settings))["done"]:  # never call an AI that isn't signed in: it hangs
            return _back("Not signed in yet. Sign in with one of these first, or skip for now and come back to it in "
                         "Settings.", form={"wizard": "4"}, ok=False)
        msg = await run_in_threadpool(resume_rebuild.run_waiting, settings)
        return _back(msg or "Your AI is connected.", form={"wizard": "4"})

    @app.get("/setup")
    def setup_page(msg: str = "", err: str = ""):
        from . import setup
        from .finder import FEEDS
        from .prefs import Prefs
        st = setup.status(settings, os.environ)
        prof = setup.load_profile(settings.root)
        if not st["steps"]["profile"]["done"]:
            # Blank out the example's Jane Doe values, then prefill from the resume.
            example = setup._yaml(settings.root / "profile.example.yaml")
            prof = {k: ("" if example.get(k) == v and k not in ("work_authorized", "requires_sponsorship", "willing_to_relocate",
                                                                "how_did_you_hear") else v) for k, v in prof.items()}
            for k, v in setup.resume_contact(setup.base_resume(settings.root)).items():
                prof[k] = prof.get(k) or v
        cfg = setup.load_config(settings.root) if not st["fresh"] else {}
        search = cfg.get("search") or {}
        boards = "\n".join(f"{b['ats']} {b['slug']}" for b in cfg.get("boards") or [])
        rss = "\n".join((f"{f['name']} | {f['url']}" if f.get("name") else f["url"]) if isinstance(f, dict) else f
                         for f in cfg.get("rss_feeds") or [])
        from .sources import RSS_PRESETS
        try:
            src_stats = json.loads((settings.data_dir / "sources.json").read_text())
        except (OSError, ValueError):
            src_stats = {}
        pr = Prefs(settings.data_dir, settings.raw)
        from . import resume_rebuild as _rb
        rb = {"pending": _rb.pending(settings), "waiting": bool(_rb.waiting(settings)), "error": _rb._read(settings).get("error", ""),
              "ai": st["steps"]["agent"]["done"], "files": _rb.files(settings)}
        return page("setup.html", rb=rb, finish_cmd=("docker compose --profile finish up -d" if Path("/.dockerenv").exists() else "docker compose -f docker-compose.finish.yml up -d"), finish_on=bool((settings.raw.get("finish") or {}).get("on_server")), nav="settings", tab="setup", st=st, standing=setup.STANDING, decline=setup.DECLINE,
                    stand=setup.standing_values(prof), prof=prof, fields=setup.PROFILE_FIELDS, yes_no=setup.YES_NO,
                    titles=", ".join(search.get("title_keywords") or []), exclude=", ".join(search.get("exclude_keywords") or []),
                    us_only=search.get("us_only", True), levels=search.get("levels") or [],
                    level_opts=__import__("autoapply.rank", fromlist=["x"]).LEVELS, boards=boards, rss=rss, rss_presets=RSS_PRESETS, src_stats=src_stats,
                    keys={k: pr.key_hint(k) for k in ("ADZUNA_APP_ID", "ADZUNA_APP_KEY", "USAJOBS_API_KEY", "USAJOBS_EMAIL")}, feeds=setup.FEED_LABELS,
                    feeds_on=cfg.get("feeds", list(FEEDS)) if cfg else [*FEEDS, "themuse", "himalayas", "jobicy"], ws=cfg.get("work_search") or {}, states=state_rules(),
                    about=setup.load_about(settings.data_dir), about_fields=setup.ABOUT_FIELDS,
                    resume=setup.base_resume(settings.root), looks=__import__("autoapply.docs", fromlist=["x"]).LOOKS,
                    look=(lambda v: v if v in __import__("autoapply.docs", fromlist=["x"]).LOOKS else "navy")(str(prof.get("resume_look") or "").lower()), accent=str(prof.get("resume_accent") or "").lstrip("#"),
                    gmail_user=os.environ.get("GMAIL_USER", ""),
                    gmail_env=pr.from_env("GMAIL_APP_PASSWORD"), mail=_mail_ctx(), agent_login=_agent_login_ctx(),
                    remake_n=len(__import__("autoapply.remake", fromlist=["x"]).waiting(db)), remake_st=app.state.remaker.status(),
                    msg=msg, err=err)

    @app.post("/setup/finish")
    def setup_finish():
        """Finish it here: the person started the browser box; check it answers, then turn it on."""
        from . import finish_here, setup
        docker = Path("/.dockerenv").exists()
        box = finish_here.Box("finish" if docker else "127.0.0.1",
                              int(os.environ.get("FINISH_CDP_PORT") or 9222) if not docker else 9222,
                              int(os.environ.get("FINISH_VNC_PORT") or 5900) if not docker else 5900)
        try:
            h = finish_here.health(box, timeout=3)
        except OSError as e:  # the name `finish` doesn't resolve: the profile isn't on
            h = {"ok": False, "error": str(e)}
        if not h["ok"]:
            return RedirectResponse("/setup?err=" + quote("The browser box isn't answering yet. Start it with the "
                                    "command above, wait a few seconds, then check again.") + "#finish", status_code=303)
        current = app.state.finish_sessions
        live = current is not None and current.status()["state"] in finish_here.LIVE
        if live and getattr(current, "box", box) != box:
            return RedirectResponse("/setup?err=" + quote("An application is open in the browser. Press Done there "
                                    "first, then check again.") + "#finish", status_code=303)
        setup.set_finish(settings, box.host, box.cdp_port, box.vnc_port)
        if current is None or (not live and getattr(current, "box", None) != box):
            app.state.finish_sessions = finish_here.Sessions(box)  # an open session is kept, never orphaned
        return RedirectResponse("/setup?msg=" + quote("Finish it here is on. It shows on any application that needs "
                                "you to finish it.") + "#finish", status_code=303)

    @app.post("/setup/profile")
    async def setup_profile(request: Request):
        from . import setup
        f = dict(await request.form())
        if not (str(f.get("first_name") or "").strip() and str(f.get("last_name") or "").strip()
                and "@" in str(f.get("email") or "")):
            return _back("First name, last name and email are needed.", "profile", form=f, ok=False)
        setup.save_profile(settings, f)
        setup.name_placeholder_resume(settings.root, settings.profile.get("last_name", ""))
        return _back("Saved your contact details to profile.yaml.", "interests", form=f, ok=True)

    @app.post("/setup/interests")
    async def setup_interests(request: Request):
        from . import setup
        f = dict(await request.form())
        if not setup._lines(str(f.get("titles") or "")):
            return _back("Add at least one job title to look for.", "interests", form=f, ok=False)
        from .prefs import Prefs
        pr = Prefs(settings.data_dir, settings.raw)
        for k in ("ADZUNA_APP_ID", "ADZUNA_APP_KEY", "USAJOBS_API_KEY", "USAJOBS_EMAIL"):
            v = str(f.get(k) or "").strip()
            if v and not pr.from_env(k):
                pr.set_key(k, v[:200])
        r = setup.save_interests(settings, f)
        env.globals["ws_label"] = (settings.raw.get("work_search") or {}).get("label", "Work search")
        msg = "Saved your job interests to config.yaml."
        if r["bad"]:
            msg += " Couldn't read: " + "; ".join(r["bad"][:5]) + ". Use a careers-page URL, 'greenhouse slug', or a feed URL."
        if r["missing_keys"]:
            msg += " Skipped until you fill in the free API keys box: " + ", ".join(r["missing_keys"]) + "."
        return _back(msg, "about", form=f, ok=True)

    @app.post("/setup/resume")
    async def setup_resume(docx: UploadFile = File(None), pdf: UploadFile = File(None), wizard: str = Form("")):
        f = {"wizard": wizard}
        if docx and docx.filename and docx.filename.lower().endswith(".pdf") and not (pdf and pdf.filename):
            docx, pdf = None, docx  # the welcome flow has one file box for either
        from . import setup
        limit = 10 * 1024 * 1024
        d = await docx.read(limit + 1) if docx and docx.filename else None
        p = await pdf.read(limit + 1) if pdf and pdf.filename else None
        if not d and not p:
            return _back("Pick your resume first: a Word file or a PDF.", "resume", form=f, ok=False)
        if (d and len(d) > limit) or (p and len(p) > limit):
            return _back("That file is over 10 MB.", "resume", form=f, ok=False)
        last = settings.profile.get("last_name") or ""
        r = await run_in_threadpool(setup.save_resume, settings.root, last, d, p)
        if r["error"]:
            return _back(r["error"], "resume", form=f, ok=False)
        if not last:  # name it after the resume's own header when contact details aren't saved yet
            guess = setup.resume_contact(setup.base_resume(settings.root)).get("last_name", "")
            if renamed := setup.name_placeholder_resume(settings.root, guess):
                r = {"docx": renamed, "pdf": renamed.replace(".docx", ".pdf") if r["pdf"] else None}
        made = " and ".join(x for x in (r["docx"], r["pdf"]) if x)
        # Any resume works (resume_rebuild.py): one tailoring can't read is put in shape by the person's own AI, now if
        # it's signed in, else as soon as it is.
        from . import resume_rebuild
        target = setup.base_resume(settings.root) if d else setup.materials(settings.root) / (r["pdf"] or "")
        if target and target.exists() and resume_rebuild.needs(target):
            if setup.agent_ready(setup._prefs(settings))["done"]:
                try:
                    await run_in_threadpool(resume_rebuild.rebuild, settings, target)
                    return _back("Saved, and we rebuilt your resume in the format tailoring uses. Check it on the Setup page when you have a minute.", "resume", form=f)
                except resume_rebuild.RebuildError as e:
                    return _back(f"Saved {made}. {e}", "resume", form=f, ok=False)
            resume_rebuild.mark_waiting(settings, target)
            return _back(f"Saved {made}. Once your AI is connected (the Agent step), we'll put it in the format "
                         "tailoring uses, and you can check it.", "profile", form=f)
        return _back(f"Saved {made} in materials/." + ("" if r["pdf"] or p else
                     " No PDF was made (LibreOffice isn't installed here), so upload one too."), "profile", form=f)

    @app.post("/setup/resume/rebuild")
    async def setup_resume_rebuild():
        from . import resume_rebuild
        msg = await run_in_threadpool(resume_rebuild.run_waiting, settings)
        return _back(msg or "There's no resume waiting to be rebuilt.", "resume")

    @app.post("/setup/resume/confirm")
    def setup_resume_confirm():
        from . import resume_rebuild
        resume_rebuild.confirm(settings)
        return _back("Thanks. Tailored resumes start from this one.", "resume")

    @app.get("/setup/resume/file/{which}")
    def setup_resume_file(which: str):
        from . import resume_rebuild
        if which not in ("base", "original"):
            raise HTTPException(404)
        p = resume_rebuild.files(settings).get(which)
        if not p:
            raise HTTPException(404)
        return FileResponse(p, filename=p.name)

    @app.post("/setup/look")
    async def setup_look(request: Request):
        from . import setup
        f = dict(await request.form())
        err = setup.save_look(settings, str(f.get("look") or ""), str(f.get("accent") or ""))
        if err:
            return _back(err, "look")
        from .docs import LOOKS
        return _back(f"Saved. New tailored resumes and letters use the {LOOKS[settings.profile['resume_look']]['label']} look.", "look")

    @app.post("/setup/look/remake")
    def setup_look_remake():
        """Lays out the waiting applications' resumes and letters again in the saved look (in the background)."""
        from .docs import LOOKS
        if not app.state.remaker.start(settings, db):
            return _back("Already remaking them; this page shows when it's done.", "look")
        label = LOOKS.get(settings.profile.get("resume_look") or "navy", LOOKS["navy"])["label"]
        return _back(f"Remaking them in the {label} look. It takes a few seconds each; reload to see how far it got.", "look")

    @app.post("/setup/look/preview")
    async def setup_look_preview(request: Request):
        """Your base resume in the chosen look, as a PDF, without saving the choice."""
        from . import docs, setup
        f = dict(await request.form())
        base = setup.base_resume(settings.root)
        if base is None:
            return _back("Upload your resume first.", "resume")
        out = settings.data_dir / "look-preview"
        shutil.rmtree(out, ignore_errors=True)
        try:
            r = await run_in_threadpool(docs.build, base, {"company": "Preview"}, out,
                                        settings.profile.get("last_name", ""), True, None,
                                        str(f.get("look") or ""), str(f.get("accent") or "").strip())
        except docs.SpecError as e:
            return _back(str(e), "look")
        pdf = (r.get("pdf") or {}).get("resume")
        if pdf:
            return FileResponse(pdf, media_type="application/pdf", content_disposition_type="inline",
                                filename=Path(pdf).name)
        return FileResponse(r["resume"], filename=Path(r["resume"]).name)  # no LibreOffice here: the Word file

    @app.post("/setup/about")
    async def setup_about(request: Request):
        from . import setup
        setup.save_about(settings.root, settings.data_dir, dict(await request.form()))
        return _back("Saved to materials/context/setup.md. The agent reads it before every application.", "gmail")

    def _agent_login_ctx() -> dict:
        from . import agentlogin
        from .prefs import Prefs, find_cli
        pr = Prefs(settings.data_dir, settings.raw)
        return {"claude": bool(find_cli("claude")), "codex": bool(find_cli("codex")),
                "token": pr.key_hint(agentlogin.TOKEN_KEY), "token_saved": bool(pr.key(agentlogin.TOKEN_KEY)),
                "token_env": pr.from_env(agentlogin.TOKEN_KEY), "codex_in": agentlogin.codex_signed_in()}

    def _mail_ctx() -> dict:
        """The Email step: providers, the main inbox's provider, forwarded addresses with their steps, extra inboxes."""
        from . import mailboxes as mb
        fwd = [{"address": a, "provider": mb.provider_for(a), "steps": mb.FORWARDING[mb.provider_for(a)]} for a in mb.forwarded()]
        return {"providers": mb.PROVIDERS, "provider": mb.main_provider(), "forwarded": fwd,
                "extras": [{"provider": x["provider"], "label": mb.PROVIDERS[x["provider"]]["label"], "user": x["user"]} for x in mb.extras()]}

    @app.post("/setup/email/forwarded")
    async def setup_forwarded(request: Request):
        """Other addresses of yours that forward into the main inbox (recommended over more logins)."""
        from .prefs import Prefs
        f = await request.form()
        raw = re.split(r"[\s,;]+", str(f.get("addresses") or ""))
        main = (os.environ.get("GMAIL_USER") or "").lower()
        found = []
        for a in raw:
            a = a.strip().lower()
            if a and a != main and a not in found:
                if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", a):
                    raise HTTPException(400, f"{a} doesn't look like an email address.")
                found.append(a)
        Prefs(settings.data_dir, settings.raw).set_key("OWN_ADDRESSES", ",".join(found[:10]))
        msg = (f"Saved {len(found)} address{'es' if len(found) != 1 else ''}. Turn on forwarding in each one (steps below), "
               "and their job mail shows up here with the rest.") if found else "No other addresses saved."
        return _back(msg, "other-emails")

    @app.post("/setup/email/inbox")
    async def setup_extra_inbox(request: Request):
        """Connect another inbox (when forwarding isn't possible): tested before it's saved."""
        from . import mailboxes as mb
        from . import setup
        from .prefs import Prefs
        f = await request.form()
        pr = Prefs(settings.data_dir, settings.raw)
        rows = mb.extras()
        if f.get("action") == "remove":
            user = str(f.get("user") or "").lower()
            mb.save_extras(pr, [r for r in rows if r["user"].lower() != user])
            return _back(f"Stopped reading {user}.", "other-emails")
        provider = str(f.get("provider") or "")
        user = str(f.get("user") or "").strip()
        pw = re.sub(r"\s", "", str(f.get("password") or ""))
        if mb.provider_for(user) == "outlook":
            raise HTTPException(400, "Outlook and Hotmail no longer allow app passwords for other apps. Forward that "
                                     "address to your main inbox instead (steps under Other addresses).")
        if "@" not in user or not pw:
            raise HTTPException(400, "Enter the address and an app password.")
        if user.lower() in mb.own_addresses() - set(mb.forwarded()):
            raise HTTPException(400, f"{user} is already connected.")
        if len(rows) >= 3:
            raise HTTPException(400, "Up to three extra inboxes. Forwarding works for the rest.")
        problem = await run_in_threadpool(setup.test_gmail, user, pw, provider)
        if problem:
            raise HTTPException(400, f"Not saved: {problem}")
        mb.save_extras(pr, [*rows, {"provider": provider, "user": user, "password": pw}])
        fwd = [a for a in mb.forwarded() if a != user.lower()]
        pr.set_key("OWN_ADDRESSES", ",".join(fwd))  # connected now, so no longer a forwarded one
        return _back(f"Connected {user}. The next email sync reads it too (read-only).", "other-emails")

    @app.post("/setup/claude-token")
    async def setup_claude_token(request: Request):
        from . import agentlogin, classify
        from .prefs import Prefs
        f = await request.form()
        pr = Prefs(settings.data_dir, settings.raw)
        back = str(f.get("back") or "setup")
        go = (lambda m: RedirectResponse(f"/settings?tab=daily&msg={quote(m)}#agent", status_code=303)) if back == "settings" \
            else (lambda m: _back(m, "agent", form=f, ok=not m.startswith(("Not saved", "Your Claude token is set"))))
        if pr.from_env(agentlogin.TOKEN_KEY):
            return go("Your Claude token is set in .env; change it there.")
        if f.get("action") == "forget":
            pr.set_key(agentlogin.TOKEN_KEY, "")
            return go("Removed the saved Claude token.")
        tok, problem = agentlogin.clean_token(str(f.get("token") or ""))
        if problem:
            return go(f"Not saved: {problem}")
        old = pr.key(agentlogin.TOKEN_KEY) or ""
        pr.set_key(agentlogin.TOKEN_KEY, tok)
        res = await run_in_threadpool(classify.test_source, "claude", pr)
        if not res.get("ok") and res.get("kind") is None:
            pr.set_key(agentlogin.TOKEN_KEY, old)
            return go(f"Not saved: Claude Code couldn't sign in with that token ({res.get('error') or 'no answer'}).")
        return go("Claude is signed in. Every run uses your subscription.")

    @app.post("/setup/codex-login")
    async def setup_codex_login(request: Request):
        from . import agentlogin
        f = await request.form()
        if f.get("action") == "cancel":
            agentlogin.cancel_codex()
            return JSONResponse(agentlogin.codex_state())
        return JSONResponse(await run_in_threadpool(agentlogin.start_codex))

    @app.get("/setup/codex-login")
    def setup_codex_state():
        from . import agentlogin
        return JSONResponse(agentlogin.codex_state())

    @app.post("/setup/gmail")
    async def setup_gmail(request: Request):
        from . import setup
        from .prefs import Prefs
        f = await request.form()
        pr = Prefs(settings.data_dir, settings.raw)
        user = str(f.get("gmail_user") or "").strip()
        pw = re.sub(r"\s", "", str(f.get("gmail_password") or ""))
        from . import mailboxes as mb
        provider = str(f.get("provider") or "gmail")
        provider = provider if provider in mb.PROVIDERS else "gmail"
        if f.get("action") == "forget":
            for k in ("GMAIL_USER", "GMAIL_APP_PASSWORD", "BRIEF_TO", "EMAIL_PROVIDER"):
                pr.set_key(k, "")
            return _back("Removed the saved email login.", "gmail", form=f, ok=True)
        same = user.lower() == (os.environ.get("GMAIL_USER") or "").lower() and provider == mb.main_provider()
        pw = pw or ((os.environ.get("GMAIL_APP_PASSWORD") or "") if same else "")
        label = mb.PROVIDERS[provider]["label"]
        if "@" not in user or not pw:
            return _back(f"Enter your {label} address and an app password.", "gmail", form=f, ok=False)
        if mb.provider_for(user) == "outlook":
            return _back("Not saved: Outlook and Hotmail no longer allow app passwords for other apps. Use a Gmail, iCloud "
                         "or Yahoo address as your main inbox, and forward Outlook to it.", "gmail", form=f, ok=False)
        problem = await run_in_threadpool(setup.test_gmail, user, pw, provider)
        if problem:
            return _back(f"Not saved: {problem}", "gmail", form=f, ok=False)
        if not pr.from_env("EMAIL_PROVIDER"):
            pr.set_key("EMAIL_PROVIDER", "" if provider == "gmail" else provider)
        if not pr.from_env("GMAIL_USER"):
            pr.set_key("GMAIL_USER", user)
        if not pr.from_env("GMAIL_APP_PASSWORD"):
            pr.set_key("GMAIL_APP_PASSWORD", pw)
        brief_to = str(f.get("brief_to") or "").strip()
        if "@" in brief_to and not pr.from_env("BRIEF_TO"):
            pr.set_key("BRIEF_TO", brief_to)
        return _back(f"{label} works. Job alerts and replies will be read (read-only), and the brief sent from it.", "other-emails", form=f, ok=True)

    @app.get("/dua")
    def dua(company: str = "", tab: str = ""):
        from . import benefits
        from . import coach
        rule = my_state(settings)
        return page("dua.html", people=coach.contacts(db)[:40], how=coach.HOW, contacts_count=coach.contacts_count(db),
                    prefill_company=company[:80], weeks=stats.dua_weeks(db), floor=work_search(settings)[1], rule=rule, nav="work", today=date.today(),
                    claim=benefits.claim(settings), runway=benefits.runway(settings, rule), days=benefits.DAYS,
                    tasks=benefits.tasks(db, include_done=True), resources=benefits.RESOURCES, general=general_notes(),
                    letters=benefits.letters(db)[:20],
                    tab=tab if tab in ("log", "claim", "people", "rule") else ("people" if company else "log"),
                    ws_rule_label=(f"{rule['name']}'s rule" if rule else "Your state's rule"))

    @app.post("/dua/claim")
    async def dua_claim(request: Request):
        from . import benefits
        benefits.save_claim(settings, dict(await request.form()))
        return RedirectResponse("/dua?tab=claim#claim", status_code=303)

    @app.post("/dua/task")
    def dua_task(action: str = Form(...), title: str = Form(""), due: str = Form(""), task_id: int = Form(0)):
        from . import benefits
        if action == "add" and title.strip():
            benefits.add_task(db, title, due)
        elif action == "resea":
            benefits.add_resea(db, (my_state(settings) or {}).get("code", ""))
        elif action == "due" and task_id:
            d = benefits._date(due)
            db.conn.execute("update ui_tasks set due = ? where id = ?", (d.isoformat() if d else None, task_id))
            db.conn.commit()
        elif action in ("done", "undo", "delete") and task_id:
            benefits.set_task(db, task_id, done=action == "done" if action != "delete" else None, delete=action == "delete")
        return RedirectResponse("/dua?tab=claim#tasks", status_code=303)

    @app.get("/dua/{week}.zip")
    def dua_zip(week: str):
        wk = next((w for w in stats.dua_weeks(db) if w["start"].isoformat() == week), None)
        if not wk:
            raise HTTPException(404)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            out = io.StringIO()
            w = csv.writer(out)
            w.writerow(["Date", "Activity Type", "Company", "Position", "Pay rate", "How", "Contact", "Result", "Proof files"])
            for r in wk["rows"]:
                w.writerow([r["date"].isoformat(), r["type"], r["company"], r["position"], r.get("pay", ""), r["how"], r["contact"],
                            r["result"], "; ".join(Path(p).name for p in r["proof"])])
            z.writestr(f"work-search-{week}.csv", out.getvalue())
            d = proof_root / week
            for f in sorted(d.iterdir()) if d.is_dir() else []:
                z.write(f, f"proof/{f.name}")
        return Response(buf.getvalue(), media_type="application/zip",
                        headers={"content-disposition": f'attachment; filename="work-search-week-{week}.zip"'})

    @app.get("/proof/{week}/{name}")
    def proof_file(week: str, name: str):
        p = (proof_root / week / name).resolve()
        if proof_root.resolve() not in p.parents or not p.is_file():
            raise HTTPException(404)
        return FileResponse(p)

    @app.post("/job/{job_id:path}")
    async def act(job_id: str, request: Request, action: str = Form(...), plan: str = Form("{}"), approve: str = Form("")):
        job, old_plan, old_report = load(job_id)
        form = await request.form()
        from . import coach
        coach.touch(db)
        try:
            # No answers box on the page (a prepped job's kit): keep the saved plan, or its files and answers are lost.
            new_plan = json.loads(plan) if "plan" in form and plan.strip() else dict(old_plan)
        except json.JSONDecodeError as e:
            raise HTTPException(400, f"answers JSON is invalid: {e}")
        new_plan.setdefault("answers", {})
        for k, v in form.items():
            if k.startswith("ans::") and v:
                new_plan["answers"][_field_label(k[5:])] = v
        # Held answers come only from this form, never from the JSON box.
        new_plan["approved"] = {_field_label(k[6:]): v for k, v in form.items() if k.startswith("held::") and v}
        new_plan["held_reviewed"] = True  # from now on a blank consent stays blank: no standing answer fills it
        new_plan["approved_by_user"] = False

        from . import demo
        if demo.ON and action not in demo.ALLOWED_JOB_ACTIONS:  # refill and submit open a real browser
            if request.headers.get("x-in-page"):
                return JSONResponse(demo.off_json(), status_code=403)
            return HTMLResponse(demo.off_page("/job/" + quote(job_id, safe=":")), status_code=403)
        before = {"status": job["status"], "lead": _lead_status(job_id)}  # what a command mark's Undo goes back to
        if action == "unsend":
            from . import finish
            if job["status"] == "submitted":
                # Any "sent" can be taken back, however it was marked: Submit here, the command, or by hand.
                m = finish.undo(db, job_id)
                prev = (m["detail"].get("status") if m else None) or (
                    "filled" if old_report else
                    "prepped" if old_plan.get("files") else "new")
                prev = prev if prev != "submitted" else "filled"
                db.update(job_id, status=prev, submitted_at=None,
                          **({"report_json": {**old_report, "submitted": False}} if old_report.get("submitted") else {}))
                from . import leads
                lead = m["detail"].get("lead") if m else None
                _set_lead(job_id, lead if lead is not None else
                          (leads.TRACKED.get(prev, "tracked") if _lead_status(job_id) is not None else None))
                db.delete_event(f"applied:{job_id}")
            return RedirectResponse(f"/job/{job_id}", status_code=303)
        if action in ("skip", "manual", "command"):  # the lead behind this job follows it
            from . import leads
            leads.ensure(db)
            db.conn.execute("update leads set status = ? where job_id = ?", ("dismissed" if action == "skip" else "applied", job_id))
            db.conn.commit()
        if action == "skip":
            db.update(job_id, status="skipped", plan_json=new_plan)
        elif action in ("manual", "command"):  # you sent it yourself: by hand, or with autoapply-finish
            from . import finish
            db.update(job_id, status="submitted", plan_json=new_plan, error=None,
                      submitted_at=time.strftime("%Y-%m-%d %H:%M"))
            finish.mark(db, job_id, "command" if action == "command" else "hand", before)
            _record_applied(job, None)
        elif action == "refill":
            r = await run_in_threadpool(fill, new_plan, settings.profile, shots, headless)  # sync Playwright can't share the event loop
            db.update(job_id, status="filled", plan_json=new_plan, report_json=r.as_dict(), screenshot=r.screenshot,
                      error=r.closed_error)
        elif action == "submit":
            if old_report.get("submit_clicked") and not old_report.get("submitted") and form.get("resubmit") != "yes":
                db.update(job_id, plan_json=new_plan,
                          error="Submit was already pressed once without a confirmation. Check your email, then tick 'submit again' if it really didn't go through.")
                return RedirectResponse(f"/job/{job_id}", status_code=303)
            if approve != "yes":
                db.update(job_id, plan_json=new_plan, error="Tick the review box to submit.")
                return RedirectResponse(f"/job/{job_id}", status_code=303)
            from . import preflight
            db.update(job_id, plan_json=new_plan)  # check what you just chose, not the old answers
            stop = preflight.blocking(preflight.for_job(db, settings, job_id))
            if stop:
                db.update(job_id, error="Not submitted. Fix these first: " + "; ".join(f"{c['group']}: {c['name']}" for c in stop))
                return RedirectResponse(f"/job/{job_id}", status_code=303)
            new_plan["approved_by_user"] = True
            r = await run_in_threadpool(submit, new_plan, settings.profile, shots, headless)
            done = r.submitted
            if r.submit_clicked and not done:
                r.errors.append({"label": "(submit)", "error": "Submit was pressed but not confirmed. Don't retry until you've checked your email."})
            new_plan["approved_by_user"] = False  # one approval, one click
            db.update(job_id, status="submitted" if done else "filled", plan_json=new_plan, report_json=r.as_dict(),
                      screenshot=r.screenshot, error=None if done else "; ".join(e["error"] for e in r.errors),
                      submitted_at=time.strftime("%Y-%m-%d %H:%M") if done else None)
            if done:
                _record_applied(job, r.screenshot)
        return RedirectResponse(f"/job/{job_id}", status_code=303)

    return app
