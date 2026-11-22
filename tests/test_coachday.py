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
