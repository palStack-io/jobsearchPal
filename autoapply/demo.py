"""Public demo mode (AUTOAPPLY_DEMO=1): a hosted copy anyone can click around in, with fictional data.

In demo mode nobody signs in, and the dashboard never reaches outside itself: no agent run, no browser, no
email, no fetching a pasted link, no credentials. Only the form posts below are allowed; they write to the
demo's own database, which the host throws away and rebuilds on a schedule (see docs/demo.md).
Never set this on a real install: it turns sign-in off.
"""
from __future__ import annotations

import html
import os
import re
import time
from urllib.parse import quote, urlsplit

ON = os.environ.get("AUTOAPPLY_DEMO") == "1"

# Form posts a visitor may make. Everything here only changes the local database or config.
ALLOWED_POSTS = re.compile(
    r"^/(?:applications/log|pal/seen|decline/.+|nudge/[^/]+|plan|plan/day|plan/note|contacts|reflect|letters|setback"
    r"|titles/add|leads|leads/add|leads/paste|lead/.+/description|skills/mark|emails/.+|dua/claim|dua/task|job/.+)$"
)
# Job page actions that only change the database; "refill" and "submit" open a real browser on a real site.
ALLOWED_JOB_ACTIONS = {"skip", "manual", "command", "unsend"}
NO_PAGES = ("/login", "/logout", "/account")
MAX_BODY = 64 * 1024        # bytes; the biggest real form here is a pasted job post
POSTS_PER_MINUTE = 30       # per visitor


def post_allowed(path: str) -> bool:
    return bool(ALLOWED_POSTS.match(path)) and not path.endswith("/run")


def visitor(headers, client_host: str | None) -> str:
    """Behind Cloudflare, CF-Connecting-IP is the visitor; locally it is the socket's address."""
    return (headers.get("cf-connecting-ip") or client_host or "?")[:64]


class Limiter:
    """Fixed one-minute window per visitor, kept in memory and capped in size."""

    def __init__(self, limit: int = POSTS_PER_MINUTE, window: float = 60.0, max_keys: int = 10000):
        self.limit, self.window, self.max_keys = limit, window, max_keys
        self.hits: dict[str, tuple[float, int]] = {}

    def take(self, key: str) -> bool:
        now = time.monotonic()
        start, n = self.hits.get(key, (now, 0))
        if now - start >= self.window:
            start, n = now, 0
        if n >= self.limit:
            return False
        if key not in self.hits and len(self.hits) >= self.max_keys:
            self.hits = {k: v for k, v in self.hits.items() if now - v[0] < self.window}
            if len(self.hits) >= self.max_keys:
                return False
        self.hits[key] = (start, n + 1)
        return True


def back_from_referer(referer: str, host: str) -> str:
    """Only a path on this same site; anything else goes back to the dashboard."""
    try:
        u = urlsplit(referer or "")
    except ValueError:
        return "/"
    return u.path if u.netloc == host and u.path.startswith("/") else "/"


def off_page(back: str = "/") -> str:
    back = back if back.startswith("/") and not back.startswith("//") and "\\" not in back else "/"
    back = html.escape(quote(back, safe="/:"), quote=True)
    return (
        "<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
        "<title>Not in the demo</title><body style=\"font:16px/1.6 system-ui,sans-serif;max-width:34rem;margin:12vh auto;"
        "padding:0 20px;color:#1d2b28\"><h1 style=\"font-size:1.4rem\">That part is off in the demo</h1>"
        "<p>In your own copy this would run the agent, open a job site, read your email or save a sign-in. "
        "The demo has fictional data and never reaches outside itself.</p>"
        f"<p><a href=\"{back}\" style=\"color:#0f6e66\">Back</a> · "
        "<a href=\"https://github.com/palStack-io/jobsearchPal\" style=\"color:#0f6e66\">Get jobsearchPal on GitHub</a></p>"
    )


OFF_TITLE = "That part is off in the demo"
OFF_DETAIL = ("In your own copy this would run the agent, open a job site, read your email or save a sign-in. "
              "The demo has fictional data and never reaches outside itself.")


def off_json() -> dict:
    """For the dashboard's in-page posts (X-In-Page), which show title and detail in a dialog."""
    return {"title": OFF_TITLE, "detail": OFF_DETAIL}
