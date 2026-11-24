"""Your next step in the app (2026-11-24): a plan becomes to-dos; the claim is set on a fresh install or after the
person confirms; an old install is never changed without the confirm screen."""
import json
from datetime import date
from pathlib import Path

import pytest
import yaml

from autoapply import next_steps
from autoapply.config import Settings
from autoapply.db import DB

TODAY = date(2026, 11, 24)
FIX = Path(__file__).parent / "fixtures" / "next_steps"


def _answers(name="laid_off_today_ma", **kw):
    return {**json.loads((FIX / f"{name}.json").read_text())["answers"], **kw}


@pytest.fixture
def env(tmp_path):
    def make(ws):
        (tmp_path / "materials").mkdir(exist_ok=True)
        cfg = {"data_dir": "data", "search": {"title_keywords": []}, "work_search": ws}
        (tmp_path / "config.yaml").write_text(yaml.safe_dump(cfg))
        raw = yaml.safe_load((tmp_path / "config.yaml").read_text()) | {"data_dir": str(tmp_path / "data")}
        s = Settings(root=tmp_path, raw=raw, profile={})
        return s, DB(s.data_dir / "autoapply.db")
    return make


def _cfg(s):
    return yaml.safe_load((s.root / "config.yaml").read_text())["work_search"]


def _tasks(db):
    from autoapply import benefits
    benefits.ensure(db)
    return [dict(r) for r in db.conn.execute("select * from ui_tasks order by id")]


def test_fresh_install_layoff_sets_claiming_true_with_state(env):
    s, db = env({"label": "Work search", "weekly_minimum": 0})
    r = next_steps.import_plan(s, db, _answers(), TODAY)
    ws = _cfg(s)
    assert ws["claiming"] is True and ws["state"] == "MA" and ws["weekly_minimum"] == 3 and r["claiming_set"]
    assert "claim_start" not in ws
    rows = _tasks(db)
    assert rows[0]["title"] == "File for unemployment" and rows[0]["due"] == "2026-11-30"
    assert all("[next_step:" in t["note"] and ("Source:" in t["note"] or "general reminder" in t["note"]) for t in rows)


def test_change_path_sets_claiming_false(env):
    s, db = env({"label": "Work search", "weekly_minimum": 0})
    next_steps.import_plan(s, db, _answers("change_no_date"), TODAY)
    assert _cfg(s)["claiming"] is False


def test_old_install_is_untouched_without_confirm(env):
    s, db = env({"label": "DUA", "weekly_minimum": 3, "state": "MA"})  # before the claiming question: means claiming
    before = (s.root / "config.yaml").read_bytes()
    r = next_steps.import_plan(s, db, _answers("change_no_date"), TODAY)
    assert (s.root / "config.yaml").read_bytes() == before and not r["claiming_set"]


def test_confirmed_change_turns_on_claiming_for_existing_user(env):
    s, db = env({"label": "Work search", "weekly_minimum": 0, "claiming": False})
    assert any("Turn on unemployment for Massachusetts" in x for x in next_steps.preview_changes(s, db, _answers(), TODAY))
    next_steps.import_plan(s, db, _answers(), TODAY, replace=True)
    assert _cfg(s)["claiming"] is True and _cfg(s)["state"] == "MA"


def test_preview_writes_nothing(env):
    s, db = env({"label": "Work search", "weekly_minimum": 0, "claiming": False})
    before = (s.root / "config.yaml").read_bytes()
    next_steps.preview_changes(s, db, _answers(), TODAY)
    assert (s.root / "config.yaml").read_bytes() == before and _tasks(db) == []


def test_reimport_adds_no_duplicates(env):
    s, db = env({"label": "Work search", "weekly_minimum": 0})
    n = next_steps.import_plan(s, db, _answers(), TODAY)["added"]
    r = next_steps.import_plan(s, db, _answers(), TODAY)
    assert r["added"] == 0 and r["skipped"] == n and len(_tasks(db)) == n


def test_new_plan_replaces_unfinished_old_steps_keeps_done(env):
    s, db = env({"label": "Work search", "weekly_minimum": 0})
    next_steps.import_plan(s, db, _answers("change_no_date"), TODAY)
    first = _tasks(db)[0]
    db.conn.execute("update ui_tasks set done = 1 where id = ?", (first["id"],))
    db.conn.commit()
    preview = next_steps.preview_changes(s, db, _answers(), TODAY)
    assert any("Remove" in x and "you finished stay" in x for x in preview)
    next_steps.import_plan(s, db, _answers(), TODAY, replace=True)
    rows = _tasks(db)
    assert any(r["id"] == first["id"] for r in rows)
    assert not any("[next_step:quiet-search]" in r["note"] for r in rows)


def test_back_to_work_turns_claiming_off_and_clears_unfinished(env):
    s, db = env({"label": "Work search", "weekly_minimum": 3, "claiming": True, "state": "MA"})
    next_steps.import_plan(s, db, _answers(), TODAY, replace=True)
    r = next_steps.back_to_work(s, db)
    assert _cfg(s)["claiming"] is False and r["removed"] > 0 and _tasks(db) == []


def test_layoff_without_state_asks_for_it(env):
    s, db = env({"label": "Work search", "weekly_minimum": 0})
    with pytest.raises(ValueError, match="state you worked in"):
        next_steps.import_plan(s, db, _answers(state=None), TODAY)
    assert _tasks(db) == []


def test_tag_survives_a_long_note_and_is_hidden_on_the_dashboard(env):
    """A state line can make a note long; the [next_step:<id>] tag (used to avoid duplicates) must always fit, and the
    dashboard never shows it (2026-11-24)."""
    from datetime import datetime
    from autoapply import benefits
    s, db = env({"label": "Work search", "weekly_minimum": 0})
    next_steps.import_plan(s, db, _answers(), TODAY)
    assert all(t["note"].endswith("]") and len(t["note"]) <= 400 for t in _tasks(db))
    items = benefits.todos(db, s, None, 0, 0, 0, datetime(2026, 11, 24, 9))
    assert items and not any("[next_step:" in i["why"] for i in items)
