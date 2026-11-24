"""The welcome flow (2026-11-22): a stranger's first run, one short step at a time, reusing the Setup routes, with
unemployment opt-in."""
from datetime import date

import pytest
import yaml

from autoapply import setup
from autoapply.config import Settings
from autoapply.db import DB


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.delenv("GMAIL_USER", raising=False)
    monkeypatch.delenv("GMAIL_APP_PASSWORD", raising=False)
    (tmp_path / "materials").mkdir()
    (tmp_path / "config.yaml").write_text(yaml.safe_dump({"data_dir": "data", "search": {"title_keywords": []},
                                                           "work_search": {"label": "Work search", "weekly_minimum": 0}}))
    raw = yaml.safe_load((tmp_path / "config.yaml").read_text()) | {"data_dir": str(tmp_path / "data")}
    s = Settings(root=tmp_path, raw=raw, profile={})
    db = DB(s.data_dir / "autoapply.db")
    from web_client import client
    return s, db, client(s, db)


def _cfg(s):
    return yaml.safe_load((s.root / "config.yaml").read_text())


def test_fresh_install_opens_welcome(env):
    s, db, c = env
    s.raw["_from_example"] = True
    r = c.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/welcome?step=0"  # your situation first (2026-11-24)


@pytest.mark.parametrize("n", [0, 1, 2, 3, 4, 5])
def test_each_step_renders(env, n):
    s, db, c = env
    html = c.get(f"/welcome?step={n}").text
    assert f"Step {n + 1} of 6" in html  # six steps since Your situation came first (2026-11-24)


def test_done_screen_offers_run_now(env):
    s, db, c = env
    html = c.get("/welcome?step=done").text
    assert "Almost there" in html and "things are still missing" in html and 'action="/run"' in html


def test_setup_routes_go_on_to_the_next_step(env):
    s, db, c = env
    r = c.post("/setup/interests", data={"titles": "data analyst", "us_only": "1", "wizard": "3"}, follow_redirects=False)
    assert r.headers["location"].startswith("/welcome?step=4")
    r = c.post("/setup/interests", data={"titles": "", "wizard": "3"}, follow_redirects=False)
    assert r.headers["location"].startswith("/welcome?step=3") and "err=" in r.headers["location"]


def test_a_wizard_value_cannot_redirect_elsewhere(env):
    s, db, c = env
    r = c.post("/setup/interests", data={"titles": "data analyst", "wizard": "//evil.example"}, follow_redirects=False)
    assert r.headers["location"].startswith("/setup")


def test_not_claiming_turns_the_benefits_side_off(env):
    s, db, c = env
    r = c.post("/welcome/about", data={"first_name": "Sam", "last_name": "Stranger", "email": "sam@example.org",
                                       "claiming": "no"}, follow_redirects=False)
    assert r.headers["location"].startswith("/welcome?step=3")
    ws = _cfg(s)["work_search"]
    assert ws["claiming"] is False and ws["weekly_minimum"] == 0
    html = c.get("/").text
    assert 'href="/dua"' not in html


def test_claiming_needs_a_state_and_takes_its_rule(env):
    s, db, c = env
    r = c.post("/welcome/about", data={"first_name": "Sam", "last_name": "Stranger", "email": "sam@example.org",
                                       "claiming": "yes", "state": ""}, follow_redirects=False)
    assert r.headers["location"].startswith("/welcome?step=2") and "err=" in r.headers["location"]
    c.post("/welcome/about", data={"first_name": "Sam", "last_name": "Stranger", "email": "sam@example.org",
                                   "claiming": "yes", "state": "MA"}, follow_redirects=False)
    ws = _cfg(s)["work_search"]
    from autoapply.web import state_rules
    want = state_rules()["MA"].get("weekly_minimum") or 0
    assert ws["claiming"] is True and ws["state"] == "MA" and ws["weekly_minimum"] == want


def test_brief_subject_leaves_out_the_count_when_not_claiming(env):
    from autoapply import brief
    s, db, c = env
    c.post("/welcome/about", data={"first_name": "Sam", "last_name": "Stranger", "email": "sam@example.org",
                                   "claiming": "no"}, follow_redirects=False)
    subject, text, html = brief.compose(db, "http://x", today=date.today(), data_dir=s.data_dir, settings=s,
                                        label="Work search", floor=0)
    assert "Work search 0/0" not in subject and "0/0" not in subject


def test_ai_step_puts_a_waiting_resume_in_shape(env, monkeypatch):
    from autoapply import resume_rebuild
    s, db, c = env
    ran = []
    # Signed in, stated here rather than read from this machine (CI has no AI; the step never calls one that isn't).
    monkeypatch.setattr(setup, "agent_ready", lambda prefs=None: {"done": True, "note": "", "who": ["Claude Code"], "claude": True, "codex": False})
    monkeypatch.setattr(resume_rebuild, "run_waiting", lambda settings, ask=None: ran.append(1) or "We rebuilt your resume.")
    r = c.post("/welcome/ai-done", follow_redirects=False)
    assert ran and r.headers["location"].startswith("/welcome?step=5")


def test_roles_step_keeps_job_sources(env):
    s, db, c = env
    cfg = _cfg(s)
    cfg["feeds"] = ["remoteok", "remotive"]
    cfg["boards"] = [{"ats": "greenhouse", "slug": "acme"}]
    (s.root / "config.yaml").write_text(yaml.safe_dump(cfg))
    s.raw.update(cfg)
    r = c.post("/welcome/roles", data={"titles": "data analyst, analytics engineer", "us_only": "1"}, follow_redirects=False)
    assert r.headers["location"].startswith("/welcome?step=4")
    after = _cfg(s)
    assert after["search"]["title_keywords"] == ["data analyst", "analytics engineer"]
    assert after["feeds"] == ["remoteok", "remotive"] and after["boards"] == [{"ats": "greenhouse", "slug": "acme"}]


def test_ai_step_without_an_ai_says_so_and_never_calls_one(env, monkeypatch):
    from autoapply import resume_rebuild
    s, db, c = env
    monkeypatch.setattr(setup, "agent_ready", lambda prefs=None: {"done": False, "note": "", "who": [], "claude": True, "codex": False})
    monkeypatch.setattr(resume_rebuild, "run_waiting", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no AI call")))
    r = c.post("/welcome/ai-done", follow_redirects=False)
    assert r.headers["location"].startswith("/welcome?step=4") and "err=" in r.headers["location"]
    assert "Skip for now" in c.get("/welcome?step=4").text


def test_an_existing_claimer_is_not_switched_off_by_default(env):
    s, db, c = env
    cfg = _cfg(s)
    cfg["work_search"] = {"label": "DUA", "weekly_minimum": 3, "state": "MA"}  # an install from before the question
    (s.root / "config.yaml").write_text(yaml.safe_dump(cfg))
    s.raw.update(cfg)
    html = c.get("/welcome?step=2").text
    yes = html[html.index('value="yes"') - 60:html.index('value="yes"') + 40]
    no = html[html.index('value="no"') - 60:html.index('value="no"') + 40]
    assert "checked" in yes and "checked" not in no
