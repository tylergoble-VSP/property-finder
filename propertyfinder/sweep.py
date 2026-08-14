"""One watch, one sweep: fan out across its queries, then reconcile the answers.

A watch is a place, but the provider does not sell places — it sells answers to search
strings. Several strings are usually needed to cover one circle (a two-mile radius spills
across ZIP boundaries), they overlap, and they disagree at the edges. This module turns
that back into a place: every query is asked, every home is judged against the radius,
and a home that two queries both returned is kept once.
"""
from __future__ import annotations

import logging

from propertyfinder.adapters import Listing, ZillowAdapter
from propertyfinder.config import Watch
from propertyfinder.geo import within_radius

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
