"""Finish it here, through the dashboard: the routes, the viewer page and what it shows in each state.
A fake session object stands in for the browser box (sessions themselves: test_finish_here.py)."""
import json
import re

import pytest

from autoapply.config import Settings
from autoapply.db import DB
from web_client import client

from test_fillers import PROFILE

ON = {"on_server": True, "host": "127.0.0.1", "cdp_port": 9, "vnc_port": 8}


class FakeSessions:
    def __init__(self, state="ready", job_id="manual:gh"):
        self.st = {"job_id": job_id, "state": state, "unanswered": ["Why here?"], "error": "", "started_at": 0, "last_input": 0}
        self.started, self.pasted, self.closed, self.busy = [], [], 0, False

    def status(self):
        return dict(self.st)

    def start(self, job_id, plan, profile, mark_sent):
        if self.busy:
            return {"busy": self.st["job_id"]}
        self.started.append((job_id, plan, mark_sent))
        self.st.update(job_id=job_id, state="starting")
        return {"started": True}

    def paste(self, text):
        self.pasted.append(text)
        return True

    def close(self):
        self.closed += 1

    def touch(self):
        pass


def _setup(tmp_path, finish=None, signed_in=True):
    raw = {"data_dir": str(tmp_path / "data")}
    if finish:
        raw["finish"] = finish
    s = Settings(root=tmp_path, raw=raw, profile=PROFILE)
    db = DB(s.data_dir / "t.db")
    db.upsert_job({"id": "manual:gh", "ats": "greenhouse", "company": "Acme", "title": "AE", "location": "",
                   "url": "https://example.com/gh", "apply_url": "https://example.com/gh", "description": ""})
    db.update("manual:gh", status="filled", plan_json={"url": "https://example.com/gh", "ats": "greenhouse", "files": {}},
              report_json={"errors": [{"label": "(submit)", "error": "captcha"}]})
    c = client(s, db, signed_in=signed_in)
    fake = FakeSessions()
    c.app.state.finish_sessions = fake
    return db, c, fake


def test_routes_404_when_off(tmp_path):
    _, c, _ = _setup(tmp_path)
    assert c.get("/finish-here/manual:gh").status_code == 404
    assert c.post("/finish-here/manual:gh", follow_redirects=False).status_code == 404
    assert c.get("/finish-here/status").status_code == 404


def test_signed_out_viewer_redirects_to_login(tmp_path):
    from web_client import EMAIL, PASSWORD  # noqa: F401
    from autoapply import auth
    _, c, _ = _setup(tmp_path, ON, signed_in=True)
    c.post("/logout")
    r = c.get("/finish-here/manual:gh", headers={"accept": "text/html"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/login")
    assert auth  # signed-out requests never reach the route


def test_start_redirects_to_viewer(tmp_path):
    db, c, fake = _setup(tmp_path, ON)
    r = c.post("/finish-here/manual:gh", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/finish-here/manual:gh"
    job_id, plan, mark_sent = fake.started[0]
    assert job_id == "manual:gh" and plan["url"] == "https://example.com/gh" and plan["approved_by_user"] is False
    from autoapply import finish
    assert finish.ran(db, "manual:gh")  # counted as a run, as /bundle does


def test_busy_redirects_to_running_job(tmp_path):
    _, c, fake = _setup(tmp_path, ON)
    fake.busy, fake.st["job_id"] = True, "other:job"
    r = c.post("/finish-here/manual:gh", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/finish-here/other:job?busy=1"


def test_status_json_shape(tmp_path):
    _, c, _ = _setup(tmp_path, ON)
    st = c.get("/finish-here/status").json()
    assert {"job_id", "state", "unanswered", "error"} <= set(st)


def test_paste_forwards_text(tmp_path):
    _, c, fake = _setup(tmp_path, ON)
    assert c.post("/finish-here/paste", data={"text": "481516"}).json() == {"ok": True}
    assert fake.pasted == ["481516"]


def test_close_goes_back_to_the_job(tmp_path):
    _, c, fake = _setup(tmp_path, ON)
    r = c.post("/finish-here/close", follow_redirects=False)
    assert fake.closed == 1 and r.status_code == 303 and r.headers["location"] == "/job/manual:gh"


def test_mark_sent_marks_the_job(tmp_path):
    db, c, fake = _setup(tmp_path, ON)
    c.post("/finish-here/manual:gh", follow_redirects=False)
    _, _, mark_sent = fake.started[0]
    assert mark_sent("manual:gh", "Thank you for applying") == {"ok": True}
    assert db.get("manual:gh")["status"] == "submitted"


def test_finished_endpoint_still_marks_sent(tmp_path):
    db, c, _ = _setup(tmp_path)
    assert c.post("/finished/manual:gh", json={"confirmation": "Thanks"}).json() == {"ok": True}
    assert db.get("manual:gh")["status"] == "submitted"


@pytest.mark.parametrize("state", ["starting", "filling", "ready", "sent", "closed", "error"])
def test_viewer_has_one_primary_action_per_state(tmp_path, state):
    _, c, fake = _setup(tmp_path, ON)
    fake.st.update(state=state, error="We lost the browser. Try again in a moment." if state == "error" else "")
    html = c.get("/finish-here/manual:gh").text
    card = html[html.index('<section class="card mt fh"'):]
    card = card[:card.index("</section>")]  # base.html's hidden dialogs carry primaries of their own
    assert len(re.findall(r'class="btn primary', card)) == 1, state
    assert ("/static/novnc/core/rfb.js" in html) == (state in ("starting", "filling", "ready", "sent"))


def test_viewer_busy_offers_to_close(tmp_path):
    _, c, fake = _setup(tmp_path, ON)
    html = c.get("/finish-here/manual:gh?busy=1").text  # where a busy start sends you: the running job
    assert "Another application is open in the browser." in html and "Close it" in html


def test_novnc_is_served(tmp_path):
    _, c, _ = _setup(tmp_path, ON)
    r = c.get("/static/novnc/core/rfb.js")
    assert r.status_code == 200 and "javascript" in r.headers["content-type"]


# ---- Task 7: where you find it: the job page, the morning brief, Setup and Settings ----

def _health(monkeypatch, ok=True, version="1.1.0"):
    from autoapply import finish_here
    monkeypatch.setattr(finish_here, "health", lambda box, timeout=2.0, with_version=True: {
        "ok": ok, "version": version if ok else "", "error": "" if ok else "refused"})


def test_job_card_offers_finish_here_when_healthy(tmp_path, monkeypatch):
    _health(monkeypatch)
    _, c, _ = _setup(tmp_path, ON)
    html = c.get("/job/manual:gh").text
    card = html[html.index('id="finish"'):]
    assert 'action="/finish-here/manual%3Agh"' in card and "Finish it here" in card and "Or on your Mac" in card
    assert card.index("Finish it here") < card.index("autoapply-finish")


def test_job_card_unchanged_when_off(tmp_path):
    _, c, _ = _setup(tmp_path)
    html = c.get("/job/manual:gh").text
    assert "On your Mac, open Terminal and run:" in html and "/finish-here/" not in html


def test_job_card_box_down_line(tmp_path, monkeypatch):
    _health(monkeypatch, ok=False)
    _, c, _ = _setup(tmp_path, ON)
    html = c.get("/job/manual:gh").text
    assert "The browser on your server isn't answering, so use your Mac for now." in html
    assert 'action="/finish-here/' not in html


def test_brief_ready_line_on_and_off(tmp_path):
    from autoapply import brief
    db, _, _ = _setup(tmp_path)
    off = Settings(root=tmp_path, raw={"data_dir": str(tmp_path / "data")}, profile=PROFILE)
    on = Settings(root=tmp_path, raw={"data_dir": str(tmp_path / "data"), "finish": ON}, profile=PROFILE)
    _, _, html_off = brief.compose(db, "https://jobs.example", settings=off)
    _, _, html_on = brief.compose(db, "https://jobs.example", settings=on)
    assert "autoapply-finish manual:gh" in html_off and "Finish it here" not in html_off
    assert "https://jobs.example/job/manual:gh#finish" in html_on and "Finish it here" in html_on
    assert "autoapply-finish" not in html_on


def test_setup_check_turns_it_on(tmp_path, monkeypatch):
    import yaml
    _health(monkeypatch)
    (tmp_path / "config.yaml").write_text("search: {title_keywords: [account executive]}\n")
    _, c, _ = _setup(tmp_path)
    r = c.post("/setup/finish", follow_redirects=False)
    assert r.status_code == 303 and "#finish" in r.headers["location"]
    cfg = yaml.safe_load((tmp_path / "config.yaml").read_text())
    assert cfg["finish"]["on_server"] is True and cfg["finish"]["host"] == "127.0.0.1"
    assert cfg["search"]["title_keywords"] == ["account executive"]
    assert list(tmp_path.glob("config.yaml.bak-*"))


def test_setup_check_box_down_changes_nothing(tmp_path, monkeypatch):
    _health(monkeypatch, ok=False)
    (tmp_path / "config.yaml").write_text("search: {}\n")
    _, c, _ = _setup(tmp_path)
    r = c.post("/setup/finish", follow_redirects=False)
    assert "err=" in r.headers["location"] and (tmp_path / "config.yaml").read_text() == "search: {}\n"


def test_setup_shows_the_command_for_this_install(tmp_path):
    _, c, _ = _setup(tmp_path)
    html = c.get("/setup").text
    assert "Finish applications in the browser" in html
    assert "docker compose -f docker-compose.finish.yml up -d" in html  # tests run outside Docker


def test_settings_version_mismatch_line(tmp_path, monkeypatch):
    _health(monkeypatch, version="0.9.0")
    _, c, _ = _setup(tmp_path, ON)
    html = c.get("/settings").text
    assert "Browser on your server" in html and "0.9.0" in html and "1.1.0" in html and "pull" in html


def test_settings_running_line(tmp_path, monkeypatch):
    _health(monkeypatch, version="1.1.0")
    _, c, _ = _setup(tmp_path, ON)
    assert "running (version 1.1.0)" in c.get("/settings").text


def test_finish_here_button_is_not_in_a_nested_form(tmp_path, monkeypatch):
    """The finish card sits inside the job page's main form, and browsers drop a <form> nested in another one, so the
    button would submit the job form instead. Found driving it end to end (2026-11-17)."""
    from html.parser import HTMLParser
    _health(monkeypatch)
    _, c, _ = _setup(tmp_path, ON)
    html = c.get("/job/manual:gh").text

    class P(HTMLParser):
        depth, nested, owner = 0, [], None
        def handle_starttag(self, tag, attrs):
            a = dict(attrs)
            if tag == "form":
                if self.depth:
                    self.nested.append(a.get("action"))
                self.depth += 1
            if tag == "button" and "data-finish-here" in a:
                self.owner = (a.get("form"), self.depth)
        def handle_endtag(self, tag):
            if tag == "form":
                self.depth -= 1
    p = P()
    p.feed(html)
    assert not [n for n in p.nested if n and "finish-here" in n]
    assert p.owner is not None, "Finish it here button not found"
    form_id, depth = p.owner
    assert form_id and f'<form id="{form_id}" method="post" action="/finish-here/manual%3Agh"' in html


def test_same_job_again_reopens_its_view(tmp_path):
    """Pressing Finish it here on the job that is already open goes back to its view, not to 'another application'."""
    _, c, fake = _setup(tmp_path, ON)
    fake.busy, fake.st["job_id"] = True, "manual:gh"
    r = c.post("/finish-here/manual:gh", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/finish-here/manual:gh"


# ---- final review fixes (2026-11-17) ----

def test_setup_check_keeps_an_open_session(tmp_path, monkeypatch):
    """Checking again in Setup while a form is open must not orphan that session (a second browser could open)."""
    _health(monkeypatch)
    (tmp_path / "config.yaml").write_text("finish: {on_server: true, host: 127.0.0.1, cdp_port: 9222, vnc_port: 5900}\n")
    _, c, fake = _setup(tmp_path, {"on_server": True, "host": "127.0.0.1", "cdp_port": 9222, "vnc_port": 5900})
    fake.st["state"] = "ready"
    c.post("/setup/finish", follow_redirects=False)
    assert c.app.state.finish_sessions is fake


def test_viewer_reconnects_after_a_drop(tmp_path):
    """A phone sleeping or a Wi-Fi blip drops the picture; the viewer must connect again while the form is open."""
    _, c, _ = _setup(tmp_path, ON)
    js = c.get("/finish-here/manual:gh").text
    handler = js[js.index('addEventListener("disconnect"'):]
    handler = handler[:handler.index("});") + 3]
    assert "rfb = null" in handler
    assert 'if (!rfb && (st.state === "ready" || st.state === "sent")) connect();' in js


def test_closed_does_not_claim_nothing_was_sent(tmp_path):
    """Done or the idle close without a recognised confirmation: you may still have pressed Submit."""
    _, c, fake = _setup(tmp_path, ON)
    fake.st["state"] = "closed"
    html = c.get("/finish-here/manual:gh").text
    assert "Nothing was sent from here" not in html
    assert "Closed. We didn't see a confirmation page. If you pressed Submit, mark it sent on the job page." in html
