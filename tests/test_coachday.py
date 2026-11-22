"""The coach (coachday.py): digest in, one validated call out, outreach the person sends themselves."""
import pytest

from autoapply import coach, coachday
from autoapply.config import Settings
from autoapply.db import DB
from autoapply.prefs import Prefs


@pytest.fixture
def env(tmp_path):
    (tmp_path / "materials" / "context").mkdir(parents=True)
    (tmp_path / "materials" / "context" / "me.md").write_text("Analytics engineer. Cut audits from 3 days to 2 hours.")
    (tmp_path / "CLAUDE.local.md").write_text("Pay ask: $150K.")
    s = Settings(root=tmp_path, raw={"data_dir": str(tmp_path / "data")}, profile={"first_name": "Pat"})
    db = DB(s.data_dir / "t.db")
    return s, db


# ---- Task 1: storage and settings ---------------------------------------------------------------

def test_ensure_creates_tables_and_contact_columns(env):
    s, db = env
    coach.ensure(db)
    coachday.ensure(db)
    coachday.ensure(db)  # twice is fine
    cols = {r[1] for r in db.conn.execute("pragma table_info(contacts)")}
    assert {"email", "link"} <= cols
    tables = {r[0] for r in db.conn.execute("select name from sqlite_master where type='table'")}
    assert {"coach_day", "outreach"} <= tables


def test_outreach_key_stable():
    a = coachday.outreach_key("Priya Rao", "referral", "gh:x:1")
    assert a == coachday.outreach_key("priya rao", "Referral", "gh:x:1")
    assert a != coachday.outreach_key("Priya Rao", "referral", "gh:x:2")
    assert len(a) == 12


def test_model_defaults_sonnet(env):
    s, db = env
    p = Prefs(s.data_dir, s.raw)
    p.data.pop("coach_model", None)
    assert coachday.model(p, "coach") == "sonnet"
    p.data["drafts_model"] = "haiku"
    assert coachday.model(p, "drafts") == "haiku"
    assert coachday.enabled(p) is False
    p.data["coach"] = True
    assert coachday.enabled(p) is True


def test_set_contact_email_updates_existing(env):
    s, db = env
    cid = coach.add_contact(db, "Priya Rao", "Scribd", "Email")
    assert coach.set_contact_email(db, "Priya Rao", "Scribd", "priya@scribd.com", "https://example.org/p") == cid
    row = db.conn.execute("select email, link from contacts where id = ?", (cid,)).fetchone()
    assert tuple(row) == ("priya@scribd.com", "https://example.org/p")
    other = coach.set_contact_email(db, "Dana Wu", "Render", "dana@render.com")
    assert other != cid


# ---- Task 2: the digest -------------------------------------------------------------------------

from datetime import date, datetime  # noqa: E402

NOW = datetime(2026, 11, 21, 6, 30)


def _job(db, jid, company, title, status="new", desc="", outcome=None, submitted_at=None):
    db.upsert_job({"id": jid, "ats": "greenhouse", "company": company, "title": title, "location": "Remote",
                   "url": f"https://example.org/{jid}", "apply_url": f"https://example.org/{jid}", "description": desc})
    fields = {"status": status}
    if outcome:
        fields.update(outcome=outcome, outcome_at="2026-11-10 09:00")
    if submitted_at:
        fields["submitted_at"] = submitted_at
    db.update(jid, **fields)


def _seed(db):
    _job(db, "gh:scribd:1", "Scribd", "Staff Analytics Engineer", "submitted", outcome="interview", submitted_at="2026-11-01 10:00")
    _job(db, "gh:render:1", "Render", "Senior Analytics Engineer", "new",
         desc="You'll join the data team. The hiring manager, Dana Wu, leads it.")
    _job(db, "gh:hooli:1", "Hooli", "Data Engineer", "new", desc="We need dbt.")
    db.add_event({"id": "<m1>", "job_id": "gh:scribd:1", "kind": "interview", "at": "2026-11-10 09:00",
                  "sender": "Priya Rao <priya@scribd.com>", "subject": "Next steps", "body": "Can you talk Tuesday?"})
    db.add_event({"id": "<m2>", "job_id": "gh:scribd:1", "kind": "recruiter", "at": "2026-11-12 09:00",
                  "sender": "Priya Rao <Priya@scribd.com>", "subject": "Re: Next steps", "body": "Great."})
    db.add_sent("<s1>", "2026-11-13 08:00", ["priya@scribd.com"], "Re: Next steps", "<m2>")
    coach.add_contact(db, "Sam Lee", "Hooli", "Coffee or video chat", at="2026-10-20")


def test_people_dedupes_and_refs(env):
    s, db = env
    _seed(db)
    cands = [{"id": "gh:render:1", "company": "Render", "description": db.get("gh:render:1")["description"]}]
    ppl = coachday.people(db, cands, NOW)
    assert [p["ref"] for p in ppl] == [f"p{i}" for i in range(1, len(ppl) + 1)]
    priya = [p for p in ppl if p["name"] == "Priya Rao"]
    assert len(priya) == 1 and priya[0]["email"] == "priya@scribd.com" and priya[0]["company"] == "Scribd"
    assert priya[0]["last"] == "2026-11-13"  # her last email, or yours to her, whichever is later
    assert any(p["name"] == "Sam Lee" and p["company"] == "Hooli" for p in ppl)


def test_named_in_posting(env):
    s, db = env
    _seed(db)
    cands = [{"id": "gh:render:1", "company": "Render", "description": db.get("gh:render:1")["description"]}]
    dana = [p for p in coachday.people(db, cands, NOW) if p["name"] == "Dana Wu"]
    assert dana and dana[0]["link"] == "named in posting" and dana[0]["email"] == "" and dana[0]["company"] == "Render"


def test_patterns_need_two(env):
    s, db = env
    _seed(db)
    assert not any(" of " in line and "contacted" in line for line in coachday.patterns(db, NOW))
    coach.add_contact(db, "Ann Bo", "Acme", at="2026-10-01")
    _job(db, "gh:acme:1", "Acme", "Analytics Engineer", "submitted", outcome="interview", submitted_at="2026-11-02 10:00")
    _job(db, "gh:globex:1", "Globex", "Analytics Engineer", "submitted", outcome="interview", submitted_at="2026-11-03 10:00")
    coach.add_contact(db, "Cy Do", "Scribd", at="2026-10-15")
    lines = coachday.patterns(db, NOW)
    assert "2 of 3 interviews came from roles where you contacted someone first" in lines


def test_digest_dates_and_budget(env):
    s, db = env
    _seed(db)
    for i in range(15):
        _job(db, f"gh:co{i}:1", f"Company{i}", "Analytics Engineer", "new", desc="x " * 2500)
    assert coachday.fmt_day(date(2026, 11, 24)) == "Tue 24 Nov"
    text, refs = coachday.digest(db, s, NOW)
    assert len(text) <= 60000
    assert refs["picks_per_day"] == Prefs(s.data_dir, s.raw).limits()["picks_per_day"]
    assert set(refs) >= {"candidates", "people", "nudges", "recent_outreach", "picks_per_day"}
    assert "Sat 21 Nov" in text  # today, in the one date form the validator accepts
