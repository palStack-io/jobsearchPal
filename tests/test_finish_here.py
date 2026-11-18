"""Finish here: one session at a time on the server's browser box. A local headless Chromium with a DevTools port
stands in for the box (the real box is tested in test_finish_box.py)."""
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from autoapply import finish_here
from autoapply.config import Settings
from autoapply.finish_here import Box, Sessions, box_from, cdp_url, health

from test_fillers import PROFILE, base_url, pdfs  # noqa: F401  (fixtures)


def _start_chromium():
    with sync_playwright() as p:
        exe = p.chromium.executable_path
    d = tempfile.mkdtemp()
    # --use-mock-keychain / --password-store=basic: what Playwright adds itself; without them macOS asks for the
    # keychain ("Chromium Safe Storage") every time a fresh Chromium starts.
    # --no-sandbox on Linux: Playwright adds it there too; CI runners (Ubuntu 24.04) block the sandbox.
    extra = ["--no-sandbox"] if sys.platform.startswith("linux") else []
    log = Path(d) / "chromium.log"
    proc = subprocess.Popen([exe, "--headless=new", "--remote-debugging-port=0", "--remote-debugging-address=127.0.0.1",
                             f"--user-data-dir={d}", "--no-first-run", "--use-mock-keychain", "--password-store=basic",
                             *extra, "about:blank"], stdout=subprocess.DEVNULL, stderr=log.open("wb"))
    f = Path(d) / "DevToolsActivePort"
    for _ in range(100):
        if f.exists() and f.read_text().strip():
            return proc, int(f.read_text().split()[0])
        time.sleep(0.1)
    proc.kill()
    raise RuntimeError("chromium did not start:\n" + log.read_text(errors="replace")[-2000:])


@pytest.fixture
def chrome():
    proc, port = _start_chromium()
    yield proc, Box("127.0.0.1", cdp_port=port)
    proc.kill()


def _wait(sessions, states, timeout=30):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        st = sessions.status()
        if st["state"] in states:
            return st
        time.sleep(0.2)
    raise AssertionError(f"still {sessions.status()}")


def _other_page(box):
    """A second connection to the same browser, standing in for you at the viewer."""
    p = sync_playwright().start()
    b = p.chromium.connect_over_cdp(cdp_url(box))
    pages = [pg for c in b.contexts for pg in c.pages if pg.url.startswith("http")]
    return p, b, pages[0]


def _plan(base_url, pdfs, form="gh"):
    return {"url": f"{base_url}/{form}/", "ats": "greenhouse", "files": pdfs}


def test_cdp_url_resolves_names():
    assert cdp_url(Box("localhost", cdp_port=9222)) == "http://127.0.0.1:9222"


def test_off_by_default(tmp_path):
    assert box_from(Settings(root=tmp_path, raw={}, profile={})) is None
    on = Settings(root=tmp_path, raw={"finish": {"on_server": True, "host": "finish", "cdp_port": 9, "vnc_port": 8}}, profile={})
    assert box_from(on) == Box("finish", cdp_port=9, vnc_port=8)


def test_demo_never_offers_it(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOAPPLY_DEMO", "1")
    assert box_from(Settings(root=tmp_path, raw={"finish": {"on_server": True}}, profile={})) is None


def test_health(chrome):
    _, box = chrome
    h = health(box)
    assert h["ok"] is True and h["error"] == "" and h["version"] == ""  # a plain Chromium has no box version file
    dead = health(Box("127.0.0.1", cdp_port=1))
    assert dead["ok"] is False and dead["error"]


def test_start_fills_then_ready(chrome, base_url, pdfs):
    _, box = chrome
    s = Sessions(box)
    assert s.start("job:1", _plan(base_url, pdfs), PROFILE, mark_sent=lambda j, c: {"ok": True}) == {"started": True}
    st = _wait(s, {"ready"})
    assert st["job_id"] == "job:1" and isinstance(st["unanswered"], list)
    s.close()
    assert _wait(s, {"closed"})["state"] == "closed"


def test_second_start_is_busy(chrome, base_url, pdfs):
    _, box = chrome
    s = Sessions(box)
    s.start("job:1", _plan(base_url, pdfs), PROFILE, mark_sent=lambda j, c: {})
    assert s.start("job:2", _plan(base_url, pdfs), PROFILE, mark_sent=lambda j, c: {}) == {"busy": "job:1"}
    s.close()
    _wait(s, {"closed"})


def test_paste_types_into_focused_field(chrome, base_url, pdfs):
    _, box = chrome
    s = Sessions(box)
    s.start("job:1", _plan(base_url, pdfs), PROFILE, mark_sent=lambda j, c: {})
    _wait(s, {"ready"})
    p, b, page = _other_page(box)
    page.fill("#first_name", "")
    page.focus("#first_name")
    assert s.paste("123456") is True
    for _ in range(50):
        if page.input_value("#first_name") == "123456":
            break
        time.sleep(0.1)
    assert page.input_value("#first_name") == "123456"
    b.close(); p.stop()
    s.close()
    _wait(s, {"closed"})


def test_confirmation_marks_sent_once(chrome, base_url, pdfs):
    _, box = chrome
    marks = []
    s = Sessions(box)
    s.start("job:1", _plan(base_url, pdfs), PROFILE, mark_sent=lambda j, c: marks.append((j, c)) or {"ok": True})
    _wait(s, {"ready"})
    p, b, page = _other_page(box)
    page.evaluate("document.querySelector('form').dispatchEvent(new Event('submit', {cancelable: true}))")
    _wait(s, {"sent"})
    time.sleep(1.5)
    assert len(marks) == 1 and marks[0][0] == "job:1" and marks[0][1]
    b.close(); p.stop()
    s.close()
    _wait(s, {"closed", "sent"})


def test_idle_close(chrome, base_url, pdfs, monkeypatch):
    _, box = chrome
    monkeypatch.setattr(finish_here, "IDLE_S", 1)
    s = Sessions(box)
    s.start("job:1", _plan(base_url, pdfs), PROFILE, mark_sent=lambda j, c: {})
    assert _wait(s, {"closed"})["state"] == "closed"
    p, b, _ = sync_playwright().start(), None, None
    b = p.chromium.connect_over_cdp(cdp_url(box))
    assert [pg for c in b.contexts for pg in c.pages if pg.url.startswith("http")] == []
    b.close(); p.stop()
    assert s.start("job:2", _plan(base_url, pdfs), PROFILE, mark_sent=lambda j, c: {}) == {"started": True}
    s.close()
    _wait(s, {"closed"})


def test_touch_keeps_it_open(chrome, base_url, pdfs, monkeypatch):
    _, box = chrome
    monkeypatch.setattr(finish_here, "IDLE_S", 2)
    s = Sessions(box)
    s.start("job:1", _plan(base_url, pdfs), PROFILE, mark_sent=lambda j, c: {})
    _wait(s, {"ready"})
    for _ in range(6):
        s.touch()
        time.sleep(0.5)
    assert s.status()["state"] == "ready"
    s.close()
    _wait(s, {"closed"})


def test_browser_gone_frees_lock(base_url, pdfs):
    proc, port = _start_chromium()
    box = Box("127.0.0.1", cdp_port=port)
    s = Sessions(box)
    s.start("job:1", _plan(base_url, pdfs), PROFILE, mark_sent=lambda j, c: {})
    _wait(s, {"ready"})
    proc.kill()
    st = _wait(s, {"error", "closed"})
    assert st["state"] == "error" and "Try again" in st["error"]
    proc2, port2 = _start_chromium()
    s.box = Box("127.0.0.1", cdp_port=port2)
    assert s.start("job:1", _plan(base_url, pdfs), PROFILE, mark_sent=lambda j, c: {}) == {"started": True}
    _wait(s, {"ready"})
    s.close()
    _wait(s, {"closed"})
    proc2.kill()


def test_unreachable_box_is_an_error_not_a_hang(base_url, pdfs):
    s = Sessions(Box("127.0.0.1", cdp_port=1))
    s.start("job:1", _plan(base_url, pdfs), PROFILE, mark_sent=lambda j, c: {})
    st = _wait(s, {"error"}, timeout=20)
    assert "Try again" in st["error"]


def test_closed_posting_ends_with_its_own_message(chrome, base_url, pdfs):
    """A removed role shows "This posting is closed", not "We lost the browser", and the lock is free again."""
    _, box = chrome
    s = Sessions(box)
    plan = {"url": f"{base_url}/ashby-closed", "ats": "ashby", "files": pdfs}
    s.start("job:gone", plan, PROFILE, mark_sent=lambda j, c: {})
    st = _wait(s, {"error"})
    assert st["state"] == "error" and st["error"].startswith("This posting is closed") and st["gone"] is True
    assert s.start("job:2", _plan(base_url, pdfs), PROFILE, mark_sent=lambda j, c: {}) == {"started": True}
    s.close()
    _wait(s, {"closed"})
