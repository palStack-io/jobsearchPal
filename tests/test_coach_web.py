"""The coach on the dashboard: outreach the person sends themselves, sent detection, undo a skip, the card, the brief."""
import json
from datetime import datetime

import pytest

from autoapply import coach, coachday
from autoapply.config import Settings
from autoapply.db import DB
from autoapply.prefs import Prefs

NOW = datetime(2026, 11, 21, 6, 30)


@pytest.fixture
def env(tmp_path):
    (tmp_path / "materials" / "context").mkdir(parents=True)
    s = Settings(root=tmp_path, raw={"data_dir": str(tmp_path / "data")}, profile={"first_name": "Pat"})
    db = DB(s.data_dir / "autoapply.db")
    coachday.ensure(db)
    db.upsert_job({"id": "gh:render:1", "ats": "greenhouse", "company": "Render", "title": "Senior Analytics Engineer",
                   "location": "Remote", "url": "u", "apply_url": "u", "description": ""})
    return s, db


def _outreach(db, person="Dana Wu", email="", day="2026-11-21", kind="hiring_team", job="gh:render:1", state="suggested"):
    key = coachday.outreach_key(person, kind, job)
    db.conn.execute("insert into outreach (key, day, person, email, company, job_id, kind, why, draft_path, state) "
                    "values (?, ?, ?, ?, 'Render', ?, ?, 'Dana Wu leads the team', '', ?)", (key, day, person, email, job, kind, state))
    db.conn.commit()
    return key


def _state(db, key):
    return db.conn.execute("select state from outreach where key = ?", (key,)).fetchone()[0]


def _client(s, db):
    from web_client import client
    return client(s, db)


def test_sent_detected_from_mail(env):
    s, db = env
    k1 = _outreach(db, email="dana@render.com")
    k2 = _outreach(db, person="Sam Lee", email="sam@hooli.com", kind="check_in", job=None)
    db.add_sent("<s1>", "2026-11-21 09:00", ["dana@render.com"], "Hello", "")
    db.add_sent("<s0>", "2026-11-19 09:00", ["sam@hooli.com"], "Old", "")  # before the suggestion: doesn't count
    assert coachday.mark_sent_from_mail(db) == 1
    assert _state(db, k1) == "sent" and _state(db, k2) == "suggested"


def test_set_state_records_change(env):
    s, db = env
    k = _outreach(db)
    coachday.set_state(db, k, "not_now")
    row = db.conn.execute("select state, changed from outreach where key = ?", (k,)).fetchone()
    assert row[0] == "not_now" and row[1]
    with pytest.raises(ValueError):
        coachday.set_state(db, k, "teleported")


def test_dismiss_post(env):
    s, db = env
    k = _outreach(db)
    r = _client(s, db).post(f"/outreach/{k}", data={"action": "dismiss"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith("#coach")
    assert _state(db, k) == "dismissed"
    assert k in coachday.dismissed_keys(db)


def test_save_email_fills_compose(env, monkeypatch):
    s, db = env
    monkeypatch.setenv("EMAIL_PROVIDER", "gmail")
    k = _outreach(db)
    path = s.data_dir / "drafts" / f"{k}.md"
    db.conn.execute("update outreach set draft_path = ? where key = ?", (str(path), k))
    db.conn.commit()
    c = _client(s, db)
    c.post(f"/outreach/{k}", data={"action": "save", "email": "dana@render.com", "link": "https://example.org/dana",
                                    "draft": "Hi Dana,\n\nHello.\n\nPat"}, follow_redirects=False)
    assert path.read_text().startswith("Hi Dana")
    row = dict(db.conn.execute("select * from outreach where key = ?", (k,)).fetchone())
    assert row["email"] == "dana@render.com"
    assert any(x["email"] == "dana@render.com" for x in coach.contacts(db))
    url = coachday.compose_url(row, path.read_text(), gmail=True)
    assert url.startswith("https://mail.google.com/mail/?view=cm") and "dana%40render.com" in url and "Hi%20Dana" in url


def test_mailto_for_non_gmail(env):
    s, db = env
    row = {"email": "", "person": "Dana Wu", "company": "Render", "job_id": "gh:render:1", "role": "Senior Analytics Engineer"}
    url = coachday.compose_url(row, "Hi Dana", gmail=False, first_name="Pat")
    assert url.startswith("mailto:?subject=") and "Senior%20Analytics%20Engineer%20at%20Render" in url
    url = coachday.compose_url({**row, "job_id": None, "role": ""}, "Hi", gmail=False, first_name="Pat")
    assert "Hello%20from%20Pat" in url


def test_undo_skip_only_for_coach_skips(env):
    s, db = env
    db.update("gh:render:1", status="skipped", error="coach: Not a close fit")
    db.upsert_job({"id": "gh:hooli:1", "ats": "greenhouse", "company": "Hooli", "title": "Data Engineer", "location": "",
                   "url": "u", "apply_url": "u", "description": ""})
    db.update("gh:hooli:1", status="skipped", error="already applied")
    c = _client(s, db)
    c.post("/coach/undo-skip/gh:render:1", follow_redirects=False)
    c.post("/coach/undo-skip/gh:hooli:1", follow_redirects=False)
    assert db.get("gh:render:1")["status"] == "new" and not db.get("gh:render:1")["error"]
    assert db.get("gh:hooli:1")["status"] == "skipped"


def test_contacts_form_takes_email(env):
    s, db = env
    _client(s, db).post("/contacts", data={"action": "add", "name": "Ann Bo", "company": "Acme", "how": "Email",
                                            "email": "ann@acme.com", "link": "https://example.org/ann"}, follow_redirects=False)
    assert any(x["name"] == "Ann Bo" and x["email"] == "ann@acme.com" for x in coach.contacts(db))


def test_not_now_hides_three_days(env):
    from datetime import date, timedelta
    s, db = env
    today = date.today()
    k = _outreach(db, day=today.isoformat())
    assert [r["key"] for r in coachday.rows(db, s, today)] == [k]
    coachday.set_state(db, k, "not_now")
    assert coachday.rows(db, s, today) == []
    db.conn.execute("update outreach set changed = ? where key = ?",
                    ((datetime.now() - timedelta(days=4)).strftime("%Y-%m-%d %H:%M"), k))
    db.conn.commit()
    assert [r["key"] for r in coachday.rows(db, s, today)] == [k]
