"""Find the luxury listings in the ring — the cheap half of the whole tool.

`sort_by=price_desc` plus a price floor plus early-stop is the enabling budget lever: an
unfiltered 25-mile sweep would cost ~490 calls, but sorted high-to-low we can stop paging a
place string the moment its cheapest listing drops below the floor, so each string costs ~1–2
calls instead of a whole ZIP. The radius stays authoritative (core's rule): whatever a place
string drags in, a listing outside the circle — or with no coordinates — is discarded.
"""
from __future__ import annotations

import logging

from propertyfinder.adapters.listing import Listing
from propertyfinder.geo import within_radius

from agentfinder.adapters import LuxeExtras, SearchApi
from agentfinder.config import LuxuryConfig

log = logging.getLogger(__name__)


def below_floor(rows: list[tuple[Listing, LuxeExtras]], floor: float) -> bool:
    """True once this price_desc page's cheapest listing is under the floor.

    That is the proof no later page can hold a luxury home, so paging can stop. An empty
    page is not 'below floor' — it is handled by the caller's own emptiness check — so a
    page with no priced rows returns False and lets the loop's other guards end it.
    """
    prices = [l.price for l, _ in rows if l.price is not None]
    return bool(prices) and min(prices) < floor


def is_luxury(listing: Listing, floor: float) -> bool:
    """A real, purchasable luxury home: at or above the floor, with a size, not a plan sheet.

    Builder 'Plan,' rows are an ask-curve, not a home (core's rule), and a listing with no
    square footage cannot be judged or furnished — both are excluded from the census."""
    return bool(
        listing.price is not None
        and listing.price >= floor
        and listing.sqft
        and "Plan," not in (listing.address or "")
    )


def collect_luxury(
    api: SearchApi, cfg: LuxuryConfig
) -> list[tuple[Listing, LuxeExtras, float | None]]:
    """Every luxury listing in the ring, deduplicated per home, nearest copy kept.

    Returns (listing, extras, distance_miles). A home surfaced by two place strings is
    stored once; the radius filter runs on every candidate and no-coordinate homes are out.
    """
    best: dict[str, tuple[Listing, LuxeExtras, float]] = {}
    for query in cfg.queries:
        for page in range(1, cfg.max_pages + 1):
            rows, pagination = api.zillow_page(query, page=page)
            if not rows:
                break
            in_radius_here = 0
            for listing, extras in rows:
                if not is_luxury(listing, cfg.price_floor):
                    continue
                inside, distance = within_radius(
                    listing.lat, listing.lon, cfg.lat, cfg.lon, cfg.radius_miles
                )
                if not inside:
                    continue
                in_radius_here += 1
                prior = best.get(listing.zpid)
                if prior is None or (distance is not None and distance < prior[2]):
                    best[listing.zpid] = (listing, extras, distance)
            # A place string that returns luxury homes but none in-radius is a mis-resolve
            # worth a line in the log — the same warning core's sweep keeps (Minerva, Ohio).
            if rows and in_radius_here == 0 and any(
                is_luxury(l, cfg.price_floor) for l, _ in rows
            ):
                log.warning("query %r returned luxury listings, none in radius", query)
            if below_floor(rows, cfg.price_floor):
                break  # price_desc: the tail is all sub-floor from here
            if page >= int(pagination.get("total_pages") or 1):
                break
    return [(l, e, d) for l, e, d in best.values()]
