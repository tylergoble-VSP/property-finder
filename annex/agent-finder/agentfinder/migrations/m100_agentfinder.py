"""100 — agents, the append-only attribution log, and the free luxury extras.

The annex's first step, and the first of the reserved 100+ range. Hand-written SQL per the
migrations-package rule (never bake today's mapped classes into a step that must run for
years). FKs point at core's `properties(zpid)`: the annex depends on core's identity table
and is meaningless without it, so a foreign key that fails loudly is correct.

Three tables, split by kind the same way core splits properties from snapshots:
- `agents` is IDENTITY — one row per person, slowly-changing, backfilled never overwritten.
- `listing_attributions` is OBSERVATION — one row per resolution ATTEMPT, append-only; the
  current answer for a home is the latest row (a re-resolution is a new row, not an edit).
- `listing_extras` is per-listing enrichment the feed gives for free but core's Listing seam
  does not carry (is_showcase, the photo array, builder name). Absence means unknown.
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Connection

# IDENTITY. agent_key is our slug (folded first|last); the TREC licence is the only real
# identity and is backfilled onto the row when discovered, never overwritten by a later null.
_AGENTS = """
CREATE TABLE IF NOT EXISTS agents (
    agent_key     TEXT PRIMARY KEY,
    full_name     TEXT NOT NULL,
    licence       TEXT,
    phone         TEXT,
    brokerage     TEXT,
    trec_status   TEXT,
    profile_url   TEXT,
    first_seen    TEXT NOT NULL,
    last_seen     TEXT NOT NULL
)
"""

# OBSERVATION, append-only. Every attempt writes a row (even UNRESOLVED), which is both the
# audit trail and the attempt-stamp that stops a source being re-asked inside a stale window
# (enrich.py's rule). Current attribution for a home = latest row by attempted_ts.
_ATTRIBUTIONS = """
CREATE TABLE IF NOT EXISTS listing_attributions (
    attribution_id INTEGER PRIMARY KEY,
    zpid          TEXT NOT NULL REFERENCES properties(zpid),
    watch_name    TEXT NOT NULL,
    attempted_ts  TEXT NOT NULL,
    tier          TEXT NOT NULL,          -- CONFIRMED | INFERRED | UNRESOLVED
    agent_key     TEXT REFERENCES agents(agent_key),   -- NULL when the individual is unknown
    agent_name    TEXT,
    licence       TEXT,
    phone         TEXT,
    brokerage     TEXT,
    trec_status   TEXT,
    method        TEXT NOT NULL,          -- 'google_listed_by' | 'manual'
    sources       TEXT,                   -- the domains the claim rests on
    evidence      TEXT,
    reason        TEXT,                   -- why not better than it is; set when tier != CONFIRMED
    co_listers    TEXT,                   -- JSON array of co-lister names, when any
    CONSTRAINT uq_attribution UNIQUE (zpid, watch_name, attempted_ts)
)
"""
_ATTR_LATEST = """
CREATE INDEX IF NOT EXISTS ix_attr_latest ON listing_attributions (zpid, attempted_ts)
"""
_ATTR_BY_AGENT = """
CREATE INDEX IF NOT EXISTS ix_attr_agent ON listing_attributions (agent_key, watch_name)
"""

# Free luxury signals the search feed carries but core's Listing drops. One row per home per
# sweep; every column nullable and conditionally present — absence is unknown, never False.
_EXTRAS = """
CREATE TABLE IF NOT EXISTS listing_extras (
    extra_id              INTEGER PRIMARY KEY,
    zpid                  TEXT NOT NULL REFERENCES properties(zpid),
    watch_name            TEXT NOT NULL,
    observed_ts           TEXT NOT NULL,
    is_showcase           INTEGER,
    is_fsba               INTEGER,
    has_3d_model          INTEGER,
    builder_name          TEXT,
    new_construction_type TEXT,
    price_reduction       REAL,
    images_json           TEXT,
    CONSTRAINT uq_extra UNIQUE (zpid, watch_name, observed_ts)
)
"""


def apply(conn: Connection) -> None:
    for stmt in (_AGENTS, _ATTRIBUTIONS, _ATTR_LATEST, _ATTR_BY_AGENT, _EXTRAS):
        conn.execute(text(stmt))
