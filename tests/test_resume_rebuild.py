"""Any resume works (resume_rebuild.py): a Word or PDF resume that isn't in the shape tailoring needs is rebuilt once
by the person's own AI, every number checked against the original, the original kept, and the person checks it."""
import json

import docx
import pytest

from autoapply import docs, resume_rebuild
from autoapply.config import Settings

ORIGINAL = """Sam Stranger
Data Analyst · Columbus, OH · sam@example.org
Summary
Data analyst with five years of SQL and Tableau, building weekly reporting for operations teams.
Experience
Data Analyst, Acme Logistics, 2021 to 2026
Built 30 Tableau dashboards used by 200 managers.
Cut the monthly close report from 3 days to 4 hours.
Skills
SQL, Tableau, Excel, Python
Education
B.S. Statistics, Ohio State University, 2020"""

GOOD = {"name": "Sam Stranger", "contact": "Columbus, OH | sam@example.org", "headline": "DATA ANALYST",
        "summary": "Data analyst with five years of SQL and Tableau, building weekly reporting for operations teams.",
        "skills": [{"label": "Analysis", "text": "SQL, Python, Excel"}, {"label": "BI", "text": "Tableau"}],
        "experience": [{"title": "Data Analyst", "org": "Acme Logistics", "dates": "2021 – 2026",
                        "bullets": ["Built 30 Tableau dashboards used by 200 managers.",
                                    "Cut the monthly close report from 3 days to 4 hours."]}],
        "education": ["B.S. Statistics, Ohio State University (2020)"], "other": []}


def _plain_docx(path):
    d = docx.Document()
    d.add_heading("Sam Stranger", 0)
    for line in ORIGINAL.splitlines()[1:]:
        d.add_paragraph(line)
    d.save(path)
    return path


@pytest.fixture
def env(tmp_path):
    (tmp_path / "materials").mkdir()
    s = Settings(root=tmp_path, raw={"data_dir": str(tmp_path / "data")}, profile={"last_name": "Stranger"})
    return s


def test_an_ordinary_word_resume_needs_a_rebuild(env, tmp_path):
    p = _plain_docx(env.root / "materials" / "Stranger Resume - Base.docx")
    assert resume_rebuild.needs(p) is True


def test_rebuild_makes_the_shape_tailoring_needs(env):
    p = _plain_docx(env.root / "materials" / "Stranger Resume - Base.docx")
    seen = []
    out = resume_rebuild.rebuild(env, p, ask=lambda text: (seen.append(text) or json.dumps(GOOD)))
    assert out.name == "Stranger Resume - Base.docx" and out.exists()
    o = docs.outline(out)  # raises SpecError when the shape is wrong
    assert o["headline"] == "DATA ANALYST"
    assert [e["heading"] for e in o["experience"]] == ["Data Analyst | Acme Logistics"]
    assert (env.root / "materials" / "Stranger Resume - Original.docx").exists()
    assert "Cut the monthly close report" in seen[0]  # the model read the original's text
    assert resume_rebuild.needs(out) is False
    assert resume_rebuild.pending(env) is True


def test_invented_numbers_are_refused(env):
    p = _plain_docx(env.root / "materials" / "Stranger Resume - Base.docx")
    bad = json.loads(json.dumps(GOOD))
    bad["experience"][0]["bullets"][0] = "Built 45 Tableau dashboards used by 900 managers."
    with pytest.raises(resume_rebuild.RebuildError, match="45"):
        resume_rebuild.rebuild(env, p, ask=lambda text: json.dumps(bad))
    assert (env.root / "materials" / "Stranger Resume - Base.docx").exists()  # the original is left as it was
    assert not (env.root / "materials" / "Stranger Resume - Original.docx").exists()


def test_pdf_only_resume_is_rebuilt(env, monkeypatch):
    pdf = env.root / "materials" / "Stranger Resume - Base.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    monkeypatch.setattr(resume_rebuild, "_text", lambda path: ORIGINAL)
    out = resume_rebuild.rebuild(env, pdf, ask=lambda text: json.dumps(GOOD))
    assert out.suffix == ".docx" and docs.outline(out)["headline"] == "DATA ANALYST"
    assert (env.root / "materials" / "Stranger Resume - Original.pdf").exists()


def test_confirming_clears_pending(env):
    p = _plain_docx(env.root / "materials" / "Stranger Resume - Base.docx")
    resume_rebuild.rebuild(env, p, ask=lambda text: json.dumps(GOOD))
    resume_rebuild.confirm(env)
    assert resume_rebuild.pending(env) is False


# ---- the upload, the wait for an AI, the check -----------------------------------------------------

@pytest.fixture
def web(env, monkeypatch):
    from autoapply import setup
    from autoapply.db import DB
    from web_client import client
    db = DB(env.data_dir / "autoapply.db")
    monkeypatch.setattr(setup, "agent_ready", lambda prefs=None: {"done": False, "note": "", "who": [], "claude": False, "codex": False})
    return env, client(env, db), monkeypatch


def _upload(c, **files):
    return c.post("/setup/resume", files={k: (n, b, t) for k, (n, b, t) in files.items()}, follow_redirects=False)


def test_pdf_only_upload_waits_for_the_ai(web):
    from autoapply import setup
    env, c, _ = web
    r = _upload(c, pdf=("resume.pdf", b"%PDF-1.4 fake", "application/pdf"))
    assert r.status_code == 303 and "once+your+ai+is+connected" in r.headers["location"].replace("%20", "+").lower()
    assert resume_rebuild.waiting(env) and setup.status(env, {})["steps"]["resume"]["done"]


def test_upload_rebuilds_now_when_the_ai_is_ready(web, tmp_path):
    from autoapply import setup
    env, c, mp = web
    mp.setattr(setup, "agent_ready", lambda prefs=None: {"done": True, "note": "", "who": ["Claude Code"], "claude": True, "codex": False})
    mp.setattr(resume_rebuild, "_ask_default", lambda settings, prompt: json.dumps(GOOD))
    plain = _plain_docx(tmp_path / "mine.docx")
    r = _upload(c, docx=("mine.docx", plain.read_bytes(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document"))
    assert "rebuilt" in r.headers["location"]
    assert docs.outline(setup.base_resume(env.root))["headline"] == "DATA ANALYST" and resume_rebuild.pending(env)


def test_waiting_resume_is_rebuilt_once_the_ai_is_in(web):
    env, c, _ = web
    _upload(c, pdf=("resume.pdf", b"%PDF-1.4 fake", "application/pdf"))
    import autoapply.resume_rebuild as rr
    orig = rr._text
    rr._text = lambda path: ORIGINAL
    try:
        msg = resume_rebuild.run_waiting(env, ask=lambda t: json.dumps(GOOD))
    finally:
        rr._text = orig
    assert "rebuilt" in msg and not resume_rebuild.waiting(env) and resume_rebuild.pending(env)


def test_check_page_files_and_confirm(web):
    env, c, mp = web
    p = _plain_docx(env.root / "materials" / "Stranger Resume - Base.docx")
    resume_rebuild.rebuild(env, p, ask=lambda t: json.dumps(GOOD))
    assert c.get("/setup/resume/file/base").status_code == 200
    assert c.get("/setup/resume/file/original").status_code == 200
    assert c.get("/setup/resume/file/../../etc").status_code in (400, 404)
    assert "We rebuilt your resume" in c.get("/setup").text
    c.post("/setup/resume/confirm", follow_redirects=False)
    assert not resume_rebuild.pending(env)


def test_daily_run_rebuilds_a_waiting_resume_first():
    from pathlib import Path
    sh = (Path(__file__).resolve().parent.parent / "scripts" / "daily.sh").read_text()
    assert "autoapply resume-rebuild" in sh
    assert sh.index("autoapply resume-rebuild") < sh.index('for stage in $(')
