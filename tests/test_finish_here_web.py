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
