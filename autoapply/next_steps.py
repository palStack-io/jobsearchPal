"""Your next step (2026-11-24): from a few answers about a layoff or a planned move, the dated steps that matter, each
with its source. The same rules run in the browser at palstack.io/next-step (planner/planner.js); the fixtures in
tests/fixtures/next_steps/ hold both engines to the same results.

Every step is a reminder, not advice: the person's own letters and plan documents decide their case. A state-specific
line shows only when that state's entry is official, high confidence and checked within a year (the gate)."""
from __future__ import annotations

import base64
import calendar
import json
from datetime import date, timedelta
from pathlib import Path

import yaml

SECTIONS = ("today_pool", "week1", "health", "money90", "bills", "before_you_go", "ongoing")
GROUP_ORDER = ("before_you_go", "week1", "health", "money90", "bills", "ongoing")
PATHS = ("laid_off", "told", "change", "looking")
AGES = ("u40", "40_54", "55_59", "60p", "unknown")
SIZES = ("u20", "20_99", "100p", "unknown")
HAS = ("severance", "401k", "401k_loan", "equity", "hsa", "fsa", "life", "student_loans")
GATE_DAYS = 365
TODAY_MAX = 3
_HERE = Path(__file__).parent


def load_facts(root: Path | None = None) -> dict:
    """{"version", "steps", "states"}: the rules file and the state entries the gate reads."""
    base = root or _HERE
    rules = yaml.safe_load((base / "next_steps.yaml").read_text())
    states = (yaml.safe_load((base / "work_search_states.yaml").read_text()) or {}).get("states") or {}
    return {"version": str(rules["version"]), "checked": str(rules.get("checked") or ""), "steps": rules["steps"], "states": states}


def _day(v) -> date | None:
    if v in (None, ""):
        return None
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v))
    except ValueError:
        raise ValueError("A date didn't read right. Use the date picker, or leave it blank.") from None


def validate_answers(raw: dict, states: dict | None = None) -> dict:
    """The answers, cleaned. Raises ValueError with a sentence for the person when something can't be used."""
    if not isinstance(raw, dict):
        raise ValueError("That plan didn't read right. Try saving it again from the planner.")
    if raw.get("v") != 1:
        raise ValueError("That plan is from a newer planner than this jobsearchPal. Update jobsearchPal, or answer the questions here.")
    path = raw.get("path")
    if path not in PATHS:
        raise ValueError("That plan didn't say which situation you're in. Answer the questions here instead.")
    state = (raw.get("state") or None)
    state = state.upper() if isinstance(state, str) else None
    if state and states is not None and state not in states:
        raise ValueError("We don't know that state. Pick it from the list.")
    cov = raw.get("coverage_end") or "unknown"
    if cov not in ("end_of_month", "unknown", "none"):
        _day(cov)
    age = raw.get("age") or "unknown"
    size = raw.get("size") or "unknown"
    if age not in AGES or size not in SIZES:
        raise ValueError("That plan didn't read right. Try saving it again from the planner.")
    has = [h for h in (raw.get("has") or []) if h in HAS]
    out = {"v": 1, "facts_version": str(raw.get("facts_version") or ""), "path": path,
           "last_day": (_day(raw.get("last_day")) or None), "state": state, "coverage_end": cov, "age": age,
           "size": size, "has": has, "vest_date": _day(raw.get("vest_date")), "bonus_date": _day(raw.get("bonus_date")),
           "noncompete": raw.get("noncompete") if raw.get("noncompete") in ("yes", "no", "unknown") else "unknown"}
    for k in ("last_day", "vest_date", "bonus_date"):
        out[k] = out[k].isoformat() if out[k] else None
    return out


def encode_code(answers: dict) -> str:
    raw = json.dumps(answers, separators=(",", ":"), sort_keys=True).encode()
    return "NS1." + base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_code(code: str, states: dict | None = None) -> dict:
    code = (code or "").strip()
    if not code.startswith("NS1."):
        raise ValueError("That doesn't look like a plan code. It starts with NS1.")
    body = code[4:]
    try:
        data = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    except (ValueError, json.JSONDecodeError):
        raise ValueError("That plan code didn't read right. Copy it again, all of it.") from None
    return validate_answers(data, states)


def _gate_ok(entry: dict, today: date) -> bool:
    try:
        checked = date.fromisoformat(str(entry.get("checked")))
    except ValueError:
        return False
    return entry.get("source") == "official" and entry.get("confidence") == "high" and (today - checked).days <= GATE_DAYS


def field_checked(facts: dict, state: str | None, field: str) -> str | None:
    """The day a state's line was checked (its own date for a layoff fact, the state entry's for the older fields)."""
    entry = (facts.get("states") or {}).get(state or "") or {}
    val = entry.get(field)
    return str((val if isinstance(val, dict) else entry).get("checked") or "") or None


def state_line(facts: dict, state: str | None, field: str, today: date) -> tuple[str | None, str | None]:
    """(the state's line for this field, the state's agency link). The line only passes the gate; the link always shows."""
    entry = (facts.get("states") or {}).get(state or "") or {}
    url = entry.get("url") or None
    val = entry.get(field)
    if isinstance(val, dict):  # a layoff fact (2026-11-24): its own text, source and gate
        if val.get("text") and _gate_ok(val, today):
            return str(val["text"]), val.get("url") or url
        return None, url
    if not entry or field not in entry or not _gate_ok(entry, today):
        return None, url
    name = entry.get("name") or state
    if field == "waiting_week":
        note = (f"In {name}, the first week of a claim is unpaid (the waiting week). Claim it anyway, or it may not count."
                if entry[field] else f"{name} has no unpaid waiting week.")
        return note, url
    return None, url


def _anchors(a: dict, today: date) -> dict:
    last = date.fromisoformat(a["last_day"]) if a.get("last_day") else None
    cov = a.get("coverage_end")
    if cov == "end_of_month":
        c = last.replace(day=calendar.monthrange(last.year, last.month)[1]) if last else None
    elif cov in ("unknown", "none", None):
        c = None
    else:
        c = date.fromisoformat(cov)
    year = (last or today).year + 1
    return {"L": last, "C": c, "today": today,
            "vest": date.fromisoformat(a["vest_date"]) if a.get("vest_date") else None,
            "bonus": date.fromisoformat(a["bonus_date"]) if a.get("bonus_date") else None,
            "tax_day": date(year, 4, 15), "jan31": date(year, 1, 31)}


def _applies(step: dict, a: dict) -> bool:
    if a["path"] not in step["path"]:
        return False
    w = step.get("when") or {}
    cov = a.get("coverage_end") or "unknown"
    cov_kind = "none" if cov == "none" else ("unknown" if cov == "unknown" else "known")
    checks = [
        set(w.get("has_all") or []) <= set(a["has"]),
        a["age"] in w["age_in"] if "age_in" in w else True,
        a["size"] in w["size_in"] if "size_in" in w else True,
        (cov_kind in ("known", "unknown") if w["coverage"] == "any_cover" else cov_kind == w["coverage"]) if "coverage" in w else True,
        ((a.get("last_day") is not None) == (w["last_day"] == "known")) if "last_day" in w else True,
        a["noncompete"] in w["noncompete_in"] if "noncompete_in" in w else True,
        bool(a.get("vest_date")) if w.get("vest") == "known" else True,
        bool(a.get("bonus_date")) if w.get("bonus") == "known" else True,
    ]
    return all(checks)


def plan(answers: dict, facts: dict, today: date) -> dict:
    """{"today", "next", "groups", "find_out", "facts_version"}: see the design doc for the rules."""
    a = validate_answers(answers, facts.get("states"))
    anchors = _anchors(a, today)
    made, find_out = [], []
    for step in facts["steps"]:
        if not _applies(step, a):
            continue
        due, unknown = None, False
        if step.get("due"):
            base = anchors.get(step["due"]["from"])
            if base is None:
                unknown = True
            else:
                due = base + timedelta(days=int(step["due"].get("days") or 0))
        note, url, note_checked = None, None, None
        if step.get("state_field") and a["state"]:
            fields = step["state_field"] if isinstance(step["state_field"], list) else [step["state_field"]]
            got = [state_line(facts, a["state"], f, today) for f in fields]
            note_checked = max((field_checked(facts, a["state"], f) or "" for f, (n, _) in zip(fields, got) if n), default="") or None
            notes = [n for n, _ in got if n]
            note = " ".join(notes) or None
            url = next((u for n, u in got if n and u), None) or next((u for _, u in got if u), None)
        if step.get("state_only") and not note:
            continue  # a step that is only its state's rule: no checked rule for this state, no step
        s = {"id": step["id"], "title": step["title"], "body": " ".join(str(step["body"]).split()),
             "due": due.isoformat() if due else None, "past": bool(due and due < today), "source": step.get("source"),
             "state_note": note, "state_url": url, "checked": str(step.get("checked") or "") or None,
             "state_checked": note_checked, "_section": step["section"], "_rank": int(step.get("rank") or 99)}
        (find_out if unknown and step["section"] != "today_pool" else made).append(s)
    past = [s for s in made if s["past"]]
    pool = [s for s in made if s["_section"] == "today_pool" and not s["past"]]
    pool.sort(key=lambda s: s["_rank"])
    today_list = sorted(past, key=lambda s: (s["due"], s["_rank"])) + pool[:max(0, TODAY_MAX - len(past))]
    taken = {s["id"] for s in today_list}
    rest = [s for s in made if s["id"] not in taken]
    for s in rest:  # Today holds three; the rest of the pool waits in week one
        if s["_section"] == "today_pool":
            s["_section"] = "week1"
    dated = sorted((s for s in rest if s["due"] and not s["past"]), key=lambda s: (s["due"], s["_rank"]))
    groups = []
    for sec in GROUP_ORDER:
        steps = sorted((s for s in rest if s["_section"] == sec), key=lambda s: (s["due"] or "9999", s["_rank"]))
        if steps:
            groups.append({"section": sec, "steps": [_public(s) for s in steps]})
    return {"today": [_public(s) for s in today_list], "next": _public(dated[0]) if dated else None, "groups": groups,
            "find_out": [_public(s) for s in find_out], "facts_version": facts["version"],
            "facts_checked": facts.get("checked") or None}


def _public(s: dict) -> dict:
    return {k: v for k, v in s.items() if not k.startswith("_")}


# ---- the app side: a plan becomes to-dos (2026-11-24) ------------------------------------------------------------
TAG = "[next_step:{}]"


def plan_steps(answers: dict, facts: dict, today: date) -> list[dict]:
    """The steps that become to-do rows: everything in Today, then every dated step, in date order."""
    p = plan(answers, facts, today)
    seen, out = set(), []
    for s in p["today"] + sorted((s for g in p["groups"] for s in g["steps"] if s["due"]), key=lambda s: s["due"]):
        if s["id"] not in seen:
            seen.add(s["id"])
            out.append(s)
    return out


def _claim_change(settings, answers: dict) -> tuple[bool, str] | None:
    """(claiming, state) to write, or None to leave the claim as it is."""
    if answers["path"] in ("laid_off", "told"):
        return (True, answers["state"]) if answers.get("state") else None
    return (False, "")


def _fresh(settings) -> bool:
    ws = settings.raw.get("work_search") or {}
    return "claiming" not in ws and not ws.get("state")


def preview_changes(settings, db, answers: dict, today: date, facts: dict | None = None) -> list[str]:
    """What a confirmed import would change, in plain sentences. Writes nothing."""
    from . import benefits
    facts = facts or load_facts()
    a = validate_answers(answers, facts["states"])
    out = []
    claim = _claim_change(settings, a)
    ws = settings.raw.get("work_search") or {}
    if claim and claim[0] and not (ws.get("claiming", bool(ws.get("state"))) and ws.get("state") == claim[1]):
        st = facts["states"].get(claim[1]) or {}
        n = st.get("weekly_minimum")
        out.append(f"Turn on unemployment for {st.get('name', claim[1])}: the work-search log"
                   + (f", {n} activities a week" if n else "") + ", claim-day and RESEA reminders.")
    elif claim and not claim[0] and ws.get("claiming", bool(ws.get("state"))):
        out.append("Turn off the unemployment side: the work-search log and claim reminders. Your log's history stays.")
    steps = plan_steps(a, facts, today)
    dated = [s for s in steps if s["due"]]
    out.append(f"Add {len(steps)} steps to your to-do list" + (f", {len(dated)} of them with a date." if dated else "."))
    benefits.ensure(db)
    old = db.conn.execute("select count(*) from ui_tasks where done = 0 and note like ?", ("%[next_step:%",)).fetchone()[0]
    kept = db.conn.execute("select count(*) from ui_tasks where done = 1 and note like ?", ("%[next_step:%",)).fetchone()[0]
    if old:
        out.append(f"Remove {old} unfinished {'step' if old == 1 else 'steps'} from your earlier plan."
                   + (f" The {kept} you finished stay as a record." if kept else ""))
    return out


def import_plan(settings, db, answers: dict, today: date, replace: bool = False, facts: dict | None = None,
                set_claim: bool = True) -> dict:
    """Turn a plan into to-do rows and, on a fresh install or a confirmed change (replace), set the claim.
    Never sets claim_start: that's the day the person filed, which only they know."""
    from . import benefits, setup
    facts = facts or load_facts()
    a = validate_answers(answers, facts["states"])
    if a["path"] in ("laid_off", "told") and not a["state"] and (replace or _fresh(settings)):
        raise ValueError("Pick the state you worked in, so the claim steps and the work-search log follow its rules.")
    claiming_set = False
    claim = _claim_change(settings, a)
    if claim and set_claim and (replace or _fresh(settings)):
        setup.save_claim(settings, claim[0], claim[1], facts["states"].get(claim[1]) if claim[1] else None)
        claiming_set = True
    benefits.ensure(db)
    removed = 0
    if replace:
        removed = db.conn.execute("delete from ui_tasks where done = 0 and note like ?", ("%[next_step:%",)).rowcount
        db.conn.commit()
    added = skipped = 0
    for s in plan_steps(a, facts, today):
        tag = TAG.format(s["id"])
        if db.conn.execute("select 1 from ui_tasks where note like ?", (f"%{tag}%",)).fetchone():
            skipped += 1
            continue
        first = s["body"].split(". ")[0].rstrip(".") + "."
        on = f" (checked {date.fromisoformat(s['checked']).strftime('%b %-d, %Y')})" if s.get("checked") else ""
        src = f"Source: {s['source']['name']}{on}." if s["source"] else "A general reminder."
        if s.get("state_note"):
            first = f"{first} {s['state_note']}"
        benefits.add_task(db, s["title"], s["due"] or "", "next_step", f"{first} {src}"[:400 - len(tag) - 1] + " " + tag)  # the tag always fits
        added += 1
    return {"added": added, "skipped": skipped, "removed": removed, "claiming_set": claiming_set}


def back_to_work(settings, db, set_claim: bool = True) -> dict:
    """'I'm working again': the unemployment side off, the unfinished plan steps gone. The work-search log keeps its
    history (it is the person's record for the agency)."""
    from . import benefits, setup
    if set_claim:
        setup.save_claim(settings, False)
    benefits.ensure(db)
    removed = db.conn.execute("delete from ui_tasks where done = 0 and note like ?", ("%[next_step:%",)).rowcount
    db.conn.commit()
    return {"removed": removed}


# ---- the quiet "Something changed?" line and the plan's progress (2026-11-24; local only, nothing is sent) --------
QUIET_DAYS = 21


def sign(settings, db, today: date) -> dict | None:
    """One gentle line for the dashboard when the app sees a sign that the person's situation may have changed, or
    None. Each sign shows once: Not now (nudge state "done") puts it away for good, and a new sign gets a new key."""
    import hashlib
    states = db.nudge_states()
    ws = settings.raw.get("work_search") or {}
    claiming = ws.get("claiming", bool(ws.get("state")))
    if not claiming:
        for e in db.events():
            if e["kind"] != "agency":
                continue
            try:
                age = (today - date.fromisoformat(str(e["at"])[:10])).days
            except ValueError:
                continue
            if 0 <= age <= 30:
                key = "sit:agency:" + hashlib.sha1(str(e["id"]).encode()).hexdigest()[:12]
                if key not in states:
                    return {"key": key, "line": "An email from your state's unemployment agency came in. If your "
                            "situation changed, jobsearchPal can put the claim steps on your list."}
                break
    sent = [str(j["submitted_at"])[:10] for j in db.by_status("submitted") if j.get("submitted_at")]
    if sent:
        last = max(sent)
        try:
            quiet = (today - date.fromisoformat(last)).days
        except ValueError:
            return None
        key = f"sit:quiet:{last}"
        if quiet >= QUIET_DAYS and key not in states:
            return {"key": key, "line": "A quiet few weeks. That happens, and rest counts too. If something changed, "
                    "like a layoff or a new job, you can tell jobsearchPal."}
    return None


def progress(db, today: date) -> dict | None:
    """The plan's own count for the Stats page: steps done of steps, and the next three still open. None without a
    plan. Kept on this computer; nothing is sent."""
    from . import benefits
    rows = [t for t in benefits.tasks(db, include_done=True) if t["kind"] == "next_step"]
    if not rows:
        return None
    left = sorted((t for t in rows if not t["done"]), key=lambda t: t["due"] or "9999")
    nxt = []
    for t in left[:3]:
        when = "Any time"
        if t["due"]:
            d = date.fromisoformat(t["due"])
            when = d.strftime("%a, %b %-d") + (" (the usual date has passed)" if d < today else "")
        nxt.append({"title": t["title"], "when": when})
    return {"done": sum(1 for t in rows if t["done"]), "total": len(rows), "next": nxt}
