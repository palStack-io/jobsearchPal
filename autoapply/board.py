"""The Leads board: a small planning board for the roles you chose to work on (2026-11-18).

A star puts a lead on the board in Shortlist; Unstar or Remove sends it back to Inbox. Cards move through four columns
(Shortlist, Research, Reach out, Ready to apply) and carry five steps you tick, a next step with a day, and notes.
"I applied" logs the application the usual way (history.log) and the card leaves the board for Applied.

Nothing here uses a model, and nothing here touches the work-search count: a person you reached for a card is not
counted toward the weekly log (Harun, 2026-11-18). Counts only go up; there are no streaks and no reminders.
"""
from __future__ import annotations

from datetime import datetime

from . import leads

COLUMNS = ("Shortlist", "Research", "Reach out", "Ready to apply")
HINTS = ("Starred from Inbox. Read it and decide.", "The team, the stack, the person.",
         "One real person per role helps most.", "Prep the resume and letter, then apply.")
STEPS = (("Read the posting", "Point at the fit score in Inbox to see why it scored that"),
         ("Look up the team and the stack", "Their site, blog and recent news"),
         ("Find one person to reach", "Add them to your contacts if you like"),
         ("Prep the resume, letter and answers", "Prep uses credits; nothing is sent until you say"),
         ("Apply, then press I applied", "The card moves to Applied"))
GONE = ("applied", "dismissed")  # lead statuses that take a card off the board
SCHEMA = """
create table if not exists board (
  key text primary key,          -- the lead's key (leads.key)
  col integer default 0,         -- 0..3, COLUMNS
  steps integer default 0,       -- bitmask of STEPS done
  next_step text default '', due text default '', notes text default '',
  added_at text, moved_at text
);
"""


def ensure(db):
    leads.ensure(db)
    db.conn.executescript(SCHEMA)


def _now(now: datetime | None) -> str:
    return (now or datetime.now()).strftime("%Y-%m-%d %H:%M")


def add(db, key: str, now: datetime | None = None) -> bool:
    """Puts a lead on the board (Shortlist) and stars it. False when there is no such lead."""
    ensure(db)
    if not leads.get(db, key):
        return False
    at = _now(now)
    db.conn.execute("insert or ignore into board (key, col, steps, added_at, moved_at) values (?, 0, 0, ?, ?)", (key, at, at))
    db.conn.execute("update leads set status = 'starred' where key = ? and status in ('new', 'dismissed')", (key,))
    db.conn.commit()
    return True


def add_job(db, job: dict, now: datetime | None = None) -> str:
    """A tracked role (the dashboard's shortlist) onto the board: its lead is made if it has none. Returns the key."""
    ensure(db)
    k = leads.key(job.get("company") or "", job.get("title") or "")
    if not leads.get(db, k):
        at = _now(now)
        db.conn.execute("insert into leads (key, title, company, location, salary, url, source, first_seen, last_seen, score, "
                        "status, job_id) values (?, ?, ?, ?, ?, ?, 'daily run', ?, ?, ?, 'starred', ?)",
                        (k, job.get("title"), job.get("company"), job.get("location") or "", job.get("pay") or "",
                         job.get("url") or "", at, at, job.get("score") or 0, job.get("id")))
    add(db, k, now)
    return k


def remove(db, key: str):
    """Off the board and back to Inbox (unstarred). Leads that already have a job keep their job status."""
    ensure(db)
    db.conn.execute("delete from board where key = ?", (key,))
    db.conn.execute("update leads set status = 'new' where key = ? and status = 'starred'", (key,))
    db.conn.commit()


def on_board(db, key: str) -> bool:
    ensure(db)
    return db.conn.execute("select 1 from board where key = ?", (key,)).fetchone() is not None


def sync_stars(db, now: datetime | None = None):
    """Every starred lead is on the board (stars from before the board, and stars set elsewhere); cards whose lead was
    applied to or dismissed leave it."""
    ensure(db)
    at = _now(now)
    db.conn.execute("insert or ignore into board (key, col, steps, added_at, moved_at) "
                    "select key, 0, 0, ?, ? from leads where status = 'starred'", (at, at))
    db.conn.execute(f"delete from board where key in (select key from leads where status in ({','.join('?' * len(GONE))})) "
                    "or key not in (select key from leads)", GONE)
    db.conn.commit()


def move(db, key: str, col: int, now: datetime | None = None):
    ensure(db)
    col = max(0, min(len(COLUMNS) - 1, int(col)))
    db.conn.execute("update board set col = ?, moved_at = ? where key = ?", (col, _now(now), key))
    db.conn.commit()


def toggle_step(db, key: str, i: int):
    ensure(db)
    if not 0 <= int(i) < len(STEPS):
        raise ValueError(i)
    db.conn.execute("update board set steps = (steps | ?) - (steps & ?) where key = ?", (1 << int(i), 1 << int(i), key))
    db.conn.commit()


def save(db, key: str, next_step: str = "", due: str = "", notes: str = ""):
    ensure(db)
    db.conn.execute("update board set next_step = ?, due = ?, notes = ? where key = ?",
                    (next_step.strip()[:200], due.strip()[:20], notes.strip()[:4000], key))
    db.conn.commit()


def cards(db) -> list[dict]:
    """Every card with its lead, in column order, newest move first within a column."""
    sync_stars(db)
    rows = db.conn.execute("select b.*, l.company, l.title, l.location, l.salary, l.url, l.apply_url, l.score, l.status, "
                           "l.job_id, l.source from board b join leads l on l.key = b.key "
                           "order by b.col, b.moved_at desc").fetchall()
    out = []
    for r in rows:
        c = dict(r)
        c["done"] = [bool(c["steps"] & (1 << i)) for i in range(len(STEPS))]
        c["n_done"] = sum(c["done"])
        out.append(c)
    return out


def columns(db) -> list[dict]:
    cs = cards(db)
    return [{"i": i, "name": n, "hint": HINTS[i], "cards": [c for c in cs if c["col"] == i]} for i, n in enumerate(COLUMNS)]


def summary(db) -> dict:
    """For the dashboard's one line: how many in play, how many ready, and the next due step."""
    cs = cards(db)
    today = datetime.now().strftime("%Y-%m-%d")
    due = sorted((c for c in cs if c["due"] and c["next_step"]), key=lambda c: c["due"])
    return {"n": len(cs), "ready": sum(1 for c in cs if c["col"] == 3),
            "due_today": [c for c in due if c["due"] <= today], "next": due[0] if due else None}
