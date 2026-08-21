"""The new-construction buyer report's payload: one dict, and a visible seam down the middle.

`reportdata.py` and `mapdata.py` build payloads that are entirely derived — every key comes
out of the database. This one does not. A buyer report about a market with no closed sales is
half arithmetic and half research: what the builders are asking today can only be computed,
and what the improvement district bills, what each builder's warranty covers, and which
neighbourhood was platted when can only be read by a person and written down.

Mixing those two in one file is the mistake this module is shaped to prevent. The original
tool kept machine-derived aggregates and hand-researched prose in a single `payload.json` and
refreshed it in place; builders turned up in the roster table holding plans they no longer had
and the cheapest-plan column rendered `undefined`, because a stale derived block had been
carried forward alongside the prose it was sitting next to (docs/PORTING-THE-REPORTS.md,
lesson 5). So:

    every top-level key here is DERIVED — recomputed from the database on every build —
    except exactly one, `curated`, which is read from `data/walsh-newcon-curated.yaml`
    and which no build ever writes.

`tests/test_newconreport.py` asserts that sentence rather than trusting it, and
`pagebuild.render`'s exactly-one-payload-token contract is what forces the two halves to meet
in one place where such a test can stand.

Two other rules the shape of this module carries:

**Today, not ever.** Every count, median and curve is computed over the most recent sweep
(`newcon.on_the_market_today`). The database remembers every home it has ever seen, which is
the right answer about history and the wrong one for a page dated today (lesson 2).

**Rates state their window.** `market.window` carries `window_days`, `window_from`,
`window_to` and `n_sweeps` alongside the absorption figures computed over them, and the keys
are named for what they are rather than for the value they held one day — the original kept a
key called `absorbed_26d` well after the window had stopped being 26 days, because renaming it
meant touching the template (lesson 7).
"""
from __future__ import annotations

import statistics
from collections.abc import Iterable, Sequence
from dataclasses import asdict

from sqlalchemy.orm import Session

from propertyfinder.config import FinanceAssumptions, Watch, WatchConfig
from propertyfinder.costmodel import monthly_cost
from propertyfinder.dataquality import (
    BATHS_CORRECTED,
    RESEARCHED,
    UNRESOLVED,
    apply_corrections,
    assess,
    curated,
)
from propertyfinder.newcon import (
    compute_plan_baseline,
    is_plan_sheet,
    is_spec,
    on_the_market_today,
    plan_community,
    plan_name,
    ppsf,
    score_specs,
)
from propertyfinder.store import (
    latest_snapshot_rows,
    observation_spans,
    price_change_map,
    sweep_timestamps,
)

CURATED_FILE = "walsh-newcon-curated.yaml"

# The one top-level key that is read rather than computed. Named here so the boundary test
# and the module's own docstring cannot drift apart.
CURATED_KEY = "curated"

# A finished spec home past this many days is the pressure point the report is about: a
# builder paying interest on a house nobody bought. Shared with the page so the sentence and
# the count cannot disagree.
STALE_SPEC_DAYS = 90

# Days per month for the absorption rate. Thirty, deliberately round: the figure it feeds
# ("months of supply") is a planning heuristic with a wide honest error bar, and a calendar
# month's worth of precision would dress it up as something finer than it is.
DAYS_PER_MONTH = 30

# What a builder that resolved to nobody is called on the page. A bucket, not a builder —
# which is why it is one string in one place rather than a null every consumer must remember
# to special-case.
UNRESOLVED_BUILDER = "Not identified"


def build_payload(
    session: Session,
    watch: Watch,
    cfg: WatchConfig,
    generated_ts: str,
    finance: FinanceAssumptions | None = None,
) -> dict:
    """Everything the new-construction report renders, computed once and handed over whole.

    `generated_ts` fixes the clock for the whole document, the same way it does in
    `mapdata.build_map_payload`, so two builds of one day's data are byte-identical.

    `finance` overrides the watch's own block, which is how `report --public` renders a page
    with market-neutral assumptions instead of a household's (lesson 9). Left `None`, the
    watch's merged block is used.
    """
    finance = cfg.finance_for(watch) if finance is None else finance

    history = latest_snapshot_rows(session, watch.name)
    rows = on_the_market_today(history)
    sweep_ts = rows[0]["snapshot_ts"] if rows else None

    # Assessed over the whole of history rather than over today's rows alone: a duplicate
    # needs the twin it duplicates, and a suspect bath count needs the sibling plan that
    # proves the feed can print a half.
    quality = assess(history)
    cuts = price_change_map(session, watch.name)
    spans = observation_spans(session, watch.name)
    corrected = [apply_corrections(r, quality.get(r["zpid"])) for r in rows]

    baseline = compute_plan_baseline(session, watch.name)
    cards = {c.zpid: c for c in score_specs(session, watch.name, baseline)}

    def home(row: dict) -> dict:
        return _home(row, quality.get(row["zpid"]), cuts.get(row["zpid"]),
                     spans.get(row["zpid"]), finance, cards.get(row["zpid"]))

    plans = sorted(
        (home(r) for r in corrected if is_plan_sheet(r)),
        key=lambda r: (r["builder"], r["price"] or 0.0),
    )
    specs = sorted(
        (
            home(r)
            for r in corrected
            if is_spec(r) and not _is_duplicate(quality.get(r["zpid"]))
        ),
        key=lambda r: (-(r["score"] or 0.0), r["address"] or ""),
    )
    resale = sorted(
        (
            home(r)
            for r in corrected
            if not is_plan_sheet(r) and not is_spec(r)
            and not _is_duplicate(quality.get(r["zpid"]))
        ),
        key=lambda r: (r["price"] is None, r["price"] or 0.0),
    )

    return {
        "watch": {
            "name": watch.name,
            "center_address": watch.center_address,
            "lat": watch.lat,
            "lon": watch.lon,
            "radius_miles": watch.radius_miles,
            "subdivision": watch.subdivision,
        },
        "generated_ts": generated_ts,
        "sweep_ts": sweep_ts,
        "market": _market(session, watch, plans, specs, resale, quality, cuts, spans),
        "askcurve": _askcurve(baseline),
        "builders": _builders(plans, specs),
        "plans": plans,
        "specs": specs,
        "resale": resale,
        "finance": _finance_block(finance),
        CURATED_KEY: dict(curated(CURATED_FILE)),
    }


# -- one home ---------------------------------------------------------------------------


def _home(row, quality, cut, span, finance, card) -> dict:
    """One plan sheet, spec home or resale, in the one shape the page reads them all in.

    A single row shape for three kinds of thing, because the map, the tables and the filters
    all want to treat them alike, and three near-identical shapes is how a template ends up
    with three near-identical renderers and a bug in one of them. `kind` says which it is;
    the fields a kind cannot have are `None` rather than absent, so nothing on the page has
    to ask whether a key exists before reading it.
    """
    price, sqft = row.get("price"), row.get("sqft")
    carry = monthly_cost(price, tax_rate=None, hoa_monthly=None, fin=finance) if finance else None
    builder, tier = (quality.builder, quality.builder_tier) if quality else (None, UNRESOLVED)
    return {
        "zpid": row["zpid"],
        "address": row.get("address"),
        "kind": "plan" if is_plan_sheet(row) else "spec" if is_spec(row) else "resale",
        "plan": plan_name(row.get("address")),
        "community": plan_community(row.get("address")),
        "builder": builder or UNRESOLVED_BUILDER,
        "builder_tier": tier,
        "beds": row.get("beds"),
        "baths": row.get("baths"),
        "baths_corrected": bool(quality and quality.has(BATHS_CORRECTED)),
        "baths_listed": (quality.corrections.get("baths") or {}).get("listed")
        if quality
        else None,
        "sqft": sqft,
        "sqft_max": row.get("sqft_max"),
        "lot_sqft": row.get("lot_sqft"),
        "price": price,
        "price_per_sqft": ppsf(row),
        "days_on_market": row.get("days_on_zillow"),
        "year_built": row.get("year_built"),
        "link": row.get("link"),
        "image_url": row.get("image_url"),
        "lat": row.get("lat"),
        "lon": row.get("lon"),
        "first_price": cut["first"] if cut else None,
        "price_cut_dollars": cut["cut_dollars"] if cut else None,
        "price_cut_pct": cut["cut_pct"] if cut else None,
        "n_observations": (span or {}).get("n_obs"),
        "first_seen": (span or {}).get("first_ts"),
        "carry": asdict(carry) if carry else None,
        # Scoring belongs to spec homes alone: a plan sheet is an offer, not a home, and a
        # resale in a market with no disclosed sale prices has nothing to be scored against.
        "score": card.score if card else None,
        "verdict": card.verdict if card else None,
        "confidence": card.confidence if card else None,
        "discount_pct": card.discount_pct if card else None,
        "comp": {"ppsf": card.comp.ppsf, "n_in_band": card.comp.n_in_band,
                 "basis": card.comp.basis}
        if card
        else None,
        "ledger": [asdict(line) for line in card.ledger] if card else None,
    }


def _is_duplicate(quality) -> bool:
    return bool(quality and quality.is_duplicate)


# -- the market, and the window every rate in it was measured over -----------------------


def _market(session, watch, plans, specs, resale, quality, cuts, spans) -> dict:
    """The read: what is standing, what it costs, what has moved, and over how long."""
    stamps = sweep_timestamps(session, watch.name)
    window = _window(stamps, spans, session, watch)

    moves = [c for c in cuts.values() if c["cut_dollars"] > 0]
    rises = [c for c in cuts.values() if c["cut_dollars"] < 0]
    return {
        "n_plans": len(plans),
        "n_specs": len(specs),
        "n_resale": len(resale),
        "spec_median_price": _median(r["price"] for r in specs),
        "spec_median_ppsf": _median(r["price_per_sqft"] for r in specs),
        "spec_median_dom": _median(r["days_on_market"] for r in specs),
        "resale_median_price": _median(r["price"] for r in resale),
        "resale_median_ppsf": _median(r["price_per_sqft"] for r in resale),
        "resale_median_dom": _median(r["days_on_market"] for r in resale),
        "specs_over_stale_days": sum(
            1 for r in specs if (r["days_on_market"] or 0) > STALE_SPEC_DAYS
        ),
        "stale_spec_days": STALE_SPEC_DAYS,
        "n_cuts": len(moves),
        "n_rises": len(rises),
        "total_cut_dollars": sum(c["cut_dollars"] for c in moves),
        "deepest_cut_dollars": max((c["cut_dollars"] for c in moves), default=None),
        "n_duplicates_dropped": sum(1 for q in quality.values() if q.is_duplicate),
        "n_bath_corrections": sum(1 for q in quality.values() if q.has(BATHS_CORRECTED)),
        "n_researched_builders": sum(
            1 for r in plans + specs if r["builder_tier"] == RESEARCHED
        ),
        "n_unresolved_builders": sum(
            1 for r in specs if r["builder"] == UNRESOLVED_BUILDER
        ),
        "window": window,
    }


def _window(stamps: Sequence[str], spans: dict, session, watch) -> dict:
    """The observed window, and the absorption rate measured across the whole of it.

    Both halves matter and neither is meaningful alone. A rate is a number divided by a
    window, and a page that prints the number without the window is asking to be believed on
    a sample size it declined to mention — which is how "18.2 months of supply" got onto a
    page on the strength of one home selling in a fortnight.

    `absorbed` counts homes whose last sighting predates the final sweep. The feed does not
    say whether they sold, were withdrawn, or came back under a new identity, so the word is
    "absorbed" and not "sold".
    """
    if not stamps:
        return {
            "n_sweeps": 0, "window_from": None, "window_to": None, "window_days": 0,
            "inventory_start": 0, "inventory_end": 0, "absorbed": 0, "months_supply": None,
        }

    first, last = stamps[0], stamps[-1]
    days = _days_between(first, last)
    rows = latest_snapshot_rows(session, watch.name)
    by_zpid = {r["zpid"]: r for r in rows}

    def specs_at(ts: str) -> int:
        return sum(
            1
            for zpid, span in spans.items()
            if span["first_ts"] <= ts <= span["last_ts"] and is_spec(by_zpid.get(zpid, {}))
        )

    absorbed = sum(
        1
        for zpid, span in spans.items()
        if span["last_ts"] < last and is_spec(by_zpid.get(zpid, {}))
    )
    inventory_end = specs_at(last)
    rate = (absorbed / days * DAYS_PER_MONTH) if days and absorbed else None
    return {
        "n_sweeps": len(stamps),
        "window_from": first,
        "window_to": last,
        "window_days": days,
        "inventory_start": specs_at(first),
        "inventory_end": inventory_end,
        "absorbed": absorbed,
        "months_supply": round(inventory_end / rate, 1) if rate else None,
    }


def _days_between(first: str, last: str) -> int:
    """Whole days between two of this tool's fixed-width UTC stamps.

    Parsed by hand rather than through `datetime` for the same reason `latest_snapshot_rows`
    compares these as text: the format is this tool's own invariant, and a dependency on
    timezone handling for a subtraction of two Z-suffixed strings buys nothing.
    """
    from datetime import datetime

    fmt = "%Y-%m-%dT%H:%M:%SZ"
    return (datetime.strptime(last, fmt) - datetime.strptime(first, fmt)).days


# -- the builder roster, recomputed every single build -----------------------------------


def _builders(plans: Sequence[dict], specs: Sequence[dict]) -> list[dict]:
    """Per builder: what it lists, what it asks, and how it has behaved on price.

    This is the exact block whose carry-forward rendered `undefined` in the original: a
    builder kept a plan count it no longer had, and the cheapest-plan column pointed at a
    plan that had left the sheet. It is derived, every build, from the rows above — there is
    no curated copy of it anywhere and no path by which a stale one could survive.
    """
    names = sorted({r["builder"] for r in list(plans) + list(specs)})
    roster = []
    for name in names:
        their_plans = [r for r in plans if r["builder"] == name]
        their_specs = [r for r in specs if r["builder"] == name]
        cut = [r for r in their_specs if (r["price_cut_dollars"] or 0) > 0]
        prices = [r["price"] for r in their_plans if r["price"] is not None]
        roster.append(
            {
                "builder": name,
                "unresolved": name == UNRESOLVED_BUILDER,
                "n_plans": len(their_plans),
                "n_specs": len(their_specs),
                "communities": sorted({r["community"] for r in their_plans if r["community"]}),
                "plan_price_min": min(prices) if prices else None,
                "plan_price_max": max(prices) if prices else None,
                "plan_ppsf": _median(r["price_per_sqft"] for r in their_plans),
                "spec_median_price": _median(r["price"] for r in their_specs),
                "spec_ppsf": _median(r["price_per_sqft"] for r in their_specs),
                "spec_median_dom": _median(r["days_on_market"] for r in their_specs),
                "n_cut": len(cut),
                "median_cut_pct": _median(r["price_cut_pct"] for r in cut),
                "total_cut_dollars": sum(r["price_cut_dollars"] for r in cut),
            }
        )
    # Most inventory first, and the unresolved bucket last however much it holds — it is not
    # a competitor in the roster, it is the report admitting what it does not know.
    roster.sort(key=lambda b: (b["unresolved"], -(b["n_plans"] + b["n_specs"]), b["builder"]))
    return roster


def _askcurve(baseline) -> dict:
    """The plan-sheet ask curve, as points a chart can draw and a caption can describe."""
    return {
        "n_plans": baseline.n_plans,
        "points": [
            {"plan": p.plan, "sqft": p.sqft, "ppsf": p.ppsf} for p in baseline.plans
        ],
        "communities": [
            {"community": c.community, "n": c.n, "sqft_p50": c.sqft_p50, "ppsf_p50": c.ppsf_p50}
            for c in baseline.communities
        ],
    }


def _finance_block(finance: FinanceAssumptions | None) -> dict | None:
    """The assumptions every carry figure rests on, verbatim, for the page's appendix.

    `model_dump` rather than a hand-written dict, same as `reportdata`: the appendix exists to
    show what was assumed, and a hand-copied list is a place for an assumption to go unshown.
    It is also what makes the public-page tripwire test possible — a whitelist can only be
    checked against a block that was dumped rather than composed.
    """
    return None if finance is None else finance.model_dump()


def _median(values: Iterable[float | None]) -> float | None:
    present = [v for v in values if v is not None]
    return statistics.median(present) if present else None
