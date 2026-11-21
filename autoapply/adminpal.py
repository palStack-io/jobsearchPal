"""palStack connection: "Report a problem" and adminPal's Home totals, for installs palStack runs.

OFF unless all three are set in the environment (.env), which only installs connected to palStack have:
    PALSTACK_MANAGER_URL      adminPal's origin, no path
    PALSTACK_MANAGER_SECRET   the shared secret adminPal holds for this install
    PALSTACK_PRODUCT_ID       optional, default "jobsearchpal"
Off, there are no routes, no button and nothing leaves the install. On, the person's own report (sent on a tick)
goes to the palStack team, and adminPal can ask for the install's totals. There is no automatic error reporting:
the README promises nothing is sent except to the models the person turns on, and reports are sent knowingly.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, Request

from .palstack_admin_kit.config import KitConfig, KitConfigError
from .palstack_admin_kit.stats import stats_v2

log = logging.getLogger(__name__)
PRODUCT = "jobsearchpal"
STATS_PATH = "/api/admin/stats"  # the one path the sign-in gate lets through; the kit checks adminPal's signature
OWNER = "owner"  # one account per install: the reporter is that account, never its email


def config() -> KitConfig | None:
    try:
        return KitConfig.from_env(secret_var="PALSTACK_MANAGER_SECRET", product_default=PRODUCT)
    except KitConfigError:
        return None


def counts(settings, db) -> dict:
    """Totals for adminPal's Home. One person per install, so: people is 1 once the account exists, active means
    the person pressed Submit in the last 30 days, and no cards (any count would be one person's activity)."""
    from . import auth
    people = 1 if auth.load(settings.data_dir) else 0
    since = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d")
    row = db.conn.execute("select count(*) from jobs where submitted_at >= ?", (since,)).fetchone()
    active = 1 if people and row and row[0] else 0
    return stats_v2(people=people, active_30d=active, new_30d=None, paying=None)


def install(app: FastAPI, settings, db, app_version: str | None) -> KitConfig | None:
    """Adds POST /api/support/report and GET /api/admin/stats when connected; returns the config or None."""
    cfg = config()
    if not cfg:
        return None
    # Imported only when connected: it needs `requests`, which an install updated by `git pull` alone may not have.
    from . import auth
    from .palstack_admin_kit.fastapi_adapter import report_router, stats_router

    def owner(request: Request) -> str | None:
        if auth.valid_cookie(auth.load(settings.data_dir), request.cookies.get(auth.COOKIE)):
            return OWNER
        return None

    router, _kit = report_router(cfg, user_ref_dependency=owner, app_root=str(Path(__file__).resolve().parent),
                                 app_version=app_version or None)
    app.include_router(router, prefix="/api")
    app.include_router(stats_router(cfg, lambda: counts(settings, db)), prefix="/api")
    log.info("palStack connection on: Report a problem and adminPal totals (%s)", cfg.product_id)
    return cfg

