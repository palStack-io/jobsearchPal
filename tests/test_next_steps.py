"""Your next step (2026-11-24): the planner's Python engine, held to the golden fixtures the JS engine also runs
(tests/fixtures/next_steps/*.json)."""
import copy
import json
from datetime import date
from pathlib import Path

import pytest

from autoapply import next_steps

FIX = Path(__file__).parent / "fixtures" / "next_steps"
CASES = sorted(FIX.glob("*.json"))


def _facts(patch=None):
    f = copy.deepcopy(next_steps.load_facts())
    for code, fields in ((patch or {}).get("states") or {}).items():
        f["states"][code].update(fields)
    return f


def _ids(steps):
    return [s["id"] for s in steps]


@pytest.mark.parametrize("case", CASES, ids=[c.stem for c in CASES])
def test_fixture(case):
    fx = json.loads(case.read_text())
    out = next_steps.plan(fx["answers"], _facts(fx.get("facts_patch")), date.fromisoformat(fx["today"]))
    every = out["today"] + out["find_out"] + [s for g in out["groups"] for s in g["steps"]]
    ex = fx["expect"]
    if "today_ids" in ex:
        assert _ids(out["today"]) == ex["today_ids"]
    if "next_id" in ex:
        assert (out["next"] or {}).get("id") == ex["next_id"]
    for sid, due in (ex.get("due") or {}).items():
        assert next(s for s in every if s["id"] == sid)["due"] == due, sid
    if "find_out_ids" in ex:
        assert _ids(out["find_out"]) == ex["find_out_ids"]
    if "past_ids" in ex:
        assert [s["id"] for s in every if s["past"]] == ex["past_ids"]
    if "state_note_ids" in ex:
        assert [s["id"] for s in every if s["state_note"]] == ex["state_note_ids"]
    for sid in ex.get("state_url_ids") or []:
        assert next(s for s in every if s["id"] == sid)["state_url"]
    for sid in ex.get("includes_ids") or []:
        assert sid in _ids(every), sid
    for sid in ex.get("excludes_ids") or []:
        assert sid not in _ids(every), sid
    assert len(_ids(every)) == len(set(_ids(every))), "a step shows twice"


def test_code_round_trip():
    a = json.loads((FIX / "laid_off_today_ma.json").read_text())["answers"]
    code = next_steps.encode_code(a)
    assert code.startswith("NS1.") and "=" not in code
    assert next_steps.decode_code(code)["state"] == "MA"


@pytest.mark.parametrize("bad", ["", "hello", "NS1.!!!", "NS1." + "e30", "NS2.eyJ2IjoyfQ"])
def test_decode_rejects_garbage(bad):
    with pytest.raises(ValueError):
        next_steps.decode_code(bad)


def test_validate_rejects_v2_unknown_state_and_bad_dates():
    good = json.loads((FIX / "laid_off_today_ma.json").read_text())["answers"]
    for patch in ({"v": 2}, {"state": "ZZ"}, {"last_day": "24/11/2026"}, {"path": "fired"}):
        with pytest.raises(ValueError):
            next_steps.validate_answers({**good, **patch}, next_steps.load_facts()["states"])


def test_every_step_has_source_checked_and_unique_id():
    steps = next_steps.load_facts()["steps"]
    assert len({s["id"] for s in steps}) == len(steps)
    for s in steps:
        assert s["source"]["url"].startswith("https://") and s["source"]["name"], s["id"]
        assert date.fromisoformat(s["checked"]), s["id"]
        assert s["section"] in next_steps.SECTIONS, s["id"]


def test_gate_needs_official_high_and_fresh():
    f = next_steps.load_facts()
    today = date(2026, 11, 24)
    assert next_steps.state_line(f, "MA", "waiting_week", today)[0]
    for patch in ({"source": "secondary"}, {"confidence": "medium"}, {"checked": "2025-11-01"}):
        g = _facts({"states": {"MA": patch}})
        note, url = next_steps.state_line(g, "MA", "waiting_week", today)
        assert note is None and url


def test_when_values_are_known_words():
    """YAML reads an unquoted 40_54 as the number 4054 (2026-11-24): every age and size in a `when` must be a word the
    engine knows, or the step silently never shows."""
    for s in next_steps.load_facts()["steps"]:
        w = s.get("when") or {}
        assert set(w.get("age_in") or []) <= set(next_steps.AGES), s["id"]
        assert set(w.get("size_in") or []) <= set(next_steps.SIZES), s["id"]
        assert set(w.get("has_all") or []) <= set(next_steps.HAS), s["id"]
        assert set(s["path"]) <= set(next_steps.PATHS), s["id"]
