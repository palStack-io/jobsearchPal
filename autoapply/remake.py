"""Remakes the resume and cover letter of every application still waiting on you, in the look saved on Setup.

Each application keeps the tailored text it was built from (docs.json next to its files), so this only lays the same
words out again: nothing is rewritten and no agent runs. Sent and skipped applications are left as they are. A remade
cover letter carries today's date. Runs in the background: a LibreOffice PDF takes a few seconds per document.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

from . import docs

WAITING = ("filled", "prepped")


def waiting(db) -> list[tuple[str, Path]]:
    """[(job id, its docs.json)] for every unsent application whose tailored text is still on disk."""
    out = []
    for jid, plan_json in db.conn.execute(f"select id, plan_json from jobs where status in ({','.join('?' * len(WAITING))}) "
                                          "order by id", WAITING):
        try:
            resume = (json.loads(plan_json or "{}").get("files") or {}).get("resume")
        except json.JSONDecodeError:
            continue
        spec = Path(resume).parent / "docs.json" if resume else None
        if spec and spec.exists():
            out.append((jid, spec))
    return out


class Remaker:
    def __init__(self):
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._st = {"running": False, "done": 0, "total": 0, "failed": [], "look": ""}

    def status(self) -> dict:
        with self._lock:
            return {**self._st, "failed": list(self._st["failed"])}

    def start(self, settings, db) -> bool:
        """False when a remake is already running."""
        with self._lock:
            if self._st["running"]:
                return False
            self._st["running"] = True
        self._thread = threading.Thread(target=self.run, args=(settings, db), daemon=True, name="remake resumes")
        self._thread.start()
        return True

    def run(self, settings, db) -> None:
        from .setup import base_resume
        items = waiting(db)
        look, accent = settings.profile.get("resume_look"), settings.profile.get("resume_accent")
        with self._lock:
            self._st.update(running=True, done=0, total=len(items), failed=[], look=look or "")
        base = base_resume(settings.root)
        for jid, spec_path in items:
            company = jid
            try:
                spec = json.loads(spec_path.read_text())
                company = spec.get("company") or jid
                if base is None:
                    raise FileNotFoundError("no base resume")
                r = docs.build(base, spec, spec_path.parent, last_name=settings.profile.get("last_name", ""),
                               look=look, accent=accent)
                job = db.get(jid) or {}
                plan = json.loads(job.get("plan_json") or "{}")
                files = plan.setdefault("files", {})
                for kind in ("resume", "cover_letter"):
                    new = (r.get("pdf") or {}).get(kind) or r.get(kind)
                    if kind in files and new and Path(new).exists() and files[kind] != new:
                        files[kind] = new  # the next fill or submit uploads the remade file
                db.update(jid, plan_json=plan)
                with self._lock:
                    self._st["done"] += 1
            except Exception:
                with self._lock:
                    self._st["failed"].append(company)
        with self._lock:
            self._st["running"] = False
