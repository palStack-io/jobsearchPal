"""Remake the resumes and letters still waiting on you in the look you saved (Setup), from each application's
docs.json: the tailored text is reused, nothing is rewritten. Asked for by Harun, 2026-11-17."""
import json
from pathlib import Path

import pytest

from autoapply import remake
from autoapply.config import Settings
from autoapply.db import DB

from web_client import client


def _job(db, data: Path, jid: str, company: str, status: str, docs: bool = True):
    db.upsert_job({"id": jid, "ats": "greenhouse", "company": company, "title": "AE", "location": "",
                   "url": "u", "apply_url": "u", "description": ""})
    folder = data / "applications" / company.lower()
    folder.mkdir(parents=True, exist_ok=True)
    if docs:
        (folder / "docs.json").write_text(json.dumps({"company": company, "resume": {}}))
    resume = folder / f"Lovelace Resume - {company}.pdf"
    resume.write_bytes(b"%PDF old")
    db.update(jid, status=status, plan_json={"url": "u", "files": {"resume": str(resume)}})


@pytest.fixture
def world(tmp_path):
    (tmp_path / "materials").mkdir()
    (tmp_path / "materials" / "Lovelace Resume.docx").write_bytes(b"docx")
    s = Settings(root=tmp_path, raw={"data_dir": str(tmp_path / "data")},
                 profile={"last_name": "Lovelace", "resume_look": "teal", "resume_accent": "7C2D12"})
    db = DB(s.data_dir / "t.db")
    _job(db, s.data_dir, "gh:a", "Acme", "filled")
    _job(db, s.data_dir, "gh:b", "Globex", "prepped")
    _job(db, s.data_dir, "gh:c", "Initech", "submitted")        # sent: its documents stay as they went out
    _job(db, s.data_dir, "gh:d", "Hooli", "skipped")
    _job(db, s.data_dir, "gh:e", "Umbrella", "filled", docs=False)  # nothing to rebuild from
    return s, db


def test_waiting_lists_only_unsent_applications_with_their_text(world):
    s, db = world
    assert sorted(j for j, _ in remake.waiting(db)) == ["gh:a", "gh:b"]


def test_remake_uses_the_saved_look_and_reports_failures(world, monkeypatch):
    s, db = world
    calls = []

    def build(base, spec, out_dir, last_name="", pdf=True, today=None, look=None, accent=None):
        calls.append((spec["company"], look, accent, Path(out_dir).name, last_name))
        if spec["company"] == "Globex":
            raise ValueError("bad spec")
        new = Path(out_dir) / f"Lovelace Resume - {spec['company']} (2).pdf"
        new.write_bytes(b"%PDF new")
        return {"resume": str(new.with_suffix(".docx")), "pdf": {"resume": str(new)}, "problems": []}

    monkeypatch.setattr(remake.docs, "build", build)
    r = remake.Remaker()
    r.run(s, db)
    assert sorted(calls) == [("Acme", "teal", "7C2D12", "acme", "Lovelace"), ("Globex", "teal", "7C2D12", "globex", "Lovelace")]
    assert r.status() == {"running": False, "done": 1, "total": 2, "failed": ["Globex"], "look": "teal"}
    # the form gets the new file the next time it is filled or submitted
    assert json.loads(db.get("gh:a")["plan_json"])["files"]["resume"].endswith("Acme (2).pdf")
    assert json.loads(db.get("gh:b")["plan_json"])["files"]["resume"].endswith("Globex.pdf")  # failed: untouched


def test_setup_offers_the_remake_and_runs_it(world, monkeypatch):
    s, db = world
    started = []
    monkeypatch.setattr(remake.Remaker, "start", lambda self, settings, db: started.append(True) or True)
    c = client(s, db)
    page = c.get("/setup").text
    assert 'action="/setup/look/remake"' in page and "Remake the 2 waiting resumes and letters" in page
    r = c.post("/setup/look/remake", follow_redirects=False)
    assert r.status_code == 303 and started == [True] and "Remaking" in r.headers["location"].replace("%20", " ")


def test_no_offer_without_waiting_applications(tmp_path):
    s = Settings(root=tmp_path, raw={"data_dir": str(tmp_path / "data")}, profile={})
    db = DB(s.data_dir / "t.db")
    assert "/setup/look/remake" not in client(s, db).get("/setup").text
