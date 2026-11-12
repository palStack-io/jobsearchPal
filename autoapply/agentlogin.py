"""Signing in to the agent CLIs from the dashboard, for servers where nobody wants to open a terminal.

- Claude: you paste a token from `claude setup-token` (run on any computer with Claude Code). It is saved in
  data/secrets.json as CLAUDE_CODE_OAUTH_TOKEN, checked with one tiny call, and handed to every run.
- Codex: the page starts `codex login --device-auth` here and shows its link and code; you open the link on any
  device, enter the code, and Codex saves its own login (~/.codex/auth.json).
"""
from __future__ import annotations

import re
import subprocess
import threading
import time
from pathlib import Path

from .prefs import find_cli

TOKEN_KEY = "CLAUDE_CODE_OAUTH_TOKEN"
_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07")
_URL = re.compile(r"https://\S+")
_CODE = re.compile(r"\b[A-Z0-9]{4,5}-[A-Z0-9]{4,5}\b")
CODEX_TIMEOUT = 15 * 60

_lock = threading.RLock()
_codex: dict = {}


def clean_token(raw: str) -> tuple[str | None, str | None]:
    """The token as pasted (spaces and line breaks removed), or a reason it can't be one."""
    tok = re.sub(r"\s", "", raw or "")
    if not tok:
        return None, "Paste the token that claude setup-token printed."
    if not tok.startswith("sk-ant-") or not 20 <= len(tok) <= 500 or not re.fullmatch(r"[A-Za-z0-9_\-]+", tok):
        return None, "That doesn't look like a Claude token (it starts with sk-ant-). Copy the whole line it printed."
    return tok, None


def codex_signed_in() -> bool:
    return Path("~/.codex/auth.json").expanduser().exists()


def _read(proc: subprocess.Popen, state: dict):
    for raw in iter(proc.stdout.readline, ""):
        line = _ANSI.sub("", raw).strip()
        if not line:
            continue
        with _lock:
            state["lines"] = (state["lines"] + [line])[-30:]
            if not state.get("url") and (m := _URL.search(line)):
                state["url"] = m.group(0).rstrip(".,)")
            if not state.get("code") and (m := _CODE.search(line)):
                state["code"] = m.group(0)
    proc.wait()
    with _lock:
        state["exit"] = proc.returncode


def start_codex() -> dict:
    """Starts `codex login --device-auth` unless one is already waiting. Returns the state."""
    exe = find_cli("codex")
    if not exe:
        return {"error": "Codex CLI isn't installed here."}
    with _lock:
        p = _codex.get("proc")
        if p and p.poll() is None and time.time() - _codex["started"] < CODEX_TIMEOUT:
            return codex_state()
        _codex.clear()
        _codex.update(lines=[], started=time.time(), url=None, code=None, exit=None)
        proc = subprocess.Popen([exe, "login", "--device-auth"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, text=True, bufsize=1)
        _codex["proc"] = proc
    threading.Thread(target=_read, args=(proc, _codex), daemon=True).start()
    return codex_state()


def codex_state() -> dict:
    with _lock:
        p = _codex.get("proc")
        if p and p.poll() is None and time.time() - _codex.get("started", 0) > CODEX_TIMEOUT:
            p.kill()
        running = bool(p and p.poll() is None)
        return {"running": running, "url": _codex.get("url"), "code": _codex.get("code"),
                "exit": _codex.get("exit"), "lines": list(_codex.get("lines") or [])[-8:],
                "signed_in": codex_signed_in()}


def cancel_codex():
    with _lock:
        p = _codex.get("proc")
        if p and p.poll() is None:
            p.kill()
