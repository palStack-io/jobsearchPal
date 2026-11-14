"""autoapply careers: open a careers page in a real browser and list its job links, including ones a script
draws inside an embedded job board (Paycom, iCIMS...), which a plain page fetch never sees."""
import functools
import http.server
import threading
from pathlib import Path

import pytest

from autoapply import careers

HERE = Path(__file__).parent


@pytest.fixture(scope="module")
def base_url():
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(HERE / "fixtures"))
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def test_lists_jobs_drawn_by_a_script_inside_a_lazy_iframe(base_url):
    out = careers.jobs_on_page(f"{base_url}/careers/index.html")
    titles = {j["title"]: j["url"] for j in out["jobs"]}
    assert titles["Senior Analytics Engineer"] == f"{base_url}/careers/jobs/101"
    assert titles["Director of Engineering"] == f"{base_url}/careers/jobs/102"
    assert out["embedded"] == [f"{base_url}/careers/board.html?clientkey=ABC&fromClientSide=true"]


def test_leaves_out_site_links_and_puts_the_asked_title_first(base_url):
    out = careers.jobs_on_page(f"{base_url}/careers/index.html", title="Sr. Analytics Engineer")
    titles = [j["title"] for j in out["jobs"]]
    assert titles[0] == "Senior Analytics Engineer"
    assert out["jobs"][0]["match"] >= 0.75
    for junk in ("Home", "About us", "open roles", "LinkedIn", "Email us", "Privacy Policy"):
        assert junk not in titles


def test_classify_links():
    keep = careers._looks_like_job
    assert keep("Senior Analytics Engineer", "https://acme.example/jobs/1")
    assert not keep("Privacy Policy", "https://acme.example/privacy")
    assert not keep("Apply", "https://acme.example/jobs/1")  # a button, not a title
    assert not keep("Senior Analytics Engineer", "mailto:a@b.c")
    assert not keep("Bamboo on X", "https://x.com/bamboo")


def test_board_links_come_before_the_site_menu(base_url):
    out = careers.jobs_on_page(f"{base_url}/careers/index.html")
    assert [j.get("board", False) for j in out["jobs"]] == sorted((j.get("board", False) for j in out["jobs"]), reverse=True)
    assert out["jobs"][0]["board"] is True
