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


# ---- Task 9: the card, the to-do order, the brief, the setting ----------------------------------

def _coach_row(db, day=None):
    from datetime import date
    day = day or date.today()
    db.conn.execute("insert into coach_day (day, json) values (?, ?)", (day.isoformat(), json.dumps({
        "note": ["Priya Rao wrote back on Thu 12 Nov. Reply to her first.", "Your screens were analytics roles."],
        "picks": [{"id": "gh:render:1", "why": "Close to your Scribd screen", "angle": "your dbt work"}],
        "skips": [{"id": "gh:hooli:9", "reason": "On-site in New York", "company": "Hooli", "title": "Data Engineer"}],
        "outreach": [], "followups": ["b", "a"], "prep": [{"job": "gh:render:1", "items": ["Your dbt migration story"]}],
        "check": ["Render lists Remote; confirm the time zone."]})))
    db.conn.commit()


def test_card_renders_note_people_and_undo(env):
    from datetime import date
    s, db = env
    _coach_row(db)
    db.upsert_job({"id": "gh:hooli:9", "ats": "greenhouse", "company": "Hooli", "title": "Data Engineer", "location": "",
                   "url": "u", "apply_url": "u", "description": ""})
    db.update("gh:hooli:9", status="skipped", error="coach: On-site in New York")
    _outreach(db, day=date.today().isoformat())
    html = _client(s, db).get("/coach").text
    assert 'id="coach"' in html
    assert "Priya Rao wrote back on Thu 12 Nov." in html
    assert "Dana Wu" in html and ">Send<" in html and "add their address" in html
    assert 'action="/coach/undo-skip/gh%3Ahooli%3A9"' in html
    _client(s, db).post("/coach/undo-skip/gh%3Ahooli%3A9", follow_redirects=False)
    assert db.get("gh:hooli:9")["status"] == "new"
    assert "Your dbt migration story" in html and "confirm the time zone" in html
    assert html.count("btn sm primary") >= 1


def test_no_card_without_row(env):
    s, db = env
    assert 'id="coach"' not in _client(s, db).get("/coach").text


def test_todo_ordered_by_followups():
    todo = [{"key": "a"}, {"key": "c"}, {"key": "b"}]
    assert [n["key"] for n in coachday.order_todo(todo, ["b", "a"])] == ["b", "a", "c"]
    assert coachday.order_todo(todo, []) == todo


def test_brief_opens_with_coach_note(env):
    from datetime import date
    from autoapply import brief
    s, db = env
    _coach_row(db)
    subject, text, html = brief.compose(db, "http://x", today=date.today(), data_dir=s.data_dir, settings=s)
    assert "Priya Rao wrote back on Thu 12 Nov." in text
    assert text.index("Priya Rao wrote back") < text.index("READY FOR YOU")
    assert "Priya Rao wrote back" in html


def test_settings_toggle_saves(env):
    s, db = env
    c = _client(s, db)
    c.post("/settings", data={"coach_form": "1", "coach": "1", "coach_model": "opus", "drafts_model": "haiku"}, follow_redirects=False)
    p = Prefs(s.data_dir, s.raw)
    assert p.data["coach"] is True and p.data["coach_model"] == "opus" and p.data["drafts_model"] == "haiku"
    c.post("/settings", data={"coach_form": "1", "coach_model": "nope"}, follow_redirects=False)
    p = Prefs(s.data_dir, s.raw)
    assert p.data["coach"] is False and p.data["coach_model"] == "opus"



# ---- Task 13: Your coach page, the nav, a lighter dashboard --------------------------------------

def test_plan_redirects_to_coach(env):
    s, db = env
    r = _client(s, db).get("/plan?planned=1", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/coach?planned=1"


def test_coach_page_shows_note_today_and_later(env):
    from datetime import date
    s, db = env
    _coach_row(db)
    row = json.loads(db.conn.execute("select json from coach_day").fetchone()[0])
    row["later"] = [{"day": "Wed", "text": "Thank Priya Rao after your screen."}]
    db.conn.execute("update coach_day set json = ?", (json.dumps(row),))
    db.conn.commit()
    _outreach(db, day=date.today().isoformat())
    html = _client(s, db).get("/coach").text
    assert "<h1>Your coach</h1>" in html and 'aria-current="page"' in html
    assert html.index("Priya Rao wrote back") < html.index(">Today<") < html.index("Later this week")
    assert "Thank Priya Rao after your screen." in html and "Dana Wu" in html
    assert "Your coach</span>" in html  # the side nav label


def test_coach_page_offers_suggested_week_when_no_plan(env):
    s, db = env
    _coach_row(db)
    row = json.loads(db.conn.execute("select json from coach_day").fetchone()[0])
    row["week"] = {"apps": 4, "reach": 120, "prep": 60, "learn": 0, "why": "Your screens came after reaching people."}
    db.conn.execute("update coach_day set json = ?", (json.dumps(row),))
    db.conn.commit()
    html = _client(s, db).get("/coach").text
    assert "Here's a week that fits your search" in html and "Your screens came after reaching people." in html
    import re
    assert re.search(r'name="apps"[^>]*value="4"', html)
    assert "Use this plan" in html


def test_dashboard_has_no_coach_card_but_panel_line(env):
    s, db = env
    _coach_row(db)
    html = _client(s, db).get("/").text
    assert 'id="coach"' not in html
    assert "Priya Rao wrote back on Thu 12 Nov." in html and 'href="/coach"' in html and "Open your coach" in html
