"""Write the planner's facts for the website (palstack.io/next-step) and copy its engine.

    python scripts/export_planner.py <palstack-website checkout>   # writes assets/planner/planner-facts.json + planner.js
    python scripts/export_planner.py --stdout                      # prints the facts JSON (the JS fixture run uses it)

The website can't fetch from anywhere but itself (its CSP), so the facts are copied in at each release. Only what the
engine reads goes out: the steps, and per state its name, agency link, gate metadata and the fields steps name."""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from autoapply import next_steps  # noqa: E402


def facts_json() -> dict:
    f = next_steps.load_facts()
    fields = {s["state_field"] for s in f["steps"] if s.get("state_field")}
    keep = {"name", "url", "source", "confidence", "checked"} | fields
    states = {code: {k: v for k, v in entry.items() if k in keep} for code, entry in f["states"].items()}
    steps = [{**s, "body": " ".join(str(s["body"]).split())} for s in f["steps"]]
    return {"version": f["version"], "steps": steps, "states": states}


def export(website_root: Path) -> str:
    out = website_root / "assets" / "planner"
    out.mkdir(parents=True, exist_ok=True)
    data = facts_json()
    (out / "planner-facts.json").write_text(json.dumps(data, separators=(",", ":"), default=str) + "\n")
    shutil.copyfile(ROOT / "planner" / "planner.js", out / "planner.js")
    return data["version"]


if __name__ == "__main__":
    if sys.argv[1:] == ["--stdout"]:
        print(json.dumps(facts_json(), default=str))
    elif len(sys.argv) == 2:
        print("facts", export(Path(sys.argv[1])))
    else:
        sys.exit(__doc__)
