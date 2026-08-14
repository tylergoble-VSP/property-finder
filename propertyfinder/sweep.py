"""One watch, one sweep: fan out across its queries, reconcile, remember, compare.

A watch is a place, but the provider does not sell places — it sells answers to search
strings. Several strings are usually needed to cover one circle (a two-mile radius spills
across ZIP boundaries), they overlap, and they disagree at the edges. This module turns
that back into a place: every query is asked, every home is judged against the radius,
a home that two queries both returned is kept once, and what survives is written down in
a single transaction and compared against the last time we looked.

The comparison is the product. Collecting today's listings is what a search engine does;
knowing that this one is thirty-five thousand dollars cheaper than when we saw it in July
is what this tool is for.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from propertyfinder.adapters import Listing, ZillowAdapter
from propertyfinder.config import Watch
from propertyfinder.geo import within_radius
from propertyfinder.store import (
    previous_snapshot_map,
    record_snapshot,
    upsert_property,
)
from propertyfinder.timeutil import utc_now_iso

log = logging.getLogger(__name__)


def collect_in_radius(
    adapter: ZillowAdapter, watch: Watch
) -> dict[str, tuple[Listing, float]]:
    """Every home inside the watch, deduplicated. Returns `{zpid: (listing, miles)}`.

    Dedupe keeps the copy that sits **nearest the centre**. The feed occasionally
    returns the same home twice with slightly different coordinates, and there is no way
    to know which is right — but the nearer one is the one that decides whether the home
    is in the circle at all, so preferring it keeps the membership decision and the
    stored distance telling the same story.
    """
    found: dict[str, tuple[Listing, float]] = {}

    for query in watch.queries:
        listings = adapter.search(
            query,
            listing_status=watch.listing_status,
            max_pages=watch.max_pages,
            extra=watch.filters or None,
        )
        inside_count = 0
        for listing in listings:
            inside, distance = within_radius(
                listing.lat, listing.lon, watch.lat, watch.lon, watch.radius_miles
            )
            if not inside:
                continue
            inside_count += 1
            previous = found.get(listing.zpid)
            if previous is None or distance < previous[1]:
                found[listing.zpid] = (listing, distance)

        # A query that comes back full of homes, none of them anywhere near the centre,
        # is almost never an empty market. It is a mis-resolved place string: we once
        # asked about 76008 and were answered, in full and in earnest, with Minerva,
        # Ohio. Silence here means a watch quietly stores nothing for weeks, so this is
        # loud — it is the difference between "no homes" and "wrong town".
        if listings and inside_count == 0:
            log.warning(
                "watch %s, query %r: the provider returned %d listings and NONE within "
                "%.1f mi of the centre — this is usually a mis-resolved place string "
                "(a bare ZIP once resolved to Minerva, Ohio), not an empty market. "
                "Check the query reads 'City, ST ZIP'.",
                watch.name,
                query,
                len(listings),
                watch.radius_miles,
            )

    log.info(
        "watch %s: %d home(s) within %.1f mi across %d quer%s (%d call(s) so far)",
        watch.name,
        len(found),
        watch.radius_miles,
        len(watch.queries),
        "y" if len(watch.queries) == 1 else "ies",
        adapter.request_count,
    )
    return found


# -- what changed ---------------------------------------------------------------------


@dataclass(frozen=True)
class PriceMove:
    """One home's ask, then and now."""

    zpid: str
    address: str | None
    previous: float
    current: float
    since: str  # when we last saw it — not necessarily the last sweep

    @property
    def delta(self) -> float:
        return self.current - self.previous


@dataclass(frozen=True)
class StatusFlip:
    """A home whose status word changed — pending, back on market, contingent."""

    zpid: str
    address: str | None
    previous: str | None
    current: str | None
    since: str


@dataclass(frozen=True)
class SweepSummary:
    """What one sweep of one watch found, and how it differs from last time."""

    watch: str
    snapshot_ts: str
    in_radius: int
    api_calls: int
    new: list[Listing] = field(default_factory=list)
    cuts: list[PriceMove] = field(default_factory=list)
    rises: list[PriceMove] = field(default_factory=list)
    status_changes: list[StatusFlip] = field(default_factory=list)
    gone: list[str] = field(default_factory=list)

    def headline(self) -> str:
        return (
            f"{self.watch}: {self.in_radius} in radius · {len(self.new)} new · "
            f"{len(self.cuts)} cut · {len(self.rises)} raised · "
            f"{len(self.status_changes)} status · {len(self.gone)} gone · "
            f"{self.api_calls} call(s)"
        )


def run_sweep(
    session: Session, adapter: ZillowAdapter, watch: Watch, now: str | None = None
) -> SweepSummary:
    """Sweep one watch: collect, persist, and report what moved.

    Everything the database learns from this sweep lands in **one transaction**. A sweep
    that failed half-way and left a partial snapshot behind would not merely lose data —
    it would invent history, because the next sweep would diff against a market that
    never existed. So on any failure the whole thing rolls back and the database is
    exactly as it was.

    The network happens first and outside the transaction. Holding a write transaction
    open across a minute of paginated HTTP would block every reader for no reason.
    """
    now = now or utc_now_iso()
    calls_before = adapter.request_count
    found = collect_in_radius(adapter, watch)

    try:
        # The baseline is read before anything is written, and with an exclusive cutoff
        # at `now`, so this sweep cannot end up compared against itself.
        baseline = previous_snapshot_map(session, watch.name, now)

        # Parents before children. No ORM relationship declares the snapshot→property
        # dependency, so nothing tells SQLAlchemy that a snapshot's INSERT must follow
        # its property's. Add a snapshot first and autoflush will happily order the
        # child ahead of the parent — foreign key failure, mid-sweep, on the one row
        # whose home the database had never seen before. Upsert every property, flush
        # the lot, and only then start observing.
        for listing, _distance in found.values():
            upsert_property(session, listing, now)
        session.flush()

        for listing, distance in found.values():
            record_snapshot(session, listing, watch.name, now, distance, watch.listing_status)

        summary = _diff(watch, now, found, baseline, adapter.request_count - calls_before)
        session.commit()
    except Exception:
        session.rollback()
        log.exception("sweep %s failed; the database is unchanged", watch.name)
        raise

    log.info("sweep %s: %s", watch.name, summary.headline())
    return summary


def _diff(
    watch: Watch,
    now: str,
    found: dict[str, tuple[Listing, float]],
    baseline: dict[str, dict],
    api_calls: int,
) -> SweepSummary:
    """Compare this sweep against the last time each home was seen.

    Two different questions, deliberately answered against two different baselines:

    - **What changed** is measured against the last sighting of *that home*, however
      long ago. A listing that vanished for three weeks and came back cheaper should
      say so, and comparing it against a sweep it was absent from could not.
    - **What left** is measured against the immediately preceding sweep only. Otherwise
      a home that sold in July is reported as newly gone every morning until Christmas.
    """
    summary_new: list[Listing] = []
    cuts: list[PriceMove] = []
    rises: list[PriceMove] = []
    flips: list[StatusFlip] = []

    for zpid, (listing, _distance) in found.items():
        was = baseline.get(zpid)
        if was is None:
            summary_new.append(listing)
            continue
        if listing.price and was["price"] and listing.price != was["price"]:
            move = PriceMove(
                zpid=zpid,
                address=listing.address,
                previous=was["price"],
                current=listing.price,
                since=was["snapshot_ts"],
            )
            (cuts if move.delta < 0 else rises).append(move)
        if listing.status_text and listing.status_text != was["status_text"]:
            flips.append(
                StatusFlip(
                    zpid=zpid,
                    address=listing.address,
                    previous=was["status_text"],
                    current=listing.status_text,
                    since=was["snapshot_ts"],
                )
            )

    last_sweep_ts = max((row["snapshot_ts"] for row in baseline.values()), default=None)
    gone = [
        zpid
        for zpid, row in baseline.items()
        if row["snapshot_ts"] == last_sweep_ts and zpid not in found
    ]

    return SweepSummary(
        watch=watch.name,
        snapshot_ts=now,
        in_radius=len(found),
        api_calls=api_calls,
        new=summary_new,
        cuts=cuts,
        rises=rises,
        status_changes=flips,
        gone=gone,
    )
