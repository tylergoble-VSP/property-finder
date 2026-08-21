"""The map report's payload: one JSON document holding everything the page will say.

The flagship page of the original tool was 1,334 lines of Python, seventy-five of them
holding script, style and closing-tag fragments spliced together one string at a time.
This module is the half of that page which is *not* markup, separated out and given a
shape a unit test can read: database in, plain dictionary out, not one angle bracket
anywhere. `templates/map.html` is the other half, and `pagebuild.render` is the only thing
that ever sees both.

**Degrade, do not die.** Every part of this page rests on something that may not exist. A
watch may have no sold companion; a sold companion may hold too few sales to fit a model
on; a market may have no builder price list; a database may hold a single sweep. None of
those is an error, and none of them stops the page being built — each becomes a block that
says `fitted: false`, or `null`, or an empty list, and the template renders the sections it
has. The `model` block exists precisely so the page can state what it is standing on
rather than leaving a reader to infer it from what is missing.

Three judgements this module makes, written down here because they are choices rather than
mechanics:

**Plan sheets are not listings.** A row addressed "GRANTLEY Plan, Walsh Ranch 70'" is a
builder's price list, not a house anyone can buy or put a pin on a map for. It never
appears in `listings`; it appears, reduced to an ask curve, in the `newcon` block. The
original put plan rows on the map as unscored markers, which meant a reader counting
homes counted offers.

**A home the feed listed twice appears once.** `dataquality.find_duplicates` names the
twin; the thinner record is dropped from `listings` and the count is published in
`counts.duplicates_dropped`, so a leaderboard never shows one house in two places and
nobody has to wonder where the missing row went.

**The tax rate is the watch's verified one; the dues are the home's own.** Two different
answers to the same shape of question, on purpose. A market's ad-valorem stack is a
published, cited figure and is better than an effective rate scraped off one listing (which
silently mixes in whatever exemptions that owner happens to hold). Association dues are the
opposite: they genuinely differ per home and no market-wide number is true of any of them,
so the enriched value is used where the detail engine found one.
"""
from __future__ import annotations

import statistics
from collections.abc import Iterable
from dataclasses import asdict

from sqlalchemy.orm import Session

from propertyfinder.baseline import compute_sold_baseline
from propertyfinder.config import Watch, WatchConfig
from propertyfinder.costmodel import FinanceAssumptions, monthly_cost
from propertyfinder.criteria import screen
from propertyfinder.dataquality import apply_corrections, assess
from propertyfinder.newcon import (
    compute_plan_baseline,
    is_plan_sheet,
    is_spec,
    score_specs,
)
from propertyfinder.store import latest_snapshot_rows, price_change_map, sweep_changes

# The naming convention that pairs a for-sale watch with the sales it is judged against.
# One rule, stated once: `walsh-aledo` is valued against `walsh-aledo-sold`.
SOLD_SUFFIX = "-sold"

# How many points the fair-price curve is drawn from. Forty is enough for a smooth line
# across a market's size range and small enough that the payload does not notice it.
CURVE_POINTS = 40

# A home is "statistically underpriced" at one standard deviation below the fitted
# surface — outside the market's own scatter, which is what the phrase has to mean if it
# is to mean anything. Same threshold `stats.py`'s docstring names.
UNDERPRICED_Z = -1.0


def build_map_payload(
    session: Session, watch: Watch, cfg: WatchConfig, generated_ts: str
) -> dict:
    """Everything the map report renders, computed once and handed to the template whole.

    `generated_ts` fixes the clock for the whole document — the page's date, the sold
    window, and the year the model measures a home's age against — so two builds of one
    day's data are byte-identical and no figure drifts against the calendar mid-build.
    """
    finance = cfg.finance_for(watch)
    sold_watch = sold_companion(cfg, watch)

    rows = latest_snapshot_rows(session, watch.name)
    sweep_ts = max((r["snapshot_ts"] for r in rows), default=None)
    active = [r for r in rows if r["snapshot_ts"] == sweep_ts] if sweep_ts else []

    # Assessed over the whole watch rather than over the active rows alone: a duplicate
    # needs the twin it duplicates, and a suspect bath count needs the sibling plan that
    # proves the feed can print a half.
    quality = assess(rows)
    solds = latest_snapshot_rows(session, sold_watch) if sold_watch else []
    model, model_block = _fit_model(solds, sold_watch, generated_ts)

    cuts = price_change_map(session, watch.name)
    corrected = [apply_corrections(r, quality.get(r["zpid"])) for r in active]

    plan_sheets = [r for r in corrected if is_plan_sheet(r)]
    candidates = [
        r
        for r in corrected
        if not is_plan_sheet(r) and not _is_duplicate(quality.get(r["zpid"]))
    ]

    # The buyer's brief, applied here and nowhere upstream. Everything below this line —
    # the cards, the counts, the medians, the curve, the map itself — is therefore about
    # the shortlist and only the shortlist, while `solds` stays deliberately unscreened:
    # a hedonic model controls for size and bedrooms, so narrowing its comps to the
    # shortlist's own shape would starve the fit of the range it needs to measure them.
    screening = screen(candidates, watch.criteria)
    homes = screening.kept
    deals = _resale_cards(homes, solds, model, cuts)
    plan_baseline = compute_plan_baseline(session, watch.name)
    specs = (
        {card.zpid: card for card in score_specs(session, watch.name, plan_baseline)}
        if plan_baseline.n_plans
        else {}
    )

    listings = sorted(
        (
            _listing(row, cuts.get(row["zpid"]), finance, quality.get(row["zpid"]),
                     deals.get(row["zpid"]), specs.get(row["zpid"]))
            for row in homes
        ),
        key=_rank,
    )
    scored = [r["deal"] for r in listings if r["deal"]]

    return {
        "watch": {
            "name": watch.name,
            "center_address": watch.center_address,
            "lat": watch.lat,
            "lon": watch.lon,
            "radius_miles": watch.radius_miles,
            "listing_status": watch.listing_status,
            "subdivision": watch.subdivision,
            "sold_watch": sold_watch,
        },
        "criteria": screening.as_payload(),
        "generated_ts": generated_ts,
        "sweep_ts": sweep_ts,
        "counts": {
            "active": len(listings),
            # What the circle held before the brief was applied. `active` is the shortlist;
            # this is the market it was drawn from, so the page can state one as a fraction
            # of the other instead of quoting a number with nothing behind it.
            "considered": screening.considered,
            "screened_out": screening.n_dropped,
            "scored": len(scored),
            "great_or_good": sum(1 for d in scored if d["verdict"] in ("GREAT", "GOOD")),
            "underpriced": sum(
                1
                for d in scored
                if (d.get("fit") or {}).get("z") is not None
                and d["fit"]["z"] <= UNDERPRICED_Z
            ),
            "plan_sheets": len(plan_sheets),
            "duplicates_dropped": sum(
                1 for r in corrected if _is_duplicate(quality.get(r["zpid"]))
            ),
            "enriched": sum(1 for r in homes if r.get("year_built")),
        },
        "medians": {
            "price": _median(r["price"] for r in listings),
            "price_per_sqft": _median(r["price_per_sqft"] for r in listings),
            "days_on_market": _median(r["days_on_market"] for r in listings),
            "carry": _median((r["carry"] or {}).get("total") for r in listings),
        },
        "finance": None if finance is None else finance.model_dump(),
        "model": model_block,
        "sold_baseline": _sold_baseline(session, sold_watch, generated_ts),
        "listings": listings,
        "movement": sweep_changes(session, watch.name),
        "newcon": _newcon(plan_baseline, specs),
        "curve": _curve(model, watch, homes),
        "solds": _sold_points(solds, model_block["basis"]),
    }


def sold_companion(cfg: WatchConfig, watch: Watch) -> str | None:
    """The sold watch this one is judged against, by naming convention, or None.

    A missing companion is an ordinary state, not a misconfiguration: a market being
    watched for the first week has no sold side yet, and the page it produces is a map and
    a table with no scores on it, which is exactly what the tool knows about that market.
    """
    name = f"{watch.name}{SOLD_SUFFIX}"
    return name if any(w.name == name for w in cfg.watches) else None


# -- the model, and the four ways it can be absent ---------------------------------------


def _fit_model(sold_rows: list[dict], sold_watch: str | None, generated_ts: str):
    """(model, the block describing it) — the page's answer to "what is this standing on".

    The import is deferred because the scientific stack is an optional extra: sweeping a
    market and reading a table must not require numpy, and a core-only install should get
    the unscored page rather than a traceback. Every failure path lands in the same shape,
    carrying a reason written for a reader rather than a stack trace.
    """
    if sold_watch is None:
        return None, _unfitted(
            "no sold companion watch is configured, so there are no sales to value "
            "against — add a watch named after this one with '-sold' on the end"
        )
    try:
        from propertyfinder.stats import MIN_COMPS, HedonicModel
    except ImportError:  # pragma: no cover - exercised only on a core-only install
        return None, _unfitted(
            "the statistics extra is not installed, so no valuation model was fitted "
            "(pip install 'propertyfinder[stats]')"
        )

    model = HedonicModel.fit(sold_rows, now_iso=generated_ts)
    if model is None:
        return None, _unfitted(
            f"{sold_watch} holds too few usable sales to fit a model — at least "
            f"{MIN_COMPS} complete ones are needed before a curve through them means "
            "anything"
        )

    return model, {
        "fitted": True,
        "n": model.n,
        "r2": round(model.r2, 4),
        "sigma": round(model.sigma, 4),
        "basis": model.basis,
        "kind": "enriched" if model.enriched_result is not None else "base",
        "located": model.location_index is not None,
        "notes": model.plain_english(),
        "reason": None,
    }


def _unfitted(reason: str) -> dict:
    """The model block for a page with no model: every field absent, and why, in English."""
    return {
        "fitted": False,
        "n": None,
        "r2": None,
        "sigma": None,
        "basis": None,
        "kind": None,
        "located": False,
        "notes": [],
        "reason": reason,
    }


def _resale_cards(homes, sold_rows, model, cuts) -> dict[str, dict]:
    """Sales-based deal cards, keyed by zpid. Empty without a fitted model.

    Every home goes in, spec homes included, even though a spec with a builder price list
    behind it will be scored on that instead and this card discarded. Two reasons. Where a
    builder publishes no price list, sales are the only evidence a new home has, and a card
    carrying its own confidence tier beats no card at all. And the days-on-market
    percentiles are computed across whatever is passed in — a market's "longer than 80% of
    what is for sale here" has to mean the whole market, not the resale half of it.
    """
    if model is None:
        return {}
    from propertyfinder.deals import build_deal_cards

    return {
        card.zpid: _deal_from_card(card)
        for card in build_deal_cards(homes, sold_rows, model, cuts)
    }


def _deal_from_card(card) -> dict:
    """One resale `DealCard`, as data.

    The ledger is copied out line by line rather than summarised, because it is the whole
    point of the card: a reader is owed the arithmetic, not its answer. `fit` holds what
    only a fitted surface can say; `comps` holds the independent nearest-sales reading that
    cross-checks it.
    """
    return {
        "track": "resale",
        "score": card.score,
        "verdict": card.verdict,
        "confidence": card.confidence,
        "basis": card.basis,
        "ledger": [asdict(entry) for entry in card.ledger],
        "flags": list(card.flags),
        "fit": {
            "expected": round(card.expectation.expected),
            "lo": round(card.expectation.lo),
            "hi": round(card.expectation.hi),
            "z": round(card.expectation.z, 2),
            "discount_pct": round(card.expectation.discount_pct, 1),
            "location_pct": round(card.expectation.location_pct, 1),
            "location_comps": card.expectation.location_comps,
            "model": card.expectation.model,
        },
        "comp_ppsf": round(card.comp_ppsf) if card.comp_ppsf else None,
        "comp_discount_pct": (
            round(card.comp_discount_pct, 1) if card.comp_discount_pct is not None else None
        ),
        "agree": card.agree,
        "comps": [
            {
                "zpid": comp.zpid,
                "address": comp.address,
                "distance_mi": round(comp.distance_mi, 2),
                "price": round(comp.price),
                "sqft": round(comp.sqft),
                "ppsf": round(comp.ppsf),
                "lat": comp.lat,
                "lon": comp.lon,
            }
            for comp in card.comps
        ],
    }


def _deal_from_spec(card) -> dict:
    """One new-construction `ScoreCard`, in the same shape as a resale card.

    Deliberately the same top-level keys — score, verdict, confidence, ledger — so the
    page renders one card component rather than two, and a resale and a spec home read on
    one scale. What differs sits in its own sub-block: `ask` is the builder's own price
    list where `fit` would be a fitted surface, and there are no sold comps to quote
    because in a new subdivision there are no sales.
    """
    return {
        "track": "newcon",
        "score": card.score,
        "verdict": card.verdict,
        "confidence": card.confidence,
        "basis": "builder_ask",
        "ledger": [asdict(line) for line in card.ledger],
        "flags": [],
        "ask": {
            "ppsf": round(card.comp.ppsf) if card.comp.ppsf else None,
            "n_in_band": card.comp.n_in_band,
            "basis": card.comp.basis,
            "discount_pct": (
                round(card.discount_pct, 1) if card.discount_pct is not None else None
            ),
        },
        "comps": [],
    }


# -- one home ----------------------------------------------------------------------------


def _listing(
    row: dict,
    price_cut: dict | None,
    finance: FinanceAssumptions | None,
    quality,
    resale_deal: dict | None,
    spec_card,
) -> dict:
    """One home's row: the facts, what it costs to hold, and what the tool thinks of it.

    `deal` is None for every home on a page with no model, and for a home the feed never
    described well enough to judge even on a page that has one. Both are "not scored", and
    the page says so rather than filling the gap.
    """
    price, sqft = row.get("price"), row.get("sqft")
    carry = (
        monthly_cost(price, tax_rate=None, hoa_monthly=row.get("hoa_monthly"), fin=finance)
        if finance is not None
        else None
    )
    return {
        "zpid": row["zpid"],
        "address": row.get("address"),
        "lat": row.get("lat"),
        "lon": row.get("lon"),
        "price": price,
        "beds": row.get("beds"),
        "baths": row.get("baths"),
        "sqft": sqft,
        "sqft_max": row.get("sqft_max"),
        "lot_sqft": row.get("lot_sqft"),
        "year_built": row.get("year_built"),
        "home_type": row.get("home_type"),
        "price_per_sqft": (price / sqft) if price and sqft else None,
        "days_on_market": row.get("days_on_zillow"),
        "status": row.get("status_text") or row.get("listing_status"),
        "link": row.get("link"),
        "image_url": row.get("image_url"),
        "distance_miles": row.get("distance_miles"),
        "first_price": price_cut["first"] if price_cut else None,
        "price_cut_dollars": price_cut["cut_dollars"] if price_cut else None,
        "price_cut_pct": (
            round(price_cut["cut_pct"], 1)
            if price_cut and price_cut["cut_pct"] is not None
            else None
        ),
        "carry": asdict(carry) if carry else None,
        "track": "newcon" if is_spec(row) else "resale",
        "deal": _deal_from_spec(spec_card) if spec_card is not None else resale_deal,
        "quality": _quality(quality),
    }


def _quality(quality) -> dict | None:
    """What is known to be wrong with this record, and who built the house.

    Flag names travel as they are stored rather than as sentences: the page owns the
    vocabulary that turns `baths_corrected` into "bath count verified against the builder's
    plan page", because that is presentation, and this module emits data.
    """
    if quality is None:
        return None
    return {
        "flags": list(quality.flags),
        "corrections": dict(quality.corrections),
        "builder": quality.builder,
        "builder_tier": quality.builder_tier,
        "duplicate_of": quality.duplicate_of,
    }


def _is_duplicate(quality) -> bool:
    return quality is not None and quality.is_duplicate


def _rank(row: dict):
    """Best deal first; unscored homes after every scored one, cheapest first among them.

    An unscored home is not a bad deal, it is an unanswered question, so it sorts below the
    ranking rather than at the bottom of it — and within that group the ordering is price,
    which is at least a fact about the home.
    """
    deal = row["deal"]
    return (
        deal is None,
        -(deal["score"] if deal else 0.0),
        row["price"] is None,
        row["price"] or 0.0,
        row["address"] or "",
    )


# -- the blocks that describe the market rather than a home -------------------------------


def _sold_baseline(session: Session, sold_watch: str | None, generated_ts: str) -> dict | None:
    """The sold comps reduced to dollars per foot, with the basis label attached.

    None when there is no sold companion at all. A companion that exists but holds nothing
    still produces a block — with `n_solds: 0` — because "we looked and found no sales" and
    "we never looked" are different statements and the page makes different sentences of
    them.
    """
    if sold_watch is None:
        return None
    baseline = compute_sold_baseline(session, sold_watch, generated_ts)
    return asdict(baseline)


def _newcon(plan_baseline, specs: dict) -> dict | None:
    """The builder's price list, per community — or None where there is no builder.

    The plans themselves are not published into the payload: sixty-eight rows of ask curve
    are the *input* to a comparison, and what a reader needs is the summary plus the score
    each spec home already carries.
    """
    if not plan_baseline.n_plans:
        return None
    return {
        "n_plans": plan_baseline.n_plans,
        "n_specs": len(specs),
        "communities": [asdict(community) for community in plan_baseline.communities],
    }


def _curve(model, watch: Watch, homes: list[dict]) -> list[dict]:
    """The fair-price curve for the money chart: expected dollars per foot against size.

    Everything but size is held at the market's own median, and the location adjustment is
    deliberately switched off, so the line answers one question only — what does size alone
    do to price here — which is the question the chart is asking. A curve carrying each
    point's own neighbourhood premium would wander for reasons the x-axis does not show.
    """
    if model is None:
        return []
    sizes = sorted(h["sqft"] for h in homes if h.get("sqft"))
    if len(sizes) < 2:
        return []

    subject = {
        "beds": _median(h.get("beds") for h in homes) or 3.0,
        "baths": _median(h.get("baths") for h in homes) or 2.0,
        "home_type": "SINGLE_FAMILY",
        "lat": watch.lat,
        "lon": watch.lon,
        "price": 1.0,  # a placeholder ask: only `expected` is read off the result
    }
    if model.enriched_result is not None:
        year = _median(h.get("year_built") for h in homes)
        lot = _median(h.get("lot_sqft") for h in homes)
        if year and lot:
            subject |= {"year_built": int(year), "lot_sqft": float(lot)}

    lo, hi = sizes[0], sizes[-1]
    step = (hi - lo) / (CURVE_POINTS - 1)
    points = []
    for i in range(CURVE_POINTS):
        sqft = lo + step * i
        expectation = model.expected({**subject, "sqft": sqft}, adjust=False)
        if expectation is not None:
            points.append(
                {"sqft": round(sqft), "ppsf": round(expectation.expected / sqft)}
            )
    return points


def _sold_points(sold_rows: list[dict], basis: str | None) -> list[dict]:
    """Recent sales as scatter points: size against dollars per foot, and nothing else.

    Read on the model's own basis — real prices where the state discloses them, the
    post-sale re-anchored estimate where it does not — so the grey cloud behind the curve
    is made of the same numbers the curve was fitted to, and not a second, quieter opinion.
    """
    if basis is None:
        return []
    points = []
    for row in sold_rows:
        if is_plan_sheet(row):
            continue
        price = row.get("price") if basis == "disclosed" else row.get("zestimate")
        sqft = row.get("sqft")
        if not price or not sqft:
            continue
        points.append({"sqft": round(sqft), "ppsf": round(price / sqft)})
    return points


def _median(values: Iterable[float | None]) -> float | None:
    present = [v for v in values if v is not None]
    return statistics.median(present) if present else None
