"""Human pacing for the form filler (fillers/pace.py): forms are typed, clicked and read at a person's speed, because
bot scoring on the boards (reCAPTCHA v3/Enterprise, Ashby's checks) flags a form filled in three seconds with no
keystrokes. Tests use the fast pace unless they ask for human (conftest)."""
import functools
import http.server
import random
import threading
from pathlib import Path

import pytest
import yaml
from playwright.sync_api import sync_playwright

from autoapply.fillers import base
from autoapply.fillers.pace import Pace, current

HERE = Path(__file__).parent
PROFILE = yaml.safe_load((HERE.parent / "profile.example.yaml").read_text())


class Clock:
    def __init__(self):
        self.slept = []

    def __call__(self, s):
        self.slept.append(s)


def test_fast_pace_never_sleeps():
    c = Clock()
    p = Pace.fast(sleep=c)
    p.read(), p.between(), p.before_submit()
    assert sum(c.slept) == 0 and p.char_delay_ms() == 0


def test_human_pace_ranges():
    c = Clock()
    p = Pace.human_pace(rng=random.Random(7), sleep=c)
    p.read()
    p.between()
    p.before_submit()
    read, between, before = c.slept
    assert 1.5 <= read <= 4.0 and 0.4 <= between <= 1.4 and 3.0 <= before <= 8.0
    delays = [p.char_delay_ms() for _ in range(200)]
    assert min(delays) >= 35 and max(delays) <= 140


def test_long_answers_are_pasted_short_ones_typed():
    p = Pace.human_pace(rng=random.Random(1), sleep=Clock())
    assert p.pastes("x" * 301) and not p.pastes("Semantic layers.")


def test_pace_comes_from_the_environment(monkeypatch):
    monkeypatch.delenv("AUTOAPPLY_FILL_PACE", raising=False)
    assert current().human
    monkeypatch.setenv("AUTOAPPLY_FILL_PACE", "fast")
    assert not current().human


@pytest.fixture(scope="module")
def base_url():
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(HERE / "fixtures"))
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


COUNT_JS = """() => { window.__keys = 0; window.__moves = 0;
  document.addEventListener('keydown', e => { if (e.isTrusted) window.__keys++ }, true);
  document.addEventListener('mousemove', e => { if (e.isTrusted) window.__moves++ }, true); }"""


def _run(base_url, tmp_path, pace):
    for n in ("resume.pdf", "cover.pdf"):
        (tmp_path / n).write_bytes(b"%PDF-1.4\n")
    plan = {"url": f"{base_url}/gh/", "ats": "greenhouse",
            "files": {"resume": str(tmp_path / "resume.pdf"), "cover_letter": str(tmp_path / "cover.pdf")},
            "answers": {"Why do you want to work here?": "Semantic layers."}}
    with sync_playwright() as p:
        b = p.chromium.launch(args=["--use-mock-keychain", "--password-store=basic"])
        page = b.new_page()
        page.goto(plan["url"])
        page.evaluate(COUNT_JS)
        report = base.Report(url=plan["url"])
        base._fill_page(page, plan, PROFILE, report, pace=pace)
        keys, moves = page.evaluate("[window.__keys, window.__moves]")
        b.close()
    return report, keys, moves


def test_human_pace_types_and_moves(base_url, tmp_path):
    pace = Pace.human_pace(rng=random.Random(3), sleep=Clock(), scale=0.0)  # human behaviour, no waiting in tests
    report, keys, moves = _run(base_url, tmp_path, pace)
    assert {f["label"] for f in report.filled} >= {"First Name", "Why do you want to work here?"}
    assert keys >= len("Semantic layers.") and moves > 0


def test_fast_pace_fills_without_keystrokes(base_url, tmp_path):
    report, keys, moves = _run(base_url, tmp_path, Pace.fast())
    assert keys == 0 and report.filled


def test_verification_step_is_recorded(base_url, tmp_path):
    from autoapply.fillers import fill
    r = fill({"url": f"{base_url}/verify/", "ats": "generic", "files": {}, "answers": {}}, PROFILE, tmp_path)
    assert r.verification is True and r.as_dict()["verification"] is True
    gh = fill({"url": f"{base_url}/gh/", "ats": "greenhouse", "files": {}, "answers": {}}, PROFILE, tmp_path)
    assert gh.verification is False
