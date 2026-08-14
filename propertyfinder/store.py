"""Persistence: writing observations down, and reading history back out.

Nothing in this module decides anything. Its entire job is that what a sweep found
outlives the process that found it, and that every later layer can ask its two questions
cheaply — what is true now, and what was true before.

The one rule with teeth: identity is **backfilled, never overwritten by absence**. The
feed omits square footage on one sighting and supplies it on the next, and a later
"unknown" that erased an earlier fact would corrupt the comparison the tool exists to
make.
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from propertyfinder.adapters import Listing
from propertyfinder.domain import PropertySnapshot, WatchedProperty

# Identity fields a later sighting may correct or fill in. Deliberately excludes
# first_seen (a fact about the past, fixed once) and zpid (the identity itself).
_REFRESHABLE = (
    "address",
    "home_type",
    "beds",
    "baths",
    "sqft",
    "lot_sqft",
    "lat",
    "lon",
    "link",
    "image_url",
    "date_sold",
)


def session_factory(engine: Engine) -> sessionmaker:
    """Sessions that survive their own commit.

    `expire_on_commit=False` so a caller may read the rows it just wrote without the ORM
    firing a fresh query per attribute — a sweep commits once and then reports on what it
    stored, which would otherwise be hundreds of pointless round trips.
    """
    return sessionmaker(bind=engine, expire_on_commit=False)


def upsert_property(session: Session, listing: Listing, now: str) -> WatchedProperty:
    """Insert the home if it is new; otherwise refresh what the feed now knows.

    Returns the identity row, flushed or not — the caller decides when the write lands.
    """
    row = session.get(WatchedProperty, listing.zpid)
    if row is None:
        row = WatchedProperty(zpid=listing.zpid, first_seen=now, last_seen=now)
        for attr in _REFRESHABLE:
            setattr(row, attr, getattr(listing, attr))
        session.add(row)
        return row

    row.last_seen = now
    for attr in _REFRESHABLE:
        value = getattr(listing, attr)
        if value is not None:  # absence never overwrites a fact we already hold
            setattr(row, attr, value)
    return row


def record_snapshot(
    session: Session,
    listing: Listing,
    watch_name: str,
    snapshot_ts: str,
    distance_miles: float | None = None,
    listing_status: str | None = None,
) -> PropertySnapshot:
    """Write down one observation. Never an update — a sweep only ever adds.

    `listing_status` is what the watch asked the provider for, which is more trustworthy
    than the row's own word; absent that, the listing's translated status stands.
    """
    row = PropertySnapshot(
        zpid=listing.zpid,
        watch_name=watch_name,
        snapshot_ts=snapshot_ts,
        listing_status=listing_status or listing.listing_status,
        price=listing.price,
        zestimate=listing.zestimate,
        rent_zestimate=listing.rent_zestimate,
        tax_assessed_value=listing.tax_assessed_value,
        days_on_zillow=listing.days_on_zillow,
        status_text=listing.status_text,
        distance_miles=distance_miles,
    )
    session.add(row)
    return row


# -- the two questions ----------------------------------------------------------------
#
# What is true now, and what was true before. Everything downstream — the report, the
# movement strip, the diff a sweep prints, the calibration loop — is one of these two
# queries with something layered on top. Both are deliberately plain SQL returning plain
# dictionaries: no ORM objects escape into the reporting layers, so nothing up there can
# accidentally hold a live session open or write through a read.


def latest_snapshot_rows(session: Session, watch_name: str) -> list[dict]:
    """The newest observation of each home in a watch, joined to its identity.

    This is what a report renders. `ROW_NUMBER() OVER (PARTITION BY zpid ORDER BY
    snapshot_ts DESC)` is the "latest per home" idiom the whole tool leans on, and it is
    correct on a text column only because timestamps are fixed-width UTC.
    """
    rows = session.execute(
        text(
            """
            WITH ranked AS (
              SELECT s.*, ROW_NUMBER() OVER (
                       PARTITION BY s.zpid
                       ORDER BY s.snapshot_ts DESC, s.snapshot_id DESC) AS rn
              FROM snapshots s
              WHERE s.watch_name = :watch
            )
            SELECT r.zpid, r.snapshot_ts, r.listing_status, r.price, r.zestimate,
                   r.rent_zestimate, r.tax_assessed_value, r.days_on_zillow,
                   r.status_text, r.distance_miles,
                   p.address, p.home_type, p.beds, p.baths, p.sqft, p.lot_sqft,
                   p.lat, p.lon, p.link, p.image_url, p.date_sold,
                   p.first_seen, p.last_seen
            FROM ranked r JOIN properties p ON p.zpid = r.zpid
            WHERE r.rn = 1
            ORDER BY r.zpid
            """
        ),
        {"watch": watch_name},
    ).mappings().all()
    return [dict(r) for r in rows]


def previous_snapshot_map(
    session: Session, watch_name: str, before_ts: str | None = None
) -> dict[str, dict]:
    """The baseline a sweep is diffed against: newest observation per home *before* now.

    `before_ts` is exclusive, and it is what makes the diff honest. A sweep computes its
    baseline with its own timestamp, so the observations it is about to write cannot be
    compared against themselves — otherwise every home would look unchanged, which is the
    one answer that is never interesting. Omit it and the function simply means "the
    latest per home", which is what a caller outside a sweep wants.
    """
    clause = "AND snapshot_ts < :before" if before_ts is not None else ""
    params = {"watch": watch_name} | ({"before": before_ts} if before_ts else {})
    rows = session.execute(
        text(
            f"""
            WITH ranked AS (
              SELECT zpid, price, listing_status, status_text, days_on_zillow,
                     snapshot_ts,
                     ROW_NUMBER() OVER (
                       PARTITION BY zpid
                       ORDER BY snapshot_ts DESC, snapshot_id DESC) AS rn
              FROM snapshots
              WHERE watch_name = :watch {clause}
            )
            SELECT zpid, price, listing_status, status_text, days_on_zillow, snapshot_ts
            FROM ranked WHERE rn = 1
            """
        ),
        params,
    ).mappings().all()
    return {r["zpid"]: dict(r) for r in rows}
