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
    from .oneshot import fence  # the same "data, not instructions" markers the apply call uses
    return fence(name, json.dumps(obj, ensure_ascii=False, indent=0))


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
            "jobs": {j["id"] for j in db.all()}, "screens": screens, "dismissed": dismissed_keys(db),
            "names": vocab(db, ppl, about, plan, todo, now)}
    return text, refs


def dismissed_keys(db) -> set[str]:
    ensure(db)
    return {r[0] for r in db.conn.execute("select key from outreach where state = 'dismissed'")}


def vocab(db, ppl: list[dict], about: str, plan: str, todo: list[dict], now: datetime) -> set[str]:
    """Lower-cased words a coach line may name: people, companies and titles you track, your own notes, the computed
    patterns and open threads. Posting descriptions are left out on purpose: text in a posting can't introduce a name."""
    parts = [p["name"] + " " + p["company"] for p in ppl] + [about, plan] + patterns(db, now)
    parts += [f"{j['company']} {j['title']} {j.get('location') or ''}" for j in db.all()]
    parts += [f"{n.get('title') or ''} {n.get('why') or ''}" for n in todo]
    return {w.lower() for w in re.findall(r"[A-Za-z][\w'&.-]*", " ".join(parts))}


# ---- validation: nothing the person didn't give us gets through ---------------------------------

KINDS = ("referral", "hiring_team", "check_in", "thank_you", "reply")
BANNED = ("streak", "guarantee", "dream job", "you'll land", "you will land", "don't break")
COMMON_CAPS = {w.lower() for w in (
    "I Your You Today Tomorrow This That These Those Their They He She It We Next Then Also And But Or So If When "
    "Staff Senior Lead Principal Engineer Engineering Analytics Data Manager Director Head Remote Hybrid "
    "Monday Tuesday Wednesday Thursday Friday Saturday Sunday Mon Tue Wed Thu Fri Sat Sun "
    "January February March April May June July August September October November December "
    "Jan Feb Mar Apr Jun Jul Aug Sep Sept Oct Nov Dec").split()}
DATE_RX = re.compile(r"\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun) \d{1,2} (?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b")
NUM_RX = re.compile(r"\d+(?:[.,]\d+)*")
EMOJI_RX = re.compile("[\U0001F300-\U0001FAFF\u2600-\u27BF]")


def clean_text(text: str, digest_text: str, names: set[str] | None = None) -> str | None:
    """The text when every number, date and name in it comes from the digest and the copy rules hold; else None."""
    t = str(text or "").strip()
    if not t or EMOJI_RX.search(t) or t.count("!") > 1 or any(b in t.lower() for b in BANNED):
        return None
    for d in DATE_RX.findall(t):
        if d not in digest_text:
            return None
    nums = set(NUM_RX.findall(digest_text))
    if any(n not in nums for n in NUM_RX.findall(t)):
        return None
    if names is not None:
        for m in re.finditer(r"[A-Za-z][\w'&.-]*", t):
            w = m.group(0)
            if not w[0].isupper():
                continue
            before = t[:m.start()].rstrip()
            if not before or before[-1] in ".!?:;\"(-":  # the first word of a sentence may be capitalised
                continue
            if w.lower().rstrip(".") not in names and w.lower().rstrip(".") not in COMMON_CAPS:
                return None
    return t


def validate(reply: dict, refs: dict, digest_text: str, now: datetime) -> tuple[dict, list[str]]:
    """(clean, dropped): only what checks out against the digest, in the spec's shape."""
    names = refs.get("names") or set()
    dropped: list[str] = []
    reply = reply if isinstance(reply, dict) else {}

    def txt(v, what):
        out = clean_text(v, digest_text, names)
        if out is None and v:
            dropped.append(f"{what}: {str(v)[:80]}")
        return out

    def items(key):
        v = reply.get(key)
        return [x for x in v if isinstance(x, dict) or isinstance(x, str)] if isinstance(v, list) else []

    clean = {"note": [], "picks": [], "skips": [], "outreach": [], "followups": [], "prep": [], "check": []}
    clean["note"] = [t for t in (txt(x, "note") for x in items("note") if isinstance(x, str)) if t]
    cands = refs.get("candidates") or {}
    picked_cos: set[str] = set()
    for x in (x for x in items("picks") if isinstance(x, dict)):
        jid = x.get("id")
        c = cands.get(jid)
        if not c:
            dropped.append(f"pick: unknown id {jid}")
            continue
        co = (c.get("company") or "").lower()
        if co in picked_cos or len(clean["picks"]) >= int(refs.get("picks_per_day") or 0):
            dropped.append(f"pick: {jid} (one per company, or over today's limit)")
            continue
        picked_cos.add(co)
        clean["picks"].append({"id": jid, "why": txt(x.get("why"), "why") or "", "angle": txt(x.get("angle"), "angle") or ""})
    picked = {p["id"] for p in clean["picks"]}
    for x in (x for x in items("skips") if isinstance(x, dict)):
        if x.get("id") in cands and x.get("id") not in picked:
            clean["skips"].append({"id": x["id"], "reason": txt(x.get("reason"), "skip") or "not a close fit"})
        else:
            dropped.append(f"skip: {x.get('id')}")
    ppl = refs.get("people") or {}
    recent = {r["key"]: r for r in refs.get("recent_outreach") or []}
    soon = (now.date() - timedelta(days=3)).isoformat()
    jobs = set(refs.get("jobs") or ()) | set(cands)
    seen: set[str] = set()
    for x in (x for x in items("outreach") if isinstance(x, dict)):
        p, kind, job = ppl.get(x.get("person")), x.get("kind"), x.get("job") or None
        if not p or kind not in KINDS or (job and job not in jobs):
            dropped.append(f"outreach: {x.get('person')} {kind} {job}")
            continue
        key = outreach_key(p["name"], kind, job)
        r = recent.get(key)
        if key in seen or key in (refs.get("dismissed") or ()) or (r and r["day"] > soon):
            dropped.append(f"outreach: {p['name']} {kind} (dismissed or suggested lately)")
            continue
        why = txt(x.get("why"), "outreach why")
        if why is None:
            continue
        seen.add(key)
        clean["outreach"].append({"person": x["person"], "kind": kind, "job": job, "why": why, "key": key})
    nudge_keys = set(refs.get("nudges") or {})
    clean["followups"] = [k for k in items("followups") if isinstance(k, str) and k in nudge_keys]
    dropped += [f"followup: {k}" for k in items("followups") if isinstance(k, str) and k not in nudge_keys]
    for x in (x for x in items("prep") if isinstance(x, dict)):
        if x.get("job") not in jobs:
            continue
        its = [t for t in (txt(i, "prep") for i in (x.get("items") or []) if isinstance(i, str)) if t]
        if its:
            clean["prep"].append({"job": x["job"], "items": its[:5]})
    clean["check"] = [t for t in (txt(x, "check") for x in items("check") if isinstance(x, str)) if t][:5]
    return clean, dropped


# ---- the call ------------------------------------------------------------------------------------

from pathlib import Path  # noqa: E402

PROMPT = Path(__file__).resolve().parent.parent / "prompts" / "coach.md"
TAG = "coach-1call"


def prompt(digest_text: str, voice: str, picks: int = 2) -> str:
    v = f"Their own notes on how they write:\n{voice}" if voice else ""
    return PROMPT.read_text().replace("{{PICKS}}", str(picks)).replace("{{VOICE}}", v).replace("{{DIGEST}}", digest_text)


def parse(reply: str) -> dict:
    m = re.search(r"\{.*\}", reply or "", re.S)
    if not m:
        raise ValueError("no JSON in the reply")
    data = json.loads(m.group(0))
    if not isinstance(data, dict):
        raise ValueError("the reply wasn't a JSON object")
    return data


def today(db, day: date) -> dict | None:
    ensure(db)
    row = db.conn.execute("select json from coach_day where day = ?", (day.isoformat(),)).fetchone()
    try:
        return json.loads(row[0]) if row else None
    except ValueError:
        return None


def angle_for(db, job_id: str, day: date) -> str:
    row = today(db, day) or {}
    return next((p.get("angle") or "" for p in row.get("picks") or [] if p.get("id") == job_id), "")


def _log(db, model: str, u: dict, err: str | None = None) -> int:
    db.log_usage("daily", f"claude:{model}:{TAG}", 0, 0, 0.0, int(u.get("ms", 0)), err, est=float(u.get("est", 0.0)),
                 tokens=int(u.get("tokens", 0)), split=u.get("split"))
    return db.conn.execute("select max(rowid) from usage").fetchone()[0]


def run(db, settings, ask=None, log=None, now: datetime | None = None) -> tuple[int, str]:
    """0 = saved, 2 = not this way (off, already coached today, nothing to coach), 1 = failed (the old path runs)."""
    from . import nudges, oneshot
    from .prefs import Prefs
    now = now or datetime.now()
    ensure(db)
    pr = Prefs(settings.data_dir, settings.raw)
    if not enabled(pr):
        return 2, "coach off"
    if today(db, now.date()) is not None:
        return 2, "coach: already done today"
    text, refs = digest(db, settings, now)
    if not refs["candidates"] and not refs["nudges"] and not refs["screens"]:
        return 2, "coach: nothing to coach today"
    m = model(pr, "coach")
    _, voice = oneshot.about_you(settings.root)
    try:
        reply, u = (ask or oneshot.ask_claude)(prompt(text, voice, refs["picks_per_day"]), m)
    except Exception as e:  # noqa: BLE001
        _log(db, m, {}, str(e)[:200])
        return 1, f"coach call failed: {str(e)[:200]}"
    try:
        data = parse(reply)
    except ValueError as e:
        _log(db, m, u, f"bad reply: {e}")
        return 1, f"coach reply wasn't usable: {e}"
    clean, dropped = validate(data, refs, text, now)
    if refs["candidates"] and refs["picks_per_day"] and not clean["picks"]:
        _log(db, m, u, "no valid picks")
        return 1, "coach: no valid picks"
    uid = _log(db, m, u)
    day = now.date().isoformat()
    jobs = {jid: db.get(jid) for jid in [p["id"] for p in clean["picks"]]}
    picks = [{"id": p["id"], "why": p["why"], "angle": p["angle"],
              **({"by_hand": True} if (jobs[p["id"]] or {}).get("ats") == "workday" else {})} for p in clean["picks"]]
    (settings.data_dir / "daily").mkdir(exist_ok=True)
    (settings.data_dir / "daily" / "picks.json").write_text(json.dumps(picks, indent=1))
    for sk in clean["skips"]:
        db.update(sk["id"], status="skipped", error=f"coach: {sk['reason']}")
    people = refs["people"]
    for o in clean["outreach"]:
        p = people[o["person"]]
        o.update(name=p["name"], company=p["company"], email=p["email"])
        db.conn.execute("insert or replace into outreach (key, day, person, email, company, job_id, kind, why, draft_path, "
                        "state, changed) values (?, ?, ?, ?, ?, ?, ?, ?, ?, 'suggested', ?)",
                        (o["key"], day, p["name"], p["email"], p["company"], o["job"], o["kind"], o["why"],
                         str(nudges.draft_path(settings.data_dir, o["key"])), now.strftime("%Y-%m-%d %H:%M")))
    clean["skips"] = [sk | {"company": (db.get(sk["id"]) or {}).get("company", ""), "title": (db.get(sk["id"]) or {}).get("title", "")}
                      for sk in clean["skips"]]
    clean["dropped"] = len(dropped)
    db.conn.execute("insert into coach_day (day, json, usage_id) values (?, ?, ?)", (day, json.dumps(clean), uid))
    db.conn.commit()
    return 0, (f"Coach: {len(picks)} picks, {len(clean['outreach'])} people to reach, {len(clean['skips'])} skipped"
               + (f", {len(dropped)} items dropped by the checks" if dropped else ""))


# ---- the drafts call -----------------------------------------------------------------------------

DRAFTS_PROMPT = Path(__file__).resolve().parent.parent / "prompts" / "drafts-once.md"
DRAFTS_TAG = "drafts-1call"


def _draft_items(db, settings, now: datetime, limit: int) -> list[dict]:
    """Open reminders without a draft, then the coach's suggestions without one, at most `limit`."""
    from . import nudges
    items = [{"key": r["key"], "kind": r["kind"], "to": r["to"], "person": "", "company": r["company"] or "",
              "role": r["role"] or "", "why": r["why"], "their_email": r["their_email"], "draft_path": r["draft_path"]}
             for r in nudges.for_agent(db, settings.data_dir, now=now)]
    ensure(db)
    for r in db.conn.execute("select * from outreach where state = 'suggested' order by day desc"):
        r = dict(r)
        if Path(r["draft_path"] or "").exists():
            continue
        job = db.get(r["job_id"]) if r["job_id"] else None
        items.append({"key": r["key"], "kind": r["kind"], "to": r["email"] or "", "person": r["person"],
                      "company": r["company"] or "", "role": (job or {}).get("title", ""), "why": r["why"],
                      "posting": ((job or {}).get("description") or "")[:1500], "draft_path": r["draft_path"]})
    return items[:limit]


def drafts(db, settings, ask=None, log=None, now: datetime | None = None) -> tuple[int, str]:
    """One call writes every draft that's due. 0 = written, 2 = off or nothing due, 1 = failed (the agent stage runs)."""
    from . import oneshot
    from .prefs import Prefs
    now = now or datetime.now()
    pr = Prefs(settings.data_dir, settings.raw)
    if not enabled(pr):
        return 2, "coach off"
    items = _draft_items(db, settings, now, int(pr.limits().get("drafts_per_day") or 0) or 6)
    if not items:
        return 2, "drafts: nothing due"
    about, voice = oneshot.about_you(settings.root)
    first = (settings.profile or {}).get("first_name") or ""
    shown = [{k: v for k, v in i.items() if k != "draft_path"} for i in items]
    data_text = "\n\n".join([oneshot.fence("about the person", about), oneshot.fence("items", json.dumps(shown, ensure_ascii=False, indent=0, default=str))])
    text = (DRAFTS_PROMPT.read_text().replace("{{FIRST}}", first)
            .replace("{{VOICE}}", f"Their own notes on how they write:\n{voice}" if voice else "").replace("{{ITEMS}}", data_text))
    m = model(pr, "drafts")
    try:
        reply, u = (ask or oneshot.ask_claude)(text, m)
        data = parse(reply)
    except Exception as e:  # noqa: BLE001
        db.log_usage("daily", f"claude:{m}:{DRAFTS_TAG}", 0, 0, 0.0, 0, str(e)[:200])
        return 1, f"drafts call failed: {str(e)[:200]}"
    db.log_usage("daily", f"claude:{m}:{DRAFTS_TAG}", 0, 0, 0.0, int(u.get("ms", 0)), None, est=float(u.get("est", 0.0)),
                 tokens=int(u.get("tokens", 0)), split=u.get("split"))
    names = {w.lower() for w in re.findall(r"[A-Za-z][\w'&.-]*", " ".join([about, first, json.dumps(shown, default=str)]))}
    wrote = 0
    for i in items:
        body = data.get(i["key"])
        ok = clean_text(body, data_text + " " + first, names) if isinstance(body, str) else None
        if not ok:
            continue
        p = Path(i["draft_path"])
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(ok.strip() + "\n")
        wrote += 1
    return 0, f"Drafts: {wrote} written" + (f", {len(items) - wrote} left for the agent or you" if wrote < len(items) else "")


# ---- outreach state: the person sends; we notice -------------------------------------------------

STATES = ("suggested", "sent", "not_now", "dismissed")
NOT_NOW_DAYS = 3


def set_state(db, key: str, state: str) -> None:
    if state not in STATES:
        raise ValueError(f"no outreach state {state}")
    ensure(db)
    db.conn.execute("update outreach set state = ?, changed = ? where key = ?",
                    (state, datetime.now().strftime("%Y-%m-%d %H:%M"), key))
    db.conn.commit()


def mark_sent_from_mail(db) -> int:
    """A suggestion with an address counts as sent once your Sent mail shows a message to it on or after that day."""
    ensure(db)
    sent = [(r["at"] or "", (r["to_addrs"] or "").lower().split()) for r in db.sent()]
    n = 0
    for key, day, addr in db.conn.execute("select key, day, lower(email) from outreach where state in ('suggested', 'not_now') "
                                          "and coalesce(email, '') != ''").fetchall():
        hit = [at for at, to in sent if addr in to and at[:10] >= day]
        if hit:
            db.conn.execute("update outreach set state = 'sent', changed = ? where key = ?", (max(hit)[:16], key))
            n += 1
    db.conn.commit()
    return n


def compose_url(row: dict, draft: str, gmail: bool, first_name: str = "") -> str:
    """Opens the note in the person's own mail, ready to send: Gmail's compose window, or mailto: for anything else."""
    from urllib.parse import quote
    subject = f"{row.get('role')} at {row.get('company')}" if row.get("job_id") and row.get("role") else \
        (f"Hello from {first_name}" if first_name else "Hello")
    to = row.get("email") or ""
    if gmail:
        return (f"https://mail.google.com/mail/?view=cm&fs=1&to={quote(to)}&su={quote(subject)}&body={quote(draft or '')}")
    return f"mailto:{quote(to)}?subject={quote(subject)}&body={quote(draft or '')}"


def rows(db, settings, day: date) -> list[dict]:
    """Today's outreach suggestions still open (not sent, not dismissed, not hidden for now), each with its draft
    and the Send link."""
    from . import mailboxes
    ensure(db)
    mark_sent_from_mail(db)
    soon = (datetime.now() - timedelta(days=NOT_NOW_DAYS)).strftime("%Y-%m-%d %H:%M")
    gmail = mailboxes.main_provider() == "gmail"
    first = (settings.profile or {}).get("first_name") or ""
    out = []
    for r in db.conn.execute("select * from outreach where day = ? order by rowid", (day.isoformat(),)):
        r = dict(r)
        if r["state"] in ("sent", "dismissed") or (r["state"] == "not_now" and (r["changed"] or "") > soon):
            continue
        job = db.get(r["job_id"]) if r["job_id"] else None
        r["role"] = (job or {}).get("title", "")
        p = Path(r["draft_path"] or "")
        r["draft"] = p.read_text() if r["draft_path"] and p.is_file() else ""
        r["send"] = compose_url(r, r["draft"], gmail, first)
        out.append(r)
    return out
