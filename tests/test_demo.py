"""Public demo mode: no sign-in, nothing that reaches outside the demo, size and rate caps."""
import pytest
from fastapi.testclient import TestClient

from autoapply import auth, demo, web
from autoapply.config import Settings
from autoapply.db import DB
from autoapply.web import make_app

HTML = {"accept": "text/html"}


@pytest.fixture
def demo_app(tmp_path, monkeypatch):
    monkeypatch.setattr(demo, "ON", True)
    s = Settings(root=tmp_path, raw={"data_dir": str(tmp_path / "data")}, profile={})
    db = DB(s.data_dir / "t.db")
    db.upsert_job({"id": "manual:gh", "ats": "greenhouse", "company": "Acme", "title": "AE", "location": "",
                   "url": "u", "apply_url": "u", "description": ""})
    db.update("manual:gh", status="filled", plan_json={"url": "u", "answers": {}}, report_json={})
    auth.save(s.data_dir, "demo@example.org", "demo password")
    return s, db


def test_no_sign_in_and_banner(demo_app):
    c = TestClient(make_app(*demo_app))
    r = c.get("/", headers=HTML)
    assert r.status_code == 200 and "Demo with fictional data" in r.text
    assert 'action="/sync"' not in r.text and 'action="/logout"' not in r.text
    for p in ("/login", "/logout", "/account"):
        assert c.get(p, follow_redirects=False).headers["location"] == "/"


@pytest.mark.parametrize("path", ["/run", "/prepare/manual:gh", "/sync", "/settings", "/setup/claude-token",
                                  "/setup/codex-login", "/setup/gmail", "/setup/resume", "/letters/examples",
                                  "/letter/manual:gh", "/leads/apply", "/lead/x/run", "/finished/manual:gh",
                                  "/applications/import", "/login", "/nope"])
def test_outside_posts_refused(demo_app, path, monkeypatch):
    started = []
    import subprocess
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: started.append(a))
    c = TestClient(make_app(*demo_app))
    r = c.post(path, data={"key": "x"}, follow_redirects=False)
    assert r.status_code in (303, 403), (path, r.status_code)
    assert not started


def test_local_posts_work(demo_app):
    c = TestClient(make_app(*demo_app))
    r = c.post("/applications/log", data={"company": "Brightline", "title": "Data Engineer"}, follow_redirects=False)
    assert r.status_code == 303
    assert c.post("/dua/task", data={"action": "add", "title": "Call the office"}, follow_redirects=False).status_code == 303


def test_job_page_browser_actions_refused(demo_app, monkeypatch):
    monkeypatch.setattr(web, "fill", lambda *a, **k: (_ for _ in ()).throw(AssertionError("browser opened")))
    monkeypatch.setattr(web, "submit", lambda *a, **k: (_ for _ in ()).throw(AssertionError("browser opened")))
    c = TestClient(make_app(*demo_app))
    for action in ("refill", "submit"):
        assert c.post("/job/manual:gh", data={"action": action, "approve": "yes"}).status_code == 403
    assert c.post("/job/manual:gh", data={"action": "manual"}, follow_redirects=False).status_code == 303


def test_pasted_link_not_fetched(demo_app, monkeypatch):
    from autoapply import paste
    monkeypatch.setattr(paste, "from_link", lambda u: (_ for _ in ()).throw(AssertionError("fetched")))
    c = TestClient(make_app(*demo_app))
    assert c.post("/leads/paste", data={"text": "https://example.com/job/1"}).status_code == 200


def test_size_and_rate_caps(demo_app):
    c = TestClient(make_app(*demo_app))
    assert c.post("/leads/paste", data={"text": "x" * (demo.MAX_BODY + 10)}).status_code == 413
    codes = [c.post("/pal/seen", data={"key": "pal:x"}, follow_redirects=False).status_code
             for _ in range(demo.POSTS_PER_MINUTE + 3)]
    assert codes.count(429) >= 2


def test_start_run_blocked_even_if_route_allowed(demo_app, monkeypatch):
    import subprocess
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("agent started")))
    monkeypatch.setattr(demo, "post_allowed", lambda p: True)  # pretend the allowlist slipped
    c = TestClient(make_app(*demo_app))
    assert c.post("/run", follow_redirects=False).status_code == 303


def test_off_when_env_unset():
    assert demo.post_allowed("/plan") and not demo.post_allowed("/lead/a/run") and not demo.post_allowed("/run")


def test_back_link_is_escaped_and_same_site():
    page = demo.off_page('/job/x"><script>alert(1)</script>')
    assert "<script>" not in page and '"><' not in page.split("<h1")[1]
    assert demo.back_from_referer("https://evil.example/phish", "jobsdemo.palstack.io") == "/"
    assert demo.back_from_referer("https://jobsdemo.palstack.io/leads?x=1", "jobsdemo.palstack.io") == "/leads"
    assert 'href="/"' in demo.off_page("//evil.example")


def test_in_page_posts_get_a_dialog_message(demo_app):
    c = TestClient(make_app(*demo_app))
    r = c.post("/run", headers={"X-In-Page": "1"})
    assert r.status_code == 403 and r.json()["title"] == demo.OFF_TITLE
    r = c.post("/job/manual:gh", data={"action": "refill"}, headers={"X-In-Page": "1"})
    assert r.status_code == 403 and "demo" in r.json()["title"]
