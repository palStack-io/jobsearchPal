"""Day one for a stranger (install walk 2026-11-22): what a new install says
must be true before anything has run, before the AI is signed in and before email is connected."""
import json
from pathlib import Path

import pytest

from autoapply import finder, setup
from autoapply.config import Settings
from autoapply.db import DB


# ---- S13: US roles only let "Remote Taiwan" through -----------------------------------------------

@pytest.mark.parametrize("loc", ["Remote Taiwan", "Remote - Asia Pacific", "Remote, Vietnam", "Remote (South Korea)",
                                 "Remote, China", "Remote - Nigeria", "Remote, Pakistan", "Remote — Worldwide except US"])
def test_non_us_remote_places_are_not_us(loc):
    assert not finder.location_ok(loc, {})


@pytest.mark.parametrize("loc", ["Remote", "Remote (US)", "Remote - United States", "New York, NY", "Boston, MA", "",
                                 "Remote, Georgia", "Indianapolis, IN"])
def test_us_places_still_pass(loc):
    assert finder.location_ok(loc, {})


# ---- the false "signed in": running claude once creates ~/.claude.json -----------------------------

def test_claude_json_alone_is_not_signed_in(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.setattr(setup, "find_cli", lambda name: "/usr/bin/claude" if name == "claude" else None)
    (tmp_path / ".claude.json").write_text(json.dumps({"numStartups": 1, "firstStartTime": "2026-11-22"}))
    assert setup.agent_ready()["done"] is False
    (tmp_path / ".claude.json").write_text(json.dumps({"numStartups": 2, "oauthAccount": {"emailAddress": "a@b.c"}}))
    assert setup.agent_ready()["done"] is True


# ---- the dashboard on day one -----------------------------------------------------------------------

@pytest.fixture
def fresh(tmp_path, monkeypatch):
    monkeypatch.delenv("GMAIL_USER", raising=False)
    monkeypatch.delenv("GMAIL_APP_PASSWORD", raising=False)
    s = Settings(root=tmp_path, raw={"data_dir": str(tmp_path / "data"),
                                     "work_search": {"label": "Work search", "weekly_minimum": 0}},
                 profile={"first_name": "Sam"})
    db = DB(s.data_dir / "autoapply.db")
    return s, db


def _home(s, db):
    from web_client import client
    return client(s, db).get("/").text


def test_nothing_has_run_yet(fresh):
    s, db = fresh
    html = _home(s, db)
    assert "Everything ran on time" not in html and "Nothing has run yet" in html


def test_steps_not_set_up_are_not_failures(fresh):
    s, db = fresh
    db.log_run("sync-email", False, "no mailbox set up")
    db.log_run("daily", False, "skipped: claude isn't signed in (Settings > Daily run)")
    db.log_run("find", True, "2 new")
    html = _home(s, db)
    assert "on us" not in html
    assert "Setup isn't finished" in html


def test_no_work_search_goal_until_one_is_set(fresh):
    s, db = fresh
    html = _home(s, db)
    assert "covers this week" not in html and "of 0" not in html


def test_week_popup_points_to_your_coach():
    src = (Path(__file__).resolve().parent.parent / "autoapply" / "templates" / "index.html").read_text()
    assert "under Your week" not in src and "under Your coach" in src


def test_example_config_starts_without_a_work_search_goal():
    import yaml
    cfg = yaml.safe_load((Path(__file__).resolve().parent.parent / "config.example.yaml").read_text())
    assert cfg["work_search"]["weekly_minimum"] == 0
