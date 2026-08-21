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
from propertyfinder.migrations import Migration, discover, ordered
from propertyfinder.timeutil import utc_now_iso

# The version stamp itself, which no migration may create because every migration is
# recorded in it. Created before the first step runs and never altered afterwards.
_SCHEMA_VERSION_DDL = """
CREATE TABLE IF NOT EXISTS schema_version (
    version    INTEGER PRIMARY KEY,
    name       TEXT NOT NULL,
    applied_ts TEXT NOT NULL
)
"""

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


def run_migrations(
    engine: Engine, migrations: list[Migration] | None = None
) -> list[Migration]:
    """Bring a database up to the current schema. Returns the steps it actually ran.

    Idempotent by construction: applied versions are read from `schema_version` and
    skipped, so this is safe to call on every command — a user should never have to know
    whether their database is current, and a cron job certainly should not.

    Each step is stamped only once it returns, so a failure part-way through a series
    leaves the earlier steps applied and recorded and the failing one unrecorded — the
    next run retries exactly it. (It retries rather than rolls back: Python's SQLite
    driver commits schema statements implicitly, which is why migrations are required to
    be safe to run twice. See `propertyfinder.migrations`.)
    """
    steps = discover() if migrations is None else ordered(migrations)
    with engine.begin() as conn:
        conn.execute(text(_SCHEMA_VERSION_DDL))
        done = {row[0] for row in conn.execute(text("SELECT version FROM schema_version"))}

    applied: list[Migration] = []
    for step in steps:
        if step.version in done:
            continue
        with engine.begin() as conn:
            step.apply(conn)
            conn.execute(
                text(
                    "INSERT INTO schema_version (version, name, applied_ts) "
                    "VALUES (:version, :name, :ts)"
                ),
                {"version": step.version, "name": step.name, "ts": utc_now_iso()},
            )
        applied.append(step)
    return applied


def schema_version(engine: Engine) -> int:
    """The highest migration this database has applied; 0 if it has applied none."""
    with engine.begin() as conn:
        conn.execute(text(_SCHEMA_VERSION_DDL))
        row = conn.execute(text("SELECT MAX(version) FROM schema_version")).scalar()
    return int(row or 0)


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


# -- what a sweep changed, read back out of history ------------------------------------
#
# `run_sweep` computes this diff live, against listings it just collected, and reports it
# once, on the spot. But the exact same comparison is recoverable from the database alone
# any time afterwards: the two most recent sweeps a watch has on record are, respectively,
# precisely the "found" and the "baseline" a live diff would have used. `sweep_changes` is
# that comparison, reusable by a report built long after the sweep that produced it.


def sweep_changes(session: Session, watch_name: str) -> dict:
    """New, cut, raised, status-flipped, and gone since the sweep before the latest one.

    A database holding fewer than two sweeps of this watch has nothing to compare against
    — every bucket comes back empty and `history_began` is False, which is a caller's cue
    to say "history begins today" rather than print a diff that would otherwise look
    identical to "a second sweep changed nothing at all".
    """
    rows = latest_snapshot_rows(session, watch_name)
    address_by_zpid = {r["zpid"]: r["address"] for r in rows}
    latest_ts = max((r["snapshot_ts"] for r in rows), default=None)

    empty = {
        "new": [],
        "cuts": [],
        "rises": [],
        "status_changes": [],
        "gone": [],
        "history_began": False,
    }
    if latest_ts is None:
        return empty

    baseline = previous_snapshot_map(session, watch_name, latest_ts)
    if not baseline:
        return empty  # one sweep on record: nothing precedes it to diff against

    found = {r["zpid"]: r for r in rows if r["snapshot_ts"] == latest_ts}
    new: list[dict] = []
    cuts: list[dict] = []
    rises: list[dict] = []
    flips: list[dict] = []

    for zpid, row in found.items():
        was = baseline.get(zpid)
        if was is None:
            new.append({"zpid": zpid, "address": row["address"], "price": row["price"]})
            continue
        if row["price"] and was["price"] and row["price"] != was["price"]:
            move = {
                "zpid": zpid,
                "address": row["address"],
                "previous": was["price"],
                "current": row["price"],
                "delta": row["price"] - was["price"],
                "since": was["snapshot_ts"],
            }
            (cuts if move["delta"] < 0 else rises).append(move)
        if row["status_text"] and row["status_text"] != was["status_text"]:
            flips.append(
                {
                    "zpid": zpid,
                    "address": row["address"],
                    "previous": was["status_text"],
                    "current": row["status_text"],
                    "since": was["snapshot_ts"],
                }
            )

    # "Gone" is scoped the same way run_sweep scopes it: only a home last seen in the
    # sweep immediately preceding this one can have just left. A home that sold in some
    # earlier sweep and has been absent ever since must not be reported gone again today.
    last_sweep_ts = max((r["snapshot_ts"] for r in baseline.values()), default=None)
    gone = [
        {"zpid": zpid, "address": address_by_zpid.get(zpid), "price": row["price"]}
        for zpid, row in baseline.items()
        if row["snapshot_ts"] == last_sweep_ts and zpid not in found
    ]

    new.sort(key=lambda d: (d["address"] or "", d["zpid"]))
    cuts.sort(key=lambda d: d["delta"])  # the biggest cut first
    rises.sort(key=lambda d: -d["delta"])  # the biggest rise first
    flips.sort(key=lambda d: (d["address"] or "", d["zpid"]))
    gone.sort(key=lambda d: (d["address"] or "", d["zpid"]))

    return {
        "new": new,
        "cuts": cuts,
        "rises": rises,
        "status_changes": flips,
        "gone": gone,
        "history_began": True,
    }


def price_change_map(session: Session, watch_name: str) -> dict[str, dict]:
    """Per home: the very first ask this watch ever recorded, versus the latest.

    Cumulative, not per-event — two cuts and a rise net out to one number, which is the
    honest answer to "how has this home moved since we first saw it" and the figure that
    turned out to be the single most persuasive one in every report the original tool
    shipped ("cut $83,000 since July"). `cut_dollars` is positive when the ask has fallen
    and negative when it has risen — a home that has only ever risen still belongs here,
    it just reads as a negative cut.

    A home whose price has never changed is absent from the map entirely. There is
    nothing to say about it, so nothing is returned, rather than a zero every caller would
    have to remember to hide.
    """
    first_rows = session.execute(
        text(
            """
            WITH ranked AS (
              SELECT zpid, price, ROW_NUMBER() OVER (
                       PARTITION BY zpid
                       ORDER BY snapshot_ts ASC, snapshot_id ASC) AS rn
              FROM snapshots
              WHERE watch_name = :watch AND price IS NOT NULL
            )
            SELECT zpid, price FROM ranked WHERE rn = 1
            """
        ),
        {"watch": watch_name},
    ).mappings().all()
    first_price = {r["zpid"]: r["price"] for r in first_rows}
    latest_price = {r["zpid"]: r["price"] for r in latest_snapshot_rows(session, watch_name)}

    changes: dict[str, dict] = {}
    for zpid, first in first_price.items():
        last = latest_price.get(zpid)
        if last is None or last == first:
            continue
        cut_dollars = first - last
        changes[zpid] = {
            "first": first,
            "last": last,
            "cut_dollars": cut_dollars,
            "cut_pct": (cut_dollars / first * 100) if first else None,
        }
    return changes


# -- the observed window ----------------------------------------------------------------
#
# Everything above answers "what is true now" or "what changed since last time". A page that
# quotes a *rate* — homes absorbed per month, months of supply — needs a third question
# answered: how long have we been watching, and what did each home do across that whole
# span. Measured between the last two sweeps, absorption at Walsh found one home in fourteen
# days and put months-of-supply at 18.2 — a headline number resting on a sample of one.
# Across the full observed history it was 13.3 on a sample of four (docs/PORTING-THE-REPORTS.md,
# lesson 7). Neither window is right in general; the fix is to make the long one available and
# to put the window itself on the page, which needs these two queries.


def sweep_timestamps(session: Session, watch_name: str) -> list[str]:
    """Every sweep this watch has on record, oldest first.

    The count of these is `n_sweeps` and the ends of the list are the observed window. Both
    belong in a payload rather than in a template's prose, so that a sentence saying how long
    the tool has been watching cannot disagree with how long it has been watching.
    """
    rows = session.execute(
        text(
            "SELECT DISTINCT snapshot_ts FROM snapshots WHERE watch_name = :watch "
            "ORDER BY snapshot_ts ASC"
        ),
        {"watch": watch_name},
    ).all()
    return [r[0] for r in rows]


def observation_spans(session: Session, watch_name: str) -> dict[str, dict]:
    """Per home: when this watch first saw it, when it last saw it, and how often.

    `last_ts` is what makes absorption measurable. A home whose last sighting predates the
    most recent sweep has left the market during the observed window — sold, withdrawn, or
    relisted under a new identity, and the feed does not say which, which is why a page
    should call the number "absorbed" and not "sold".
    """
    rows = session.execute(
        text(
            """
            SELECT zpid,
                   MIN(snapshot_ts) AS first_ts,
                   MAX(snapshot_ts) AS last_ts,
                   COUNT(*)         AS n_obs
            FROM snapshots
            WHERE watch_name = :watch
            GROUP BY zpid
            """
        ),
        {"watch": watch_name},
    ).mappings().all()
    return {r["zpid"]: dict(r) for r in rows}
