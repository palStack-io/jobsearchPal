"""Remakes the resume and cover letter of every application still waiting on you, in the look saved on Setup.

Each application keeps the tailored text it was built from (docs.json next to its files), so this only lays the same
tailored words out again on today's base resume: nothing is rewritten and no agent runs. Sent and skipped applications are left as they are. A remade
cover letter carries today's date. Runs in the background: a LibreOffice PDF takes a few seconds per document.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
from pathlib import Path

from . import docs

WAITING = ("filled", "prepped")


def waiting(db) -> list[tuple[str, Path]]:
    """[(job id, its docs.json)] for every unsent application whose tailored text is still on disk."""
    out = []
    for jid, plan_json, report_json in db.conn.execute(
            f"select id, plan_json, report_json from jobs where status in ({','.join('?' * len(WAITING))}) order by id", WAITING):
        try:
            resume = (json.loads(plan_json or "{}").get("files") or {}).get("resume")
            if json.loads(report_json or "{}").get("closed"):
                continue  # the company took it down: nothing to send it with
        except (json.JSONDecodeError, AttributeError):
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
            if (db.get(jid) or {}).get("status") not in WAITING:
                continue  # sent or skipped since the list was made: its files stay as they went out
            scratch = Path(tempfile.mkdtemp(prefix=".remake-", dir=spec_path.parent))
            try:
                spec = json.loads(spec_path.read_text())
                company = spec.get("company") or jid
                if base is None:
                    raise FileNotFoundError("no base resume")
                # Built aside, then swapped in whole: a submit running now never uploads a half-written file.
                r = docs.build(base, spec, scratch, last_name=settings.profile.get("last_name", ""),
                               look=look, accent=accent)
                if r.get("problems"):
                    raise ValueError(r["problems"][0])  # e.g. no PDF, or past the page limit in this look
                plan = json.loads((db.get(jid) or {}).get("plan_json") or "{}")
                files = plan.setdefault("files", {})
                built = {k: Path((r.get("pdf") or {}).get(k) or r[k]) for k in ("resume", "cover_letter") if r.get(k)}
                for kind, old in files.items():
                    if kind in built and Path(old).suffix.lower() != built[kind].suffix.lower():
                        raise ValueError(f"made a {built[kind].suffix} where the form takes a {Path(old).suffix}")
                if (db.get(jid) or {}).get("status") not in WAITING:
                    continue  # sent while this one was being built
                for f in scratch.iterdir():
                    if f.is_file():
                        os.replace(f, spec_path.parent / f.name)
                for kind in built:
                    if kind in files:
                        files[kind] = str(spec_path.parent / built[kind].name)  # the next fill or submit uploads it
                db.update(jid, plan_json=plan)
                with self._lock:
                    self._st["done"] += 1
            except Exception:
                with self._lock:
                    self._st["failed"].append(company)
            finally:
                shutil.rmtree(scratch, ignore_errors=True)
        with self._lock:
            self._st["running"] = False
