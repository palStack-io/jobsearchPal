"""The coach: one call a morning that reads the person's whole search and says what to do next to get hired.

Python gathers the facts (the digest), one model call with no tools coaches and judges, and Python checks every id,
person, number and name in the reply against the digest before anything is saved. The person sends every message
themselves. Design: docs/superpowers/specs/2026-11-21-coach-design.md.
"""
from __future__ import annotations

import hashlib

SCHEMA = """
create table if not exists coach_day (   -- one validated coach reply a day
  day text primary key, json text, usage_id integer, fallback integer default 0,
  at text default (datetime('now', 'localtime'))
);
create table if not exists outreach (    -- people the coach suggested reaching; the person sends each note
  key text primary key, day text, person text, email text, company text, job_id text, kind text, why text,
  draft_path text, state text default 'suggested', changed text
);
"""

STAGES = ("coach", "drafts")


def ensure(db) -> None:
    db.conn.executescript(SCHEMA)


def enabled(prefs) -> bool:
    return bool(prefs.data.get("coach", False))


def model(prefs, stage: str) -> str:
    """The coach and drafts default to sonnet: the judgement and the voice are the point (Daily run settings)."""
    return prefs.data.get(f"{stage}_model") or "sonnet"


def outreach_key(person: str, kind: str, job_id: str | None) -> str:
    parts = [(person or "").strip().lower(), (kind or "").strip().lower(), job_id or ""]
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:12]


# ---- the digest: everything the coach may use, computed here ------------------------------------

import email.utils  # noqa: E402
import json  # noqa: E402
import re  # noqa: E402
from datetime import date, datetime, timedelta  # noqa: E402

DIGEST_CHARS = 60000
CANDIDATES = 15
RECENT_OUTREACH_DAYS = 30
NAMED = re.compile(r"(?:hiring manager|reports? to|work(?:ing)? with|recruiter)[:\s,\-]+(?:is\s+)?([A-Z][a-z]+ [A-Z][a-z]+)")


def fmt_day(d: date) -> str:
    """The one date form the coach sees and may repeat: "Tue 24 Nov"."""
    return f"{d.strftime('%a')} {d.day} {d.strftime('%b')}"


def _day(s: str | None) -> str:
    return (s or "")[:10]


def people(db, cands: list[dict], now: datetime) -> list[dict]:
    """Everyone the person has a real link to, each with a ref the coach must use: contacts, people who wrote to them
    about a job, and names a candidate posting gives next to a cue ("the hiring manager, Dana Wu")."""
    from . import coach
    coach.ensure(db)
    out: list[dict] = []
    by_email: dict[str, dict] = {}
    by_name: dict[tuple, dict] = {}

    def add(name: str, company: str, addr: str, link: str, last: str):
        addr = (addr or "").lower()
        key = (name.lower(), coach._norm(company))
        p = by_email.get(addr) if addr else None
        p = p or by_name.get(key)
        if p:
            p["last"] = max(p["last"], last)
            p["email"] = p["email"] or addr
            p["company"] = p["company"] or company
            if addr:
                by_email[addr] = p
            return
        p = {"name": name, "company": company or "", "email": addr, "link": link, "last": last}
        out.append(p)
        by_name[key] = p
        if addr:
            by_email[addr] = p

    for c in coach.contacts(db):
        add(c["name"], c.get("company") or "", c.get("email") or "", "contact", _day(c["at"]))
    jobs = {j["id"]: j for j in db.all()}
    for e in reversed(db.events()):  # oldest first, so "last" ends on the latest
        if e["kind"] in ("rejected",):
            continue
        name, addr = email.utils.parseaddr(e["sender"] or "")
        if not name or not addr or any(n in addr.lower() for n in ("no-reply", "noreply", "donotreply", "notifications@")):
            continue
        company = (jobs.get(e["job_id"]) or {}).get("company") or ""
        add(name.strip().strip('"'), company, addr, "wrote to you", _day(e["at"]))
    for srow in db.sent():  # your mail to someone already known moves their last touch
        for addr in (srow["to_addrs"] or "").lower().split():
            if addr in by_email:
                by_email[addr]["last"] = max(by_email[addr]["last"], _day(srow["at"]))
    for c in cands:
        for m in NAMED.finditer(c.get("description") or ""):
            add(m.group(1), c.get("company") or "", "", "named in posting", "")
    for i, p in enumerate(out, 1):
        p["ref"] = f"p{i}"
    return out


def _contacted_first(db, job: dict) -> bool:
    """A contact (or a sent outreach) at the same company dated on or before the application."""
    from . import coach
    co = coach._norm(job["company"])
    when = _day(job.get("submitted_at")) or "9999"
    for c in coach.contacts(db):
        if coach._norm(c.get("company") or "") == co and _day(c["at"]) <= when:
            return True
    ensure(db)
    for r in db.conn.execute("select company, changed from outreach where state = 'sent'"):
        if coach._norm(r[0] or "") == co and _day(r[1]) <= when:
            return True
    return False


def patterns(db, now: datetime) -> list[str]:
    """Factual lines with counts, for the coach to narrate (it never counts itself). A line needs at least 2 behind it."""
    from . import coach
    sent = db.by_status("submitted")
    ivs = [j for j in sent if j.get("outcome") in ("interview", "offer")]
    lines = []
    if len(ivs) >= 2:
        first = sum(1 for j in ivs if _contacted_first(db, j))
        if first:
            lines.append(f"{first} of {len(ivs)} interviews came from roles where you contacted someone first")
    fams: dict[str, list[dict]] = {}
    for j in sent:
        fams.setdefault(coach._clean_title(j["title"]) or "other", []).append(j)
    for fam, js in sorted(fams.items(), key=lambda kv: -len(kv[1])):
        if len(js) < 2 or fam == "other":
            continue
        n_iv = sum(1 for j in js if j.get("outcome") in ("interview", "offer"))
        n_reply = sum(1 for j in js if j.get("outcome") in ("interview", "offer", "rejected"))
        lines.append(f"{fam.title()} roles: {n_iv} interviews and {n_reply} replies from {len(js)} sent")
    return lines[:8]


def _fence(name: str, obj) -> str:
    return f"```{name}\n{json.dumps(obj, ensure_ascii=False, indent=0)}\n```"


def recent_outreach(db, now: datetime) -> list[dict]:
    ensure(db)
    since = (now - timedelta(days=RECENT_OUTREACH_DAYS)).date().isoformat()
    return [dict(r) for r in db.conn.execute("select key, day, person, company, job_id, kind, state, changed from outreach "
                                             "where day >= ? order by day desc", (since,))]


def digest(db, settings, now: datetime | None = None) -> tuple[str, dict]:
    """(text, refs): what the coach call reads, and the lists its reply is checked against."""
    from . import coach, goals, nudges, oneshot, prep, rank, stats
    from .prefs import Prefs
    from .web import work_search
    now = now or datetime.now()
    today = now.date()
    s = stats.compute(db, today, floor=work_search(settings)[1])
    cands = rank.candidates(db, settings, CANDIDATES, desc_chars=700)
    ppl = people(db, cands, now)
    todo = [n for n in nudges.compute(db, settings.data_dir, now=now, settings=settings) if n.get("key")]
    recent = recent_outreach(db, now)
    about, _ = oneshot.about_you(settings.root)
    ivs = [r for r in prep.interviews(db, settings.data_dir) if _day(r["latest"]) >= (today - timedelta(days=30)).isoformat()]
    plan = coach.plan_line(coach.plan(db, s["this_week_start"]))
    tv = goals.today_view(db, today)
    events = [{"day": fmt_day(datetime.strptime(_day(e["at"]), "%Y-%m-%d").date()), "kind": e["kind"],
               "company": next((j["company"] for j in [db.get(e["job_id"])] if j), "") if e["job_id"] else "",
               "from": email.utils.parseaddr(e["sender"] or "")[0]}
              for e in db.events() if _day(e["at"]) >= (today - timedelta(days=30)).isoformat() and _day(e["at"])]
    record = {"sent": s["applied"], "interviews": s["interviews"], "closed": s["rejected"], "still_in_play": s["waiting"],
              "this_week": s["this_week"], "weekly_minimum": s["dua_floor"]}
    threads = [{"key": n["key"], "kind": n.get("kind"), "title": n.get("title"), "why": n.get("why"),
                "company": (n.get("job") or {}).get("company")} for n in todo]
    person_rows = [{k: p[k] for k in ("ref", "name", "company", "email", "link")} |
                   {"last": fmt_day(date.fromisoformat(p["last"])) if p["last"] else ""} for p in ppl]
    screens = [{"job": r["job"]["id"], "company": r["job"]["company"], "title": r["job"]["title"],
                "last_email": fmt_day(datetime.strptime(_day(r["latest"]), "%Y-%m-%d").date())} for r in ivs[:5]]
    head = [f"Today is {fmt_day(today)}.", "# About the person", about,
            "# Record", _fence("record", record), "# Events, last 30 days", _fence("events", events[:40]),
            "# Patterns (computed; use as given)", "\n".join(f"- {line}" for line in patterns(db, now)) or "- none yet",
            "# Open threads", _fence("threads", threads),
            "# People (use only these refs)", _fence("people", person_rows),
            "# Recent outreach (don't repeat these)", _fence("recent_outreach", [{k: r[k] for k in ("key", "person", "company", "kind", "state")} for r in recent]),
            "# This week", (f"Their plan: {plan}" if plan else "No plan written this week.") +
            (f"\nToday's goals: {json.dumps(tv, default=str)[:600]}" if tv else ""),
            "# Upcoming screens", _fence("screens", screens)]
    head_text = "\n\n".join(head)
    room = DIGEST_CHARS - len(head_text) - 200
    per = max(200, min(700, room // max(1, len(cands)) - 250)) if cands else 700
    cand_rows = [{"id": c["id"], "company": c["company"], "title": c["title"], "location": c["location"],
                  "found": fmt_day(datetime.strptime(_day(c["found_at"]), "%Y-%m-%d").date()) if _day(c["found_at"]) else "",
                  "description": (c["description"] or "")[:per]} for c in cands]
    text = head_text + "\n\n# Candidates (use only these ids)\n" + _fence("candidates", cand_rows)
    if len(text) > DIGEST_CHARS:
        text = text[:DIGEST_CHARS]
    refs = {"candidates": {c["id"]: c for c in cands}, "people": {p["ref"]: p for p in ppl},
            "nudges": {n["key"]: n for n in todo}, "recent_outreach": recent,
            "picks_per_day": Prefs(settings.data_dir, settings.raw).limits()["picks_per_day"],
            "jobs": {j["id"] for j in db.all()}}
    return text, refs
