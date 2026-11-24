"""Which Your next step facts need re-checking (2026-11-24). Laws change: a state line older than 365 days stops
showing (the gate in autoapply/next_steps.py), so each fact is due a re-check before then.

    python tools/next_steps_status.py                    # full report
    python tools/next_steps_status.py --due-only         # prints nothing when nothing is due (for the llm reminder)
    python tools/next_steps_status.py --today 2027-10-20 # what it will say on a date
    python tools/next_steps_status.py --root <checkout>  # read another checkout

DUE: checked more than 330 days ago (re-check before the gate hides it at 365), or a yearly figure from an earlier
year once January comes. OVERDUE: past 365 days, so it is hidden now. Runbook: skill palstack-yearly-facts."""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

import yaml

FIELDS = ("final_pay", "severance_effect", "mini_cobra", "separation_notice", "state_warn")
DUE_DAYS, GATE_DAYS = 330, 365


def _age(checked, today: date) -> int | None:
    try:
        return (today - date.fromisoformat(str(checked))).days
    except ValueError:
        return None


def report(root: Path, today: date) -> dict:
    """{"due": [...], "overdue": [...], "shown": n, "hidden": n, "states_with_facts": n, "states": n}."""
    pkg = root / "autoapply"
    states = yaml.safe_load((pkg / "work_search_states.yaml").read_text())["states"]
    steps = yaml.safe_load((pkg / "next_steps.yaml").read_text())["steps"]
    due, overdue = [], []
    shown = hidden = 0
    with_facts = 0

    def judge(label: str, checked, yearly: bool = False):
        age = _age(checked, today)
        if age is None:
            overdue.append(f"{label}: no valid checked date")
        elif age > GATE_DAYS:
            overdue.append(f"{label}: checked {checked}, {age} days ago (hidden now)")
        elif age > DUE_DAYS:
            due.append(f"{label}: checked {checked}, {age} days ago (hides at {GATE_DAYS})")
        elif yearly and today.year > date.fromisoformat(str(checked)).year:
            due.append(f"{label}: its figure is from {str(checked)[:4]}; a new year's figure is out")

    for code, entry in sorted(states.items()):
        has = False
        for f in FIELDS:
            v = entry.get(f)
            if not isinstance(v, dict):
                continue
            has = True
            if v.get("confidence") == "high":
                shown += 1
                judge(f"{code} {f}", v.get("checked"), bool(v.get("yearly")))
            else:
                hidden += 1
        with_facts += has
        judge(f"{code} work-search rule", entry.get("checked"))
    for s in steps:
        judge(f"step {s['id']}", s.get("checked"))
    return {"due": due, "overdue": overdue, "shown": shown, "hidden": hidden, "states_with_facts": with_facts,
            "states": len(states)}


def render(r: dict, due_only: bool) -> str:
    if due_only and not (r["due"] or r["overdue"]):
        return ""
    out = [f"Your next step facts: {r['shown']} state lines shown, {r['hidden']} hidden; {r['states_with_facts']} of "
           f"{r['states']} states have layoff facts."]
    if r["overdue"]:
        out += ["", f"OVERDUE ({len(r['overdue'])}): past {GATE_DAYS} days, so hidden from people now:"] + [f"  {x}" for x in r["overdue"]]
    if r["due"]:
        out += ["", f"DUE ({len(r['due'])}): re-check on the official page, update text and checked date:"] + [f"  {x}" for x in r["due"]]
    if not (r["due"] or r["overdue"]):
        out += ["", "Nothing due."]
    out += ["", "How: skill palstack-yearly-facts, section jobsearchPal (research agents, independent review, Harun's approval)."]
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(Path(__file__).resolve().parents[1]))
    ap.add_argument("--today", default="")
    ap.add_argument("--due-only", action="store_true")
    a = ap.parse_args(argv)
    text = render(report(Path(a.root), date.fromisoformat(a.today) if a.today else date.today()), a.due_only)
    if text:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
