"""Finish it here: the form is filled in a browser on your server (the browser box), and you finish it from the job page.

One session at a time. A worker thread owns Playwright (its sync API is single-threaded): it connects to the box over
the DevTools protocol, runs the same filler `autoapply-finish` runs, and watches for the site's confirmation page.
Paste and Close are queued and run by that thread on its next poll. A session ends when you press Done, after
IDLE_S without input from the viewer, AFTER_SENT_S after the confirmation, or when the browser goes away. It never
presses Submit.
"""
from __future__ import annotations

import os
import queue
import socket
import threading
import time
from dataclasses import dataclass

import httpx

IDLE_S = 20 * 60
AFTER_SENT_S = 3 * 60
LIVE = ("starting", "filling", "ready", "sent")
LOST = "We lost the browser. Try again in a moment."
VERSION_FILE = "file:///etc/jobsearchpal-finish-version"


@dataclass
class Box:
    host: str
    cdp_port: int = 9222
    vnc_port: int = 5900


def box_from(settings) -> Box | None:
    """The configured box, or None when Finish here is off (and always on the public demo)."""
    if os.environ.get("AUTOAPPLY_DEMO"):
        return None
    f = (settings.raw or {}).get("finish") or {}
    if not f.get("on_server"):
        return None
    return Box(str(f.get("host") or "127.0.0.1"), int(f.get("cdp_port") or 9222), int(f.get("vnc_port") or 5900))


def cdp_url(box: Box) -> str:
    # Chromium refuses a Host header that is a name other than localhost, so a Docker service name goes in as its IP.
    return f"http://{socket.gethostbyname(box.host)}:{box.cdp_port}"


def health(box: Box, timeout: float = 2.0, with_version: bool = True) -> dict:
    try:
        r = httpx.get(f"{cdp_url(box)}/json/version", timeout=timeout)
        r.raise_for_status()
    except (httpx.HTTPError, OSError) as e:
        return {"ok": False, "version": "", "error": str(e) or type(e).__name__}
    version = ""
    if with_version:
        from playwright.sync_api import sync_playwright
        try:
            with sync_playwright() as p:
                b = p.chromium.connect_over_cdp(cdp_url(box), timeout=timeout * 1000)
                ctx = b.new_context()
                try:
                    page = ctx.new_page()
                    page.goto(VERSION_FILE, timeout=timeout * 1000)
                    version = page.inner_text("body").strip()
                except Exception:
                    version = ""  # a plain Chromium, not the box
                finally:
                    ctx.close()
                    b.close()
        except Exception as e:
            return {"ok": False, "version": "", "error": str(e).splitlines()[0]}
    return {"ok": True, "version": version, "error": ""}


class Sessions:
    def __init__(self, box: Box | None):
        self.box = box
        self._lock = threading.Lock()
        self._cmds: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._st = {"job_id": "", "state": "closed", "unanswered": [], "error": "", "started_at": 0.0, "last_input": 0.0}

    def status(self) -> dict:
        with self._lock:
            return {**self._st, "unanswered": list(self._st["unanswered"])}

    def _set(self, **kw):
        with self._lock:
            self._st.update(kw)

    def start(self, job_id: str, plan: dict, profile: dict, mark_sent) -> dict:
        with self._lock:
            if self._thread is not None and self._thread.is_alive() and self._st["state"] in LIVE:
                return {"busy": self._st["job_id"]}
            now = time.time()
            self._cmds = queue.Queue()
            self._st = {"job_id": job_id, "state": "starting", "unanswered": [], "error": "", "started_at": now,
                        "last_input": now}
            self._thread = threading.Thread(target=self._run, args=(job_id, plan, profile, mark_sent, self._cmds),
                                            daemon=True, name=f"finish-here {job_id}")
            self._thread.start()
        return {"started": True}

    def touch(self):
        self._set(last_input=time.time())

    def paste(self, text: str) -> bool:
        if self.status()["state"] not in ("ready", "sent") or not text:
            return False
        self._cmds.put(("paste", text))
        self.touch()
        return True

    def close(self) -> None:
        self._cmds.put(("close", None))

    def _run(self, job_id, plan, profile, mark_sent, cmds: queue.Queue):
        from playwright.sync_api import sync_playwright
        from .fillers.base import handoff
        sent_at = [0.0]
        page_ref = []
        ended = [""]

        def on_page(page, report):
            if report.closed:  # the company took the posting down: say so, and nothing to finish
                ended[0] = "posting"
                self._set(state="error", error=report.errors[0]["error"], gone=True)
                return
            page_ref.append(page)
            page.bring_to_front()
            self._set(state="ready", unanswered=[u["label"] for u in report.unanswered], last_input=time.time())  # idle counts from here

        def on_sent(confirmation: str):
            mark_sent(job_id, confirmation)
            sent_at[0] = time.time()
            self._set(state="sent")

        def tick() -> bool:
            while True:
                try:
                    kind, arg = cmds.get_nowait()
                except queue.Empty:
                    break
                if kind == "close":
                    ended[0] = "closed"
                    return False
                if kind == "paste" and page_ref:
                    page_ref[0].keyboard.insert_text(arg)
            now = time.time()
            if now - self.status()["last_input"] > IDLE_S:
                ended[0] = "closed"
                return False
            if sent_at[0] and now - sent_at[0] > AFTER_SENT_S:
                return False
            if page_ref and page_ref[0].is_closed():
                ended[0] = "closed"
                return False
            return True

        try:
            with sync_playwright() as p:
                browser = p.chromium.connect_over_cdp(cdp_url(self.box), timeout=15000)
                self._set(state="filling")
                try:
                    handoff(plan, profile, browser=browser, on_page=on_page, on_sent=on_sent, tick=tick)
                finally:
                    try:
                        browser.close()  # a CDP connection: this disconnects, the box's browser keeps running
                    except Exception:
                        pass
            if ended[0] == "posting":
                return  # state is already the closed-posting error
            if not page_ref:
                raise RuntimeError("the form never opened")
            if not browser.is_connected() and not ended[0] and not sent_at[0]:
                raise RuntimeError("disconnected")
            self._set(state="sent" if sent_at[0] and not ended[0] else "closed")
        except Exception:
            self._set(state="error", error=LOST)
