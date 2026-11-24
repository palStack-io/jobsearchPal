"""The quiet "Something changed?" line (2026-11-24) and the plan's progress on Stats, local only: nothing is sent
anywhere. The line shows only on a sign (an agency email while not claiming, or three quiet weeks after applying),
once per sign, and Not now puts it away."""
import json
from datetime import date, timedelta
from pathlib import Path

import pytest
import yaml

from autoapply import next_steps
from autoapply.config import Settings
from autoapply.db import DB

TODAY = date(2026, 11, 24)
FIX = Path(__file__).parent / "fixtures" / "next_steps"


@pytest.fixture
def make(tmp_path):
    def _m(ws):
        (tmp_path / "materials").mkdir(exist_ok=True)
        (tmp_path / "config.yaml").write_text(yaml.safe_dump({"data_dir": "data", "search": {"title_keywords": []}, "work_search": ws}))
        raw = yaml.safe_load((tmp_path / "config.yaml").read_text()) | {"data_dir": str(tmp_path / "data")}
        s = Settings(root=tmp_path, raw=raw, profile={})
        return s, DB(s.data_dir / "autoapply.db")
    return _m


def _sent(db, i, day):
    db.upsert_job({"id": f"gh:a:{i}", "ats": "greenhouse", "company": "Acme", "title": "Analyst", "location": "",
                   "url": "u", "apply_url": "u", "description": ""})
    db.update(f"gh:a:{i}", status="submitted", submitted_at=f"{day.isoformat()} 10:00")


def _agency(db, day):
    db.add_event({"id": "<agency-1@mass.gov>", "job_id": None, "kind": "agency", "at": f"{day.isoformat()} 09:00",
                  "sender": "DUA", "subject": "Your claim"})


def test_no_sign_on_a_fresh_or_busy_account(make):
    s, db = make({"label": "Work search", "weekly_minimum": 0, "claiming": False})
    assert next_steps.sign(settings=s, db=db, today=TODAY) is None
    _sent(db, 1, TODAY - timedelta(days=3))
    assert next_steps.sign(settings=s, db=db, today=TODAY) is None


def test_agency_email_while_not_claiming(make):
    s, db = make({"label": "Work search", "weekly_minimum": 0, "claiming": False})
    _agency(db, TODAY - timedelta(days=2))
    sg = next_steps.sign(settings=s, db=db, today=TODAY)
    assert sg and sg["key"].startswith("sit:agency:") and "unemployment agency" in sg["line"]
    db.set_nudge(sg["key"], "done")
    assert next_steps.sign(settings=s, db=db, today=TODAY) is None


def test_agency_email_while_claiming_is_no_sign(make):
    s, db = make({"label": "DUA", "weekly_minimum": 3, "claiming": True, "state": "MA"})
    _agency(db, TODAY - timedelta(days=2))
    assert next_steps.sign(settings=s, db=db, today=TODAY) is None


def test_three_quiet_weeks(make):
    s, db = make({"label": "Work search", "weekly_minimum": 0, "claiming": False})
    _sent(db, 1, TODAY - timedelta(days=22))
    sg = next_steps.sign(settings=s, db=db, today=TODAY)
    assert sg and sg["key"].startswith("sit:quiet:") and "quiet" in sg["line"].lower()
    _sent(db, 2, TODAY - timedelta(days=21))  # a newer application starts a new count
    assert next_steps.sign(settings=s, db=db, today=TODAY)["key"] != sg["key"]


def test_progress_counts_the_plan_only(make):
    from autoapply import benefits
    s, db = make({"label": "Work search", "weekly_minimum": 0})
    assert next_steps.progress(db, TODAY) is None
    benefits.add_task(db, "RESEA meeting", kind="resea")
    next_steps.import_plan(s, db, json.loads((FIX / "laid_off_today_ma.json").read_text())["answers"], TODAY)
    p = next_steps.progress(db, TODAY)
    total = p["total"]
    first = db.conn.execute("select id from ui_tasks where kind = 'next_step' order by id limit 1").fetchone()[0]
    benefits.set_task(db, first, done=True)
    p = next_steps.progress(db, TODAY)
    assert p["done"] == 1 and p["total"] == total and len(p["next"]) <= 3 and all(n["when"] for n in p["next"])


def test_dashboard_and_stats_render(make):
    from web_client import client
    s, db = make({"label": "Work search", "weekly_minimum": 0, "claiming": False})
    _agency(db, date.today() - timedelta(days=1))
    next_steps.import_plan(s, db, json.loads((FIX / "change_no_date.json").read_text())["answers"], date.today())
    c = client(s, db)
    html = c.get("/").text
    assert 'href="/situation"' in html and "Not now" in html
    assert "Your plan" in c.get("/stats").text
