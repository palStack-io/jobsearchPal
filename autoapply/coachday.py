"""The coach: one call a morning that reads the person's whole search and says what to do next to get hired.

Python gathers the facts (the digest), one model call with no tools coaches and judges, and Python checks every id,
person, number and name in the reply against the digest before anything is saved. The person sends every message
themselves. Design: docs/superpowers/specs/2026-11-21-coach-design.md.
"""
from __future__ import annotations

import hashlib

SCHEMA = """
create table if not exists coach_day (   -- one validated coach reply a day
  day text primary key, json text, usage_id integer, fallback integer default 0,
  at text default (datetime('now', 'localtime'))
);
create table if not exists outreach (    -- people the coach suggested reaching; the person sends each note
  key text primary key, day text, person text, email text, company text, job_id text, kind text, why text,
  draft_path text, state text default 'suggested', changed text
);
"""

STAGES = ("coach", "drafts")


def ensure(db) -> None:
    db.conn.executescript(SCHEMA)


def enabled(prefs) -> bool:
    return bool(prefs.data.get("coach", False))


def model(prefs, stage: str) -> str:
    """The coach and drafts default to sonnet: the judgement and the voice are the point (Daily run settings)."""
    return prefs.data.get(f"{stage}_model") or "sonnet"


def outreach_key(person: str, kind: str, job_id: str | None) -> str:
    parts = [(person or "").strip().lower(), (kind or "").strip().lower(), job_id or ""]
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:12]
