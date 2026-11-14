"""Job links on a company careers page, read in a real browser.

Many careers pages draw their job list with a script, often inside an embedded job board (Paycom, iCIMS, UKG,
Dayforce...). A plain page fetch (WebFetch, curl) sees an empty "Open roles" section there, so the lookup step
would call a live role gone (Bamboo Insurance on Paycom, 11/14). This opens the page in headless Chromium, also
opens each embedded board (lazy-loaded iframes too), lets their scripts run, and lists the links that look like jobs.
"""
from __future__ import annotations

import os
import re
from html import unescape
from urllib.parse import urljoin, urlparse

# Link text that is site furniture, not a job title.
_JUNK = re.compile(r"^(home|about( us)?|apply( now)?|open roles?|careers?|jobs?|view (all )?(jobs|roles|openings)|"
                   r"see (all )?(jobs|roles|openings)|log ?in|sign ?in|sign ?up|create account|contact( us)?|blog|news|"
                   r"privacy( policy)?|terms( of use| of service)?|cookie.*|accept.*|share|search|next|previous|back|"
                   r"show more|load more|read more|learn more|linkedin|facebook|instagram|twitter|x|youtube|email us|"
                   r"join the talent community|talent community|benefits|culture|our team)$", re.I)
_SOCIAL = re.compile(r"(^|\.)(linkedin|facebook|instagram|twitter|x|youtube|tiktok)\.com$")

# Frames that are never a job board: consent banners, chat, video, maps, analytics.
_NOT_A_BOARD = re.compile(r"cookie|consent|onetrust|recaptcha|hcaptcha|youtube|vimeo|google\.com/maps|"
                          r"googletagmanager|doubleclick|hubspot|intercom|drift|zendesk|facebook|twitter", re.I)

_LINKS_JS = """() => [...document.querySelectorAll('a[href]')].map(a => ({
  text: (a.innerText || a.textContent || '').trim(), href: a.href }))"""
_FRAMES_JS = """() => [...document.querySelectorAll('iframe')].map(f =>
  f.getAttribute('data-lazy-src') || f.getAttribute('data-src') || f.getAttribute('src') || '')"""


def _title(text: str) -> str:
    """A job card's first line is its title ("Senior Analytics Engineer\\nFull Time\\nFully Remote...")."""
    return next((l.strip() for l in text.splitlines() if l.strip()), "")


def _looks_like_job(title: str, href: str) -> bool:
    if not href.startswith("http") or _SOCIAL.search(urlparse(href).hostname or ""):
        return False
    if not (4 <= len(title) <= 120) or len(title.split()) > 14 or _JUNK.match(title.strip(" .:|>»→")):
        return False
    return True


def jobs_on_page(url: str, title: str = "", limit: int = 60) -> dict:
    """{"page", "embedded": [board urls], "jobs": [{"title", "url", "board"?, "match"?}]}, the asked title's best match first."""
    from playwright.sync_api import sync_playwright
    from .leads import same_title
    out = {"page": url, "embedded": [], "jobs": []}
    seen: set[str] = set()

    def collect(page, board: bool = False):
        for l in page.evaluate(_LINKS_JS):
            t, h = _title(l["text"]), l["href"].split("#")[0]
            if h and h not in seen and _looks_like_job(t, h):
                seen.add(h)
                out["jobs"].append({"title": t, "url": h, **({"board": True} if board else {})})

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=os.environ.get("AUTOAPPLY_CHROMIUM") or None)
        page = browser.new_context(viewport={"width": 1280, "height": 1600}).new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            _settle(page)
            seen.add(url.split("#")[0])
            frames = [urljoin(page.url, unescape(s)) for s in page.evaluate(_FRAMES_JS)]
            out["embedded"] = [f for f in dict.fromkeys(frames) if f.startswith("http") and f.split("#")[0] != page.url.split("#")[0]
                               and not _NOT_A_BOARD.search(f)][:3]
            collect(page)
            # Each embedded board on its own page: its scripts run there even when the parent page lazy-loads the
            # iframe on scroll, and a board that blocks framing still shows.
            for f in out["embedded"]:
                try:
                    page.goto(f, wait_until="domcontentloaded", timeout=45000)
                    _settle(page)
                    collect(page, board=True)
                except Exception:
                    continue
        finally:
            browser.close()
    # The embedded board's links before the site's own menus and footers; the asked title's best match first.
    for j in out["jobs"]:
        if title:
            j["match"] = round(same_title(title, j["title"]), 2)
    out["jobs"].sort(key=lambda j: (-j.get("match", 0), not j.get("board")))
    out["jobs"] = out["jobs"][:limit]
    return out


def _settle(page, most_ms: int = 20000):
    """Wait until the job list stops growing. Boards fetch it from an API and draw it a few seconds after load
    (Paycom took ~5 s and drew 3 jobs before the other 5), so "network idle" alone stops too early."""
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass
    last, steady, waited = -1, 0, 0
    while waited < most_ms and steady < 3:
        page.wait_for_timeout(1000)
        waited += 1000
        n = page.evaluate("() => document.querySelectorAll('a[href]').length")
        steady = steady + 1 if n == last else 0
        last = n
