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

The `movement` block is `store.sweep_changes` verbatim — this module does no diffing of
its own. History is the product, and the payload's job is to hand the page whatever the
store already knows how to say about it.

`carry` is the monthly cost of holding each home, and it is present only when the caller
supplies the assumptions it rests on. Nothing here invents a mortgage rate or a tax rate:
a report built without a finance block simply has no monthly column, which is the honest
outcome. When there *is* one, the citations travel with it into the page, because a number
that moved every monthly figure by hundreds of dollars when it was corrected is a number
the reader is owed the source of.
"""
from __future__ import annotations

import statistics
from collections.abc import Iterable
from dataclasses import asdict

from sqlalchemy.orm import Session

from propertyfinder.config import FinanceAssumptions, Watch
from propertyfinder.costmodel import monthly_cost
from propertyfinder.criteria import screen
from propertyfinder.store import latest_snapshot_rows, price_change_map, sweep_changes


def build_payload(
    session: Session,
    watch: Watch,
    generated_ts: str,
    finance: FinanceAssumptions | None = None,
) -> dict:
    """Everything one report renders, computed once and handed to the template whole."""
    rows = latest_snapshot_rows(session, watch.name)
    sweep_ts = max((r["snapshot_ts"] for r in rows), default=None)
    active = [r for r in rows if r["snapshot_ts"] == sweep_ts] if sweep_ts else []

    # The buyer's brief, applied at render time exactly as the map applies it, so the two
    # pages built from one watch are about the same set of homes and their counts agree.
    screening = screen(active, watch.criteria)

    cuts_to_date = price_change_map(session, watch.name)
    listings = sorted(
        (
            _listing_row(r, cuts_to_date.get(r["zpid"]), finance)
            for r in screening.kept
        ),
        key=_listing_sort_key,
    )

    return {
        "watch": {
            "name": watch.name,
            "center_address": watch.center_address,
            "radius_miles": watch.radius_miles,
            "listing_status": watch.listing_status,
        },
        "criteria": screening.as_payload(),
        "generated_ts": generated_ts,
        "sweep_ts": sweep_ts,
        "counts": {
            "total": len(listings),
            "considered": screening.considered,
            "screened_out": screening.n_dropped,
        },
        "medians": {
            "price": _median(r["price"] for r in listings),
            "price_per_sqft": _median(r["price_per_sqft"] for r in listings),
            "days_on_market": _median(r["days_on_market"] for r in listings),
            "carry": _median(
                (r["carry"] or {}).get("total") for r in listings if r["carry"]
            ),
        },
        "finance": _finance_block(finance),
        "listings": listings,
        "movement": sweep_changes(session, watch.name),
    }


def _finance_block(finance: FinanceAssumptions | None) -> dict | None:
    """The assumptions every `carry` figure rests on, verbatim, for the page's appendix.

    `model_dump` rather than a hand-written dict: the appendix's whole purpose is to show
    what was assumed, and a hand-copied list is a place for an assumption to go unshown.
    """
    return None if finance is None else finance.model_dump()


def _listing_row(row: dict, price_cut: dict | None, finance: FinanceAssumptions | None) -> dict:
    """One home's payload row. `price_per_sqft` is computed here, once, honestly: absent
    unless both price and square footage are actually known.

    `price_cut` comes from `store.price_change_map` — `None` for a home whose ask has
    never moved, which is exactly what leaves `price_cut_dollars` absent rather than a
    zero the template would otherwise have to know to hide.
    """
    price, sqft = row.get("price"), row.get("sqft")
    # No tax rate or dues are stored per home yet (both arrive with the detail engine at
    # Stage 8), so every home is costed on the watch's own verified rate — which is the
    # better number here anyway: it is the adopted stack, not a county average.
    carry = (
        monthly_cost(price, tax_rate=None, hoa_monthly=None, fin=finance)
        if finance is not None
        else None
    )
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
        "first_price": price_cut["first"] if price_cut else None,
        "price_cut_dollars": price_cut["cut_dollars"] if price_cut else None,
        "price_cut_pct": price_cut["cut_pct"] if price_cut else None,
        "carry": asdict(carry) if carry else None,
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
