"""Persistence for the annex — base listings in core's tables, agents/attribution in ours.

The 25-mile luxury discovery sweep writes into core's `properties`/`snapshots` via core's own
`upsert_property`/`record_snapshot` (identity backfilled, observation append-only, parents
flushed before children — core's rules, reused not reimplemented). Only what core has no
concept of lives in annex tables: who listed a home, and the free luxury extras.

The current attribution for a home is the latest row by `attempted_ts` — the same
"latest-per-key" window idiom the whole tool leans on, here over `listing_attributions`.
"""
from __future__ import annotations

import json

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from propertyfinder.store import (
    latest_snapshot_rows,
    record_snapshot,
    run_migrations,
    upsert_property,
)

from agentfinder import migrations as annex_migrations
from agentfinder.attribution import AgentAttribution, _norm
from agentfinder.trec import TrecCheck


def migrate(engine: Engine) -> None:
    """Bring the shared database up to date: core's steps (1–99), then the annex's (100+).

    Two calls against the one `schema_version` table. Idempotent — safe on every command, so
    nobody has to know whether their database is current."""
    run_migrations(engine)
    run_migrations(engine, annex_migrations.discover())


def record_luxury_sweep(
    session: Session,
    rows: list[tuple],          # (Listing, LuxeExtras, distance_miles)
    watch_name: str,
    now: str,
) -> int:
    """Store one luxury sweep: base facts to core's tables, extras to ours. Parents first."""
    listings = [r[0] for r in rows]
    for listing in listings:
        upsert_property(session, listing, now)
    session.flush()  # properties must exist before snapshots/extras reference them
    for listing, extras, distance in rows:
        record_snapshot(session, listing, watch_name, now, distance_miles=distance,
                        listing_status="for_sale")
        session.execute(
            text("""
                INSERT OR IGNORE INTO listing_extras
                  (zpid, watch_name, observed_ts, is_showcase, is_fsba, has_3d_model,
                   builder_name, new_construction_type, price_reduction, images_json)
                VALUES (:zpid,:watch,:ts,:showcase,:fsba,:d3,:builder,:nct,:cut,:images)
            """),
            {"zpid": listing.zpid, "watch": watch_name, "ts": now,
             "showcase": _b(extras.get("is_showcase")), "fsba": _b(extras.get("is_fsba")),
             "d3": _b(extras.get("has_3d_model")), "builder": extras.get("builder_name"),
             "nct": extras.get("new_construction_type"),
             "cut": extras.get("price_reduction"),
             "images": json.dumps(extras.get("images")) if extras.get("images") else None},
        )
    return len(listings)


def _b(v):
    return None if v is None else (1 if v else 0)


def upsert_agent(session: Session, attr: AgentAttribution, trec: TrecCheck | None, now: str):
    """Insert or backfill the agent named by an attribution. Returns the agent_key, or None
    when no individual was named (a brokerage-only or unresolved attribution has no person)."""
    if not attr.agent:
        return None
    key = _norm(attr.agent)
    brokerage = (trec.brokerage if trec and trec.brokerage else attr.brokerage)
    status = trec.status if trec else None
    row = session.execute(text("SELECT agent_key FROM agents WHERE agent_key=:k"),
                          {"k": key}).first()
    if row is None:
        session.execute(text("""
            INSERT INTO agents (agent_key, full_name, licence, phone, brokerage, trec_status,
                                first_seen, last_seen)
            VALUES (:k,:name,:lic,:phone,:broker,:status,:now,:now)
        """), {"k": key, "name": attr.agent, "lic": attr.licence, "phone": attr.phone,
               "broker": brokerage, "status": status, "now": now})
    else:
        # Backfill: a later null never erases a fact we already hold (core's discipline).
        session.execute(text("""
            UPDATE agents SET last_seen=:now,
              full_name=COALESCE(:name, full_name),
              licence=COALESCE(:lic, licence),
              phone=COALESCE(:phone, phone),
              brokerage=COALESCE(:broker, brokerage),
              trec_status=COALESCE(:status, trec_status)
            WHERE agent_key=:k
        """), {"k": key, "now": now, "name": attr.agent, "lic": attr.licence,
               "phone": attr.phone, "broker": brokerage, "status": status})
    return key


def record_attribution(session: Session, zpid: str, watch_name: str,
                       attr: AgentAttribution, trec: TrecCheck | None, now: str) -> None:
    """Append one resolution attempt. Never an update — a re-resolution is a new row."""
    agent_key = upsert_agent(session, attr, trec, now)
    session.execute(text("""
        INSERT OR IGNORE INTO listing_attributions
          (zpid, watch_name, attempted_ts, tier, agent_key, agent_name, licence, phone,
           brokerage, trec_status, method, sources, evidence, reason, co_listers)
        VALUES (:zpid,:watch,:ts,:tier,:key,:name,:lic,:phone,:broker,:status,:method,
                :sources,:evidence,:reason,:co)
    """), {"zpid": zpid, "watch": watch_name, "ts": now, "tier": attr.tier, "key": agent_key,
           "name": attr.agent, "lic": attr.licence, "phone": attr.phone,
           "broker": (trec.brokerage if trec and trec.brokerage else attr.brokerage),
           "status": trec.status if trec else None, "method": "google_listed_by",
           "sources": ",".join(attr.sources) or None, "evidence": attr.evidence,
           "reason": attr.reason,
           "co": json.dumps(list(attr.co_listers)) if attr.co_listers else None})


def current_attribution(session: Session, watch_name: str) -> dict[str, dict]:
    """The newest attribution per home — the same latest-per-key window query as core's
    `latest_snapshot_rows`, over `listing_attributions`."""
    rows = session.execute(text("""
        WITH ranked AS (
          SELECT a.*, ROW_NUMBER() OVER (
                   PARTITION BY zpid ORDER BY attempted_ts DESC, attribution_id DESC) AS rn
          FROM listing_attributions a WHERE watch_name = :watch)
        SELECT * FROM ranked WHERE rn = 1
    """), {"watch": watch_name}).mappings().all()
    return {r["zpid"]: dict(r) for r in rows}


def zpids_needing_attribution(session: Session, watch_name: str) -> list[str]:
    """Luxury homes in this watch with no CONFIRMED/INFERRED attribution yet — the resolve
    work-list. Never-attempted and still-unresolved both qualify; a home already resolved to
    an actionable tier is left alone so quota is not spent relearning it."""
    current = current_attribution(session, watch_name)
    out = []
    for r in latest_snapshot_rows(session, watch_name):
        cur = current.get(r["zpid"])
        if cur is None or cur["tier"] == "UNRESOLVED":
            out.append(r["zpid"])
    return out


def extras_map(session: Session, watch_name: str) -> dict[str, dict]:
    """Latest free-extras row per home in this watch."""
    rows = session.execute(text("""
        WITH ranked AS (
          SELECT e.*, ROW_NUMBER() OVER (
                   PARTITION BY zpid ORDER BY observed_ts DESC, extra_id DESC) AS rn
          FROM listing_extras e WHERE watch_name = :watch)
        SELECT * FROM ranked WHERE rn = 1
    """), {"watch": watch_name}).mappings().all()
    return {r["zpid"]: dict(r) for r in rows}
