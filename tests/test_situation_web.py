"""Your next step in the app's pages (2026-11-24): welcome step 0, the questions, Settings > Something changed? and the
confirm screen."""
import json
from pathlib import Path

import pytest
import yaml

from autoapply import next_steps
from autoapply.config import Settings
from autoapply.db import DB

FIX = Path(__file__).parent / "fixtures" / "next_steps"


def _make(tmp_path, ws):
    (tmp_path / "materials").mkdir(exist_ok=True)
    (tmp_path / "config.yaml").write_text(yaml.safe_dump({"data_dir": "data", "search": {"title_keywords": []}, "work_search": ws}))
    raw = yaml.safe_load((tmp_path / "config.yaml").read_text()) | {"data_dir": str(tmp_path / "data")}
    s = Settings(root=tmp_path, raw=raw, profile={})
    db = DB(s.data_dir / "autoapply.db")
    from web_client import client
    return s, db, client(s, db)


@pytest.fixture
def fresh(tmp_path):
    return _make(tmp_path, {"label": "Work search", "weekly_minimum": 0})


@pytest.fixture
def existing(tmp_path):
    return _make(tmp_path, {"label": "DUA", "weekly_minimum": 0, "claiming": False})


def _cfg(s):
    return yaml.safe_load((s.root / "config.yaml").read_text())["work_search"]


def _tasks(db):
    from autoapply import benefits
    benefits.ensure(db)
    return [dict(r) for r in db.conn.execute("select * from ui_tasks")]


LAID = {"mode": "welcome", "door": "laid", "last_day": "2026-11-24", "state": "MA", "coverage": "end_of_month",
        "age": "40_54", "size": "100p", "has": ["severance", "401k"]}


def test_every_step_has_skip(fresh):
    s, db, c = fresh
    for n in range(0, 6):
        html = c.get(f"/welcome?step={n}").text
        assert "Skip" in html or "Continue" in html and n == 4, n


def test_fresh_layoff_goes_straight_to_the_list_and_prefills_about_you(fresh):
    s, db, c = fresh
    r = c.post("/situation/answers", data=LAID, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/welcome?step=1&msg=")
    assert _cfg(s)["claiming"] is True and _cfg(s)["state"] == "MA"
    assert any(t["title"] == "File for unemployment" and t["kind"] == "next_step" for t in _tasks(db))
    html = c.get("/welcome?step=2").text
    assert '<option value="MA" selected' in html


def test_just_looking_sets_claiming_false_and_adds_no_steps(fresh):
    s, db, c = fresh
    r = c.post("/situation/start", data={"mode": "welcome", "door": "looking"}, follow_redirects=False)
    assert r.headers["location"].startswith("/welcome?step=1")
    assert _cfg(s)["claiming"] is False and _tasks(db) == []


def test_layoff_without_state_asks_for_it(fresh):
    s, db, c = fresh
    html = c.post("/situation/answers", data={**LAID, "state": ""}).text
    assert "Pick the state you worked in" in html and _tasks(db) == []


def test_plan_code_fills_the_questions(fresh):
    s, db, c = fresh
    a = json.loads((FIX / "laid_off_today_ma.json").read_text())["answers"]
    html = c.post("/situation/start", data={"mode": "welcome", "door": "laid", "code": next_steps.encode_code(a)}).text
    assert "filled in below" in html and '<option value="MA" selected' in html and _tasks(db) == []


def test_bad_code_writes_nothing(fresh):
    s, db, c = fresh
    before = (s.root / "config.yaml").read_bytes()
    r = c.post("/situation/start", data={"mode": "welcome", "door": "laid", "code": "NS1.garbage!!"}, follow_redirects=False)
    assert r.status_code == 303 and "err=" in r.headers["location"]
    assert (s.root / "config.yaml").read_bytes() == before and _tasks(db) == []


def test_existing_user_sees_confirm_before_anything_changes(existing):
    s, db, c = existing
    before = (s.root / "config.yaml").read_bytes()
    html = c.post("/situation/answers", data={**LAID, "mode": "settings"}).text
    assert "Turn on unemployment for Massachusetts" in html and "Confirm these changes" in html
    assert (s.root / "config.yaml").read_bytes() == before and _tasks(db) == []
    code = html.split('name="code" value="')[1].split('"')[0]
    r = c.post("/situation/confirm", data={"code": code, "door": "laid"}, follow_redirects=False)
    assert r.status_code == 303 and _cfg(s)["claiming"] is True and _tasks(db)


def test_working_again_clears_unfinished_steps(existing):
    s, db, c = existing
    c.post("/situation/confirm", data={"code": c.post("/situation/answers", data={**LAID, "mode": "settings"}).text
                                       .split('name="code" value="')[1].split('"')[0], "door": "laid"})
    html = c.post("/situation/start", data={"mode": "settings", "door": "working"}).text
    assert "Confirm these changes" in html
    c.post("/situation/confirm", data={"door": "working"})
    assert _cfg(s)["claiming"] is False and _tasks(db) == []


def test_settings_has_the_entry(existing):
    s, db, c = existing
    assert 'href="/situation"' in c.get("/settings").text


def test_done_lists_first_steps(fresh):
    s, db, c = fresh
    c.post("/situation/answers", data=LAID)
    html = c.get("/welcome?step=done").text
    assert "First on your list" in html and "File for unemployment" in html
