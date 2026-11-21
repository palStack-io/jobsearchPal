"""palStack connection (autoapply/adminpal.py): off unless PALSTACK_MANAGER_URL and _SECRET are set."""
import hashlib
import hmac
import time

import pytest
from fastapi.testclient import TestClient
from web_client import client

from autoapply.config import Settings
from autoapply.db import DB
from autoapply.palstack_admin_kit.client import ReportResult
from autoapply.web import make_app

SECRET = "test-secret-not-real"


@pytest.fixture
def web_setup(tmp_path):
    (tmp_path / "data").mkdir()
    db = DB(tmp_path / "data" / "autoapply.db")
    return Settings(root=tmp_path, raw={"data_dir": str(tmp_path / "data")}, profile={"first_name": "Alex"}), db


@pytest.fixture
def connected(monkeypatch):
    monkeypatch.setenv("PALSTACK_MANAGER_URL", "http://adminpal.invalid")
    monkeypatch.setenv("PALSTACK_MANAGER_SECRET", SECRET)
    monkeypatch.delenv("PALSTACK_PRODUCT_ID", raising=False)


def signed(path="/api/admin/stats", secret=SECRET, role="system"):
    ts = str(int(time.time()))
    sig = hmac.new(secret.encode(), f"GET:{path}:{ts}:1:{role}".encode(), hashlib.sha256).hexdigest()
    return {"X-PalStack-Sig": f"jobsearchpal.{sig}", "X-PalStack-Ts": ts, "X-PalStack-Staff-Id": "1",
            "X-PalStack-Staff-Role": role}


def test_off_by_default_no_routes_no_button(web_setup, monkeypatch):
    monkeypatch.delenv("PALSTACK_MANAGER_URL", raising=False)
    monkeypatch.delenv("PALSTACK_MANAGER_SECRET", raising=False)
    s, db = web_setup
    c = client(s, db)
    assert c.post("/api/support/report", json={"text": "x", "consent": True, "platform": "web"}).status_code == 404
    assert "Report a problem" not in c.get("/settings").text
    # signed or not, the stats path is just another page behind the sign-in when not connected
    assert TestClient(make_app(s, db)).get("/api/admin/stats", headers=signed()).status_code == 401


def test_report_signed_in_is_forwarded(web_setup, connected, monkeypatch):
    s, db = web_setup
    c = client(s, db)
    assert "Report a problem" in c.get("/settings").text
    sent = {}

    def fake_send(self, payload):
        sent.update(payload)
        return ReportResult(True, 202, "JS-2K4F", None, {"ref": "JS-2K4F"})

    monkeypatch.setattr("autoapply.palstack_admin_kit.client.AdminClient.send_report", fake_send)
    r = c.post("/api/support/report", json={"text": "The board froze", "consent": True, "platform": "web",
                                            "route": "/settings", "reporter_ref": "someone-else"})
    assert r.status_code == 202 and r.json()["ref"] == "JS-2K4F"
    assert sent["text"] == "The board froze" and sent["reporter_ref"] == "owner"  # never the email, never the body's


def test_report_signed_out_is_refused(web_setup, connected):
    s, db = web_setup
    c = client(s, db)
    out = TestClient(c.app)
    r = out.post("/api/support/report", json={"text": "x", "consent": True, "platform": "web"})
    assert r.status_code == 401


def test_admin_unreachable_says_try_again(web_setup, connected):
    s, db = web_setup
    r = client(s, db).post("/api/support/report", json={"text": "x", "consent": True, "platform": "web"})
    assert r.status_code == 503  # adminpal.invalid does not resolve: the person gets "try again", the app carries on


def test_stats_needs_adminpal_signature(web_setup, connected):
    s, db = web_setup
    client(s, db)  # creates the account
    anon = TestClient(make_app(s, db))
    assert anon.get("/api/admin/stats").status_code == 403
    assert anon.get("/api/admin/stats", headers=signed(secret="wrong")).status_code == 403
    r = anon.get("/api/admin/stats", headers=signed())
    assert r.status_code == 200
    body = r.json()
    assert body["version"] == 2 and body["people"] == 1 and body["active_30d"] == 0 and body["cards"] == []
    # only that one path skips the sign-in
    assert anon.get("/api/admin/users", headers=signed("/api/admin/users")).status_code == 401


def test_stats_active_after_a_submit(web_setup, connected):
    s, db = web_setup
    client(s, db)
    db.conn.execute("insert into jobs (id, url, company, title, status, submitted_at) values "
                    "('gh:acme:1', 'https://x.invalid/1', 'Acme', 'Engineer', 'submitted', datetime('now'))")
    db.conn.commit()
    assert TestClient(make_app(s, db)).get("/api/admin/stats", headers=signed()).json()["active_30d"] == 1


def test_demo_never_connects(web_setup, connected, monkeypatch):
    from autoapply import demo
    monkeypatch.setattr(demo, "ON", True)
    s, db = web_setup
    assert TestClient(make_app(s, db)).post("/api/support/report", json={}).status_code in (403, 404)
