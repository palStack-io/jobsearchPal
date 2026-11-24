"""The browser engine (planner/planner.js) gives the same results as the Python one, in any time zone (2026-11-24)."""
import json
import os
import shutil
import sys
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("tz", ["", "America/Los_Angeles", "Pacific/Kiritimati"])
def test_js_engine_matches_fixtures(tmp_path, tz):
    node = shutil.which("node")
    if not node:
        if os.environ.get("CI"):
            pytest.fail("node is needed in CI")
        pytest.skip("node not installed")
    facts = subprocess.run([sys.executable, str(ROOT / "scripts" / "export_planner.py"), "--stdout"], capture_output=True,
                           text=True, check=True).stdout
    (tmp_path / "facts.json").write_text(facts)
    env = {**os.environ, **({"TZ": tz} if tz else {})}
    r = subprocess.run([node, str(ROOT / "planner" / "run_fixtures.mjs"), str(tmp_path / "facts.json")],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stdout + r.stderr


def test_python_and_js_codes_match():
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    from autoapply import next_steps
    a = json.loads((ROOT / "tests/fixtures/next_steps/laid_off_today_ma.json").read_text())["answers"]
    js = subprocess.run([node, "--input-type=module", "-e",
                         f"import {{encodeCode}} from '{ROOT / 'planner/planner.js'}'; console.log(encodeCode({json.dumps(a)}))"],
                        capture_output=True, text=True, check=True).stdout.strip()
    assert js == next_steps.encode_code(a)
