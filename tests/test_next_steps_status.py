"""The yearly re-check report for Your next step's facts (2026-11-24)."""
from datetime import date
from pathlib import Path

from tools import next_steps_status as st

ROOT = Path(__file__).resolve().parents[1]


def test_nothing_due_right_after_checking():
    r = st.report(ROOT, date(2026, 12, 1))
    assert not r["overdue"] and not r["due"] and r["shown"] > 150 and r["states_with_facts"] >= 52
    assert st.render(r, due_only=True) == ""


def test_january_flags_the_yearly_figure():
    r = st.report(ROOT, date(2027, 1, 5))
    assert any(x.startswith("PA severance_effect") and "new year's figure" in x for x in r["due"])
    assert "DUE" in st.render(r, due_only=True)


def test_due_before_the_gate_hides_it_then_overdue():
    due = st.report(ROOT, date(2027, 10, 25))
    assert any("final_pay" in x and "hides at 365" in x for x in due["due"])
    late = st.report(ROOT, date(2027, 12, 1))
    assert any("hidden now" in x for x in late["overdue"])
