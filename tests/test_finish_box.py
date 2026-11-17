"""The browser box and its proxy, as real containers (docker-compose.finish.yml).

Needs Docker, so it only runs with JSP_FINISH_DOCKER=1 (on a Docker host, e.g. `JSP_FINISH_DOCKER=1 pytest
tests/test_finish_box.py`). Ports: FINISH_CDP_PORT / FINISH_VNC_PORT, defaults 9222 / 5900."""
import os
import socket
import subprocess
import time
from pathlib import Path

import httpx
import pytest

pytestmark = pytest.mark.skipif(os.environ.get("JSP_FINISH_DOCKER") != "1", reason="needs Docker: JSP_FINISH_DOCKER=1")

ROOT = Path(__file__).resolve().parent.parent
CDP = int(os.environ.get("FINISH_CDP_PORT", "9222"))
VNC = int(os.environ.get("FINISH_VNC_PORT", "5900"))
COMPOSE = ["docker", "compose", "-f", str(ROOT / "docker-compose.finish.yml"), "-p", "jspfinishtest"]


@pytest.fixture(scope="module")
def box():
    subprocess.run([*COMPOSE, "up", "-d", "--build"], check=True, cwd=ROOT)
    try:
        for _ in range(60):
            try:
                if httpx.get(f"http://127.0.0.1:{CDP}/json/version", timeout=2).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(1)
        yield f"http://127.0.0.1:{CDP}"
    finally:
        subprocess.run([*COMPOSE, "down", "-v"], cwd=ROOT)


@pytest.fixture
def page(box):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.connect_over_cdp(box)
        ctx = b.new_context()
        pg = ctx.new_page()
        pg.set_default_timeout(20000)
        yield pg
        ctx.close()
        b.close()


def test_cdp_answers(box):
    r = httpx.get(f"{box}/json/version", timeout=5)
    assert r.status_code == 200 and "Browser" in r.json()


def test_vnc_answers(box):
    with socket.create_connection(("127.0.0.1", VNC), timeout=5) as s:
        assert s.recv(12).startswith(b"RFB 003.")


def test_upload_without_mount(page, tmp_path):
    f = tmp_path / "resume.pdf"
    f.write_bytes(b"only on the host")
    page.set_content("<input type=file id=f>")
    page.set_input_files("#f", str(f))
    assert page.evaluate("async () => await document.getElementById('f').files[0].text()") == "only on the host"


def test_public_site_loads(page):
    assert page.goto("https://example.com", wait_until="domcontentloaded").status == 200


@pytest.mark.parametrize("url", ["http://192.168.10.247:8123/", "http://100.122.220.35:8787/", "http://10.0.0.1/",
                                 "http://127.0.0.1:8787/", "http://169.254.169.254/"])
def test_home_addresses_refused(page, url):
    try:
        r = page.goto(url, wait_until="domcontentloaded", timeout=15000)
    except Exception:
        return  # refused outright
    assert r is None or r.status == 403, f"{url} answered {r.status}"


def test_version_file(page):
    page.goto("file:///etc/jobsearchpal-finish-version")
    assert page.inner_text("body").strip() == "dev"
