"""The payload: everything a report says, as one JSON-able dict — no HTML in sight.

`build_payload` is a pure read: database in, dict out. It never touches a template or
writes a byte of markup — the page-building step (`pagebuild.render`) owns markup, and
its whole job is receiving this dict and splicing it into a page. Keeping the two apart
is the rebuild's first rule (docs/REBUILD.md, Part I item 1): a number that is wrong
becomes a Python dict a unit test can check without a browser, and a page that looks
wrong becomes an HTML file with syntax highlighting, and the two mistakes never hide
inside each other.

Nulls are the honest answer to missing data. A home the feed never gave a square footage
has no $ per square foot — this module writes that down as `None` (JSON `null`), which the
template renders as "—", rather than inventing a number to fill the cell. Medians are
computed over whatever values are actually present, never padded with a guess.

"Listings" means homes seen in the *most recent* sweep this watch has on record — a home
whose newest observation predates that sweep has left the market, and belongs to the
movement strip's "gone" bucket, not a table of things still for sale.
"""
from __future__ import annotations

import statistics
from collections.abc import Iterable

from sqlalchemy.orm import Session

from propertyfinder.config import Watch
from propertyfinder.store import latest_snapshot_rows


def build_payload(session: Session, watch: Watch, generated_ts: str) -> dict:
    """Everything one report renders, computed once and handed to the template whole."""
    rows = latest_snapshot_rows(session, watch.name)
    sweep_ts = max((r["snapshot_ts"] for r in rows), default=None)
    active = [r for r in rows if r["snapshot_ts"] == sweep_ts] if sweep_ts else []

    listings = sorted((_listing_row(r) for r in active), key=_listing_sort_key)

    return {
        "watch": {
            "name": watch.name,
            "center_address": watch.center_address,
            "radius_miles": watch.radius_miles,
            "listing_status": watch.listing_status,
        },
        "generated_ts": generated_ts,
        "sweep_ts": sweep_ts,
        "counts": {"total": len(listings)},
        "medians": {
            "price": _median(r["price"] for r in listings),
            "price_per_sqft": _median(r["price_per_sqft"] for r in listings),
            "days_on_market": _median(r["days_on_market"] for r in listings),
        },
        "listings": listings,
    }


def _listing_row(row: dict) -> dict:
    """One home's payload row. `price_per_sqft` is computed here, once, honestly: absent
    unless both price and square footage are actually known."""
    price, sqft = row.get("price"), row.get("sqft")
    return {
        "zpid": row["zpid"],
        "address": row.get("address"),
        "price": price,
        "beds": row.get("beds"),
        "baths": row.get("baths"),
        "sqft": sqft,
        "price_per_sqft": (price / sqft) if price and sqft else None,
        "days_on_market": row.get("days_on_zillow"),
        "status": row.get("status_text") or row.get("listing_status"),
        "link": row.get("link"),
        "distance_miles": row.get("distance_miles"),
    }


def _listing_sort_key(row: dict):
    """Cheapest first, by default — the table is sortable in the page, so this default
    only has to be *a* sensible order, not the only useful one. Homes with no price sort
    to the end rather than to the front, where a `None` would otherwise look free."""
    price = row["price"]
    return (price is None, price if price is not None else 0.0, row["address"] or "")


def _median(values: Iterable[float | None]) -> float | None:
    present = [v for v in values if v is not None]
    return statistics.median(present) if present else None
