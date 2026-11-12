import os
import stat
import subprocess
import sys
import time
from pathlib import Path

from autoapply import agentlogin, classify, config
from test_setup import fresh  # noqa: F401  (the fixture)

TOKEN = "sk-ant-oat01-" + "a" * 60


def test_clean_token():
    assert agentlogin.clean_token(f"  {TOKEN[:30]}\n{TOKEN[30:]} ") == (TOKEN, None)
    assert agentlogin.clean_token("")[0] is None
    assert agentlogin.clean_token("hello world")[0] is None
    assert agentlogin.clean_token("sk-ant-<script>alert(1)</script>")[0] is None


def test_claude_token_saved_only_when_it_works(fresh, monkeypatch):  # noqa: F811
    s, c = fresh
    monkeypatch.delenv(agentlogin.TOKEN_KEY, raising=False)
    config.FROM_SECRETS.discard(agentlogin.TOKEN_KEY)
    monkeypatch.setattr(classify, "test_source", lambda name, pr: {"ok": False, "error": "401 invalid token"})
    r = c.post("/setup/claude-token", data={"token": TOKEN})
    assert "Not saved" in r.text and "401 invalid token" in r.text
    assert not os.environ.get(agentlogin.TOKEN_KEY)
    seen = {}
    monkeypatch.setattr(classify, "test_source",
                        lambda name, pr: seen.update(tok=os.environ.get(agentlogin.TOKEN_KEY)) or {"ok": True, "kind": "interview"})
    r = c.post("/setup/claude-token", data={"token": TOKEN})
    assert "Claude is signed in" in r.text and seen["tok"] == TOKEN
    assert TOKEN not in c.get("/setup").text and TOKEN not in c.get("/settings?tab=daily").text  # never shown again
    assert stat.S_IMODE((s.data_dir / "secrets.json").stat().st_mode) == 0o600
    # The daily run picks it up through `autoapply agent-env`.
    env = {k: v for k, v in os.environ.items() if k != agentlogin.TOKEN_KEY}
    (s.root / "config.yaml").write_text((s.root / "config.example.yaml").read_text())
    env["PYTHONPATH"] = str(Path(agentlogin.__file__).resolve().parents[1])  # this checkout, not an installed copy
    out = subprocess.run([sys.executable, "-m", "autoapply.cli", "--config", str(s.root / "config.yaml"), "agent-env"],
                         capture_output=True, text=True, env=env, cwd=s.root)
    assert out.stdout.strip() == f"export {agentlogin.TOKEN_KEY}={TOKEN}", out.stderr[-800:]
    r = c.post("/setup/claude-token", data={"action": "forget", "back": "settings"}, follow_redirects=False)
    assert r.headers["location"].startswith("/settings?tab=daily") and not os.environ.get(agentlogin.TOKEN_KEY)


def test_codex_device_login_shows_link_and_code(tmp_path, monkeypatch):
    fake = tmp_path / "codex"
    fake.write_text("#!/bin/sh\necho 'Open \033[1mhttps://auth.openai.com/codex/device\033[0m and enter'\n"
                    "echo 'ABCD-12345'\nsleep 30\n")
    fake.chmod(0o755)
    monkeypatch.setattr(agentlogin, "find_cli", lambda name: str(fake))
    monkeypatch.setenv("HOME", str(tmp_path))
    agentlogin.start_codex()
    for _ in range(50):
        st = agentlogin.codex_state()
        if st["code"]:
            break
        time.sleep(0.1)
    assert st["running"] and st["url"] == "https://auth.openai.com/codex/device" and st["code"] == "ABCD-12345"
    assert agentlogin.start_codex()["code"] == "ABCD-12345"  # a second click reuses the waiting login
    agentlogin.cancel_codex()
    time.sleep(0.3)
    assert not agentlogin.codex_state()["running"] and not agentlogin.codex_state()["signed_in"]
