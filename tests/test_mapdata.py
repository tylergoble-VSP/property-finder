"""The map payload, in both of the states it has to survive: fitted, and not.

The interesting half of this module is the second one. A watch with no sold companion, a
sold companion with four sales in it, a market whose builder publishes nothing — each is
an ordinary Tuesday, and each must produce a whole payload with an honest hole in it
rather than an exception. So every degradation gets a test that reads the reason out loud.

The fitted half is held to one invariant above all others: a card's ledger sums to its
score *after* the trip through the payload. `test_deals` proves it of the dataclass; this
proves the dictionary the page will actually read did not lose a line on the way out.
"""
import math
from dataclasses import asdict

import pytest
from conftest import make_listing

from propertyfinder.config import (
    FinanceAssumptions,
    SpecialAssessment,
    Watch,
    WatchConfig,
)
from propertyfinder.costmodel import monthly_cost
from propertyfinder.mapdata import build_map_payload, sold_companion
from propertyfinder.store import record_snapshot, upsert_property

T1, T2 = "2026-07-10T10:00:00Z", "2026-07-11T10:00:00Z"
GENERATED = "2026-07-11T12:00:00Z"

WALSH_FINANCE = FinanceAssumptions(
    default_tax_rate=2.339427,
    tax_rate_citation="Tax year 2025 adopted rates, verified 2026-08-06.",
    special_assessment=SpecialAssessment(flat_annual=3271.0, citation="May 2026 update."),
)


def _watch(name="walsh-aledo", status="for_sale") -> Watch:
    return Watch(
        name=name,
        center_address="2112 Eastus Ln, Aledo, TX 76008",
        lat=32.73665,
        lon=-97.55626,
        radius_miles=2.0,
        listing_status=status,
        queries=["Aledo, TX 76008"],
    )


WATCH = _watch()
SOLD = _watch("walsh-aledo-sold", "sold")


def _config(*watches, finance: FinanceAssumptions | None = None) -> WatchConfig:
    return WatchConfig(finance=finance or FinanceAssumptions(), watches=list(watches))


def _record(sessions, listings, watch_name=WATCH.name, ts=T1, status=None) -> None:
    with sessions() as s:
        for listing in listings:
            upsert_property(s, listing, ts)
        s.flush()
        for listing in listings:
            record_snapshot(s, listing, watch_name, ts, distance_miles=0.8,
                            listing_status=status)
        s.commit()


# -- a market whose answer is known in advance -------------------------------------------


def _priced(sqft: float, baths: float = 3.0) -> float:
    """The surface the synthetic sales obey — $200 a foot at 2,400 feet, elasticity 0.83.

    The same rule `test_stats` generates its market from, restated rather than imported,
    because a payload test that shared a generator with a model test would fail for two
    unrelated reasons at once.
    """
    return 200 * 2400 * (sqft / 2400) ** 0.83 * 1.04 ** (baths - 3.0)


def _sold_market(n=40):
    """Forty closed sales on that surface, with a deterministic wobble and real spread.

    Forty rather than the twenty the fit needs: past twenty-five the spatial residual
    field is built too, so the payload under test is the one the tool actually ships.
    """
    listings = []
    for i in range(n):
        sqft = 1800 + (i % 20) * 120
        baths = 2.0 + (i % 3)
        price = _priced(sqft, baths) * (1 + 0.13 * math.sin(i))
        listings.append(
            make_listing(
                f"s{i}",
                address=f"{100 + i} Sold Ln, Aledo, TX 76008",
                price=round(price),
                sqft=sqft,
                baths=baths,
                lat=32.7300 + (i % 7) * 0.002,
                lon=-97.5500 + (i % 5) * 0.002,
                listing_status="sold",
                status_text="Sold",
                date_sold="2026-06-01",
            )
        )
    return listings


@pytest.fixture
def fitted(sessions):
    """A watch with three homes — fair, cheap and dear — and forty sales behind them."""
    _record(sessions, _sold_market(), watch_name=SOLD.name, status="sold")
    _record(
        sessions,
        [
            make_listing("fair", address="1 Fair St", price=round(_priced(3000)), sqft=3000),
            make_listing(
                "cheap", address="2 Cheap St", price=round(_priced(2600) * 0.80), sqft=2600
            ),
            make_listing(
                "dear", address="3 Dear St", price=round(_priced(3400) * 1.30), sqft=3400
            ),
        ],
    )
    return sessions


def _payload(sessions, cfg=None, watch=WATCH) -> dict:
    cfg = cfg or _config(WATCH, SOLD, finance=WALSH_FINANCE)
    with sessions() as s:
        return build_map_payload(s, watch, cfg, GENERATED)


# -- the sold companion, found by convention ---------------------------------------------


def test_the_sold_companion_is_found_by_name_and_absent_when_it_is_not_configured():
    assert sold_companion(_config(WATCH, SOLD), WATCH) == "walsh-aledo-sold"
    assert sold_companion(_config(WATCH), WATCH) is None


# -- the fitted payload -------------------------------------------------------------------


def test_the_fitted_payload_has_the_shape_the_template_reads(fitted):
    payload = _payload(fitted)

    assert set(payload) == {
        "watch", "criteria", "generated_ts", "sweep_ts", "counts", "medians", "finance",
        "model", "sold_baseline", "listings", "movement", "newcon", "curve", "solds",
    }
    assert payload["model"]["fitted"] is True
    assert payload["model"]["n"] == 40
    assert payload["model"]["r2"] > 0.75
    assert payload["model"]["basis"] == "disclosed"
    assert payload["model"]["kind"] == "base"  # nothing enriched, so no age or lot terms
    assert payload["model"]["located"] is True  # forty sales is enough to smooth over
    assert payload["model"]["reason"] is None
    assert payload["model"]["notes"], "the fit has to be able to explain itself"


def test_every_scored_home_carries_a_ledger_that_sums_to_its_own_score(fitted):
    """The invariant `test_deals` proves of the dataclass, reasserted at the boundary the
    page actually reads: a line lost in serialisation is a page whose arithmetic lies."""
    payload = _payload(fitted)
    scored = [row for row in payload["listings"] if row["deal"]]

    assert len(scored) == 3
    for row in scored:
        total = sum(line["points"] for line in row["deal"]["ledger"])
        assert total == pytest.approx(row["deal"]["score"], abs=0.05), row["zpid"]
        assert all(line["label"] and line["detail"] for line in row["deal"]["ledger"])


def test_a_scored_home_carries_the_whole_card_and_the_sales_behind_it(fitted):
    payload = _payload(fitted)
    cheap = next(r for r in payload["listings"] if r["zpid"] == "cheap")

    assert set(cheap["deal"]) == {
        "track", "score", "verdict", "confidence", "basis", "ledger", "flags",
        "fit", "comp_ppsf", "comp_discount_pct", "agree", "comps",
    }
    assert cheap["deal"]["track"] == "resale"
    assert cheap["deal"]["verdict"] in ("GREAT", "GOOD")
    assert cheap["deal"]["basis"] == "disclosed"
    assert cheap["deal"]["fit"]["z"] < -1
    assert cheap["deal"]["fit"]["expected"] > cheap["price"]
    assert cheap["deal"]["comps"], "a card without its comps cannot be checked by a reader"
    assert all("address" in comp and "ppsf" in comp for comp in cheap["deal"]["comps"])


def test_the_ranking_puts_the_best_deal_first_and_the_dearest_last(fitted):
    payload = _payload(fitted)
    assert [r["zpid"] for r in payload["listings"]] == ["cheap", "fair", "dear"]
    assert payload["counts"]["scored"] == 3
    assert payload["counts"]["underpriced"] == 1
    assert payload["counts"]["great_or_good"] >= 1


def test_the_sold_baseline_travels_with_its_basis_label(fitted):
    payload = _payload(fitted)
    baseline = payload["sold_baseline"]

    assert baseline["watch_name"] == "walsh-aledo-sold"
    assert baseline["n_solds"] == 40
    assert baseline["basis"] == "disclosed" and baseline["price_disclosed"] == 40
    assert baseline["segments"]["all"]["p50"] > 0


def test_the_money_chart_gets_a_curve_and_the_sales_it_was_fitted_to(fitted):
    payload = _payload(fitted)

    assert len(payload["curve"]) == 40
    assert payload["curve"][0]["sqft"] == 2600 and payload["curve"][-1]["sqft"] == 3400
    # The whole reason the chart exists: dollars per foot FALL as homes grow, so a raw
    # price-per-foot ranking hands back a list of big houses and calls them bargains.
    assert payload["curve"][0]["ppsf"] > payload["curve"][-1]["ppsf"]
    assert len(payload["solds"]) == 40


def test_a_home_carries_its_carry_and_the_dues_the_detail_engine_found(fitted, sessions):
    payload = _payload(fitted)
    fair = next(r for r in payload["listings"] if r["zpid"] == "fair")
    direct = monthly_cost(fair["price"], None, None, WALSH_FINANCE)

    assert fair["carry"] == asdict(direct)
    assert fair["carry"]["assessment_basis"] == "flat"  # dollars per lot, not a rate
    assert fair["carry"]["tax_rate_used"] == 2.339427


# -- degradation: every way this page can be short of something ---------------------------


def test_a_watch_with_no_sold_companion_still_builds_a_whole_page(sessions):
    _record(sessions, [make_listing("111", price=500_000)])
    payload = _payload(sessions, _config(WATCH))

    assert payload["model"]["fitted"] is False
    assert "-sold" in payload["model"]["reason"]
    assert payload["sold_baseline"] is None
    assert payload["curve"] == [] and payload["solds"] == []
    assert len(payload["listings"]) == 1
    assert payload["listings"][0]["deal"] is None
    assert payload["counts"]["scored"] == 0


def test_a_sold_companion_with_too_few_sales_says_how_few_and_scores_nothing(sessions):
    _record(sessions, _sold_market(n=6), watch_name=SOLD.name, status="sold")
    _record(sessions, [make_listing("111", price=500_000, sqft=3000)])
    payload = _payload(sessions)

    assert payload["model"]["fitted"] is False
    assert "too few usable sales" in payload["model"]["reason"]
    assert payload["listings"][0]["deal"] is None
    # ...but the sold side was looked at, and what it holds is still published.
    assert payload["sold_baseline"]["n_solds"] == 6
    assert payload["curve"] == []


def test_an_empty_database_is_an_honest_empty_payload(sessions):
    payload = _payload(sessions, _config(WATCH))

    assert payload["sweep_ts"] is None
    assert payload["listings"] == []
    assert payload["counts"]["active"] == 0
    assert payload["medians"] == {
        "price": None, "price_per_sqft": None, "days_on_market": None, "carry": None
    }
    assert payload["movement"]["history_began"] is False
    assert payload["newcon"] is None


def test_the_degraded_payload_snapshots_cleanly(sessions):
    """A whole-payload equality check on the state with the least in it — because the
    shape of the dictionary is the contract the template is written against, and a page
    built from a market this bare is the one most likely to hit a missing key."""
    _record(
        sessions,
        [
            make_listing(
                "111",
                address="111 Tolleson Dr, Aledo, TX 76008",
                price=674_900.0,
                beds=4,
                baths=3,
                sqft=3012,
                days_on_zillow=27,
                status_text="House for sale",
                link="https://www.zillow.com/homedetails/111_zpid/",
            )
        ],
    )
    payload = _payload(sessions, _config(WATCH, finance=WALSH_FINANCE))
    carry = asdict(monthly_cost(674_900.0, None, None, WALSH_FINANCE))

    assert payload == {
        "watch": {
            "name": "walsh-aledo",
            "center_address": "2112 Eastus Ln, Aledo, TX 76008",
            "lat": 32.73665,
            "lon": -97.55626,
            "radius_miles": 2.0,
            "listing_status": "for_sale",
            "subdivision": None,
            "sold_watch": None,
        },
        "generated_ts": GENERATED,
        "sweep_ts": T1,
        "criteria": {
            "declared": False,
            "describe": [],
            "considered": 1,
            "kept": 1,
            "dropped": 0,
            "reasons": [],
        },
        "counts": {
            "active": 1,
            "considered": 1,
            "screened_out": 0,
            "scored": 0,
            "great_or_good": 0,
            "underpriced": 0,
            "plan_sheets": 0,
            "duplicates_dropped": 0,
            "enriched": 0,
        },
        "medians": {
            "price": 674_900.0,
            "price_per_sqft": 674_900.0 / 3012,
            "days_on_market": 27,
            "carry": carry["total"],
        },
        "finance": WALSH_FINANCE.model_dump(),
        "model": {
            "fitted": False,
            "n": None,
            "r2": None,
            "sigma": None,
            "basis": None,
            "kind": None,
            "located": False,
            "notes": [],
            "reason": payload["model"]["reason"],  # asserted for content elsewhere
        },
        "sold_baseline": None,
        "listings": [
            {
                "zpid": "111",
                "address": "111 Tolleson Dr, Aledo, TX 76008",
                "lat": 32.741913,
                "lon": -97.560241,
                "price": 674_900.0,
                "beds": 4,
                "baths": 3,
                "sqft": 3012,
                "sqft_max": None,
                "lot_sqft": None,
                "year_built": None,
                "home_type": "SINGLE_FAMILY",
                "price_per_sqft": 674_900.0 / 3012,
                "days_on_market": 27,
                "status": "House for sale",
                "link": "https://www.zillow.com/homedetails/111_zpid/",
                "image_url": None,
                "distance_miles": 0.8,
                "first_price": None,
                "price_cut_dollars": None,
                "price_cut_pct": None,
                "carry": carry,
                "track": "resale",
                "deal": None,
                "quality": {
                    "flags": [],
                    "corrections": {},
                    "builder": None,
                    "builder_tier": "UNRESOLVED",
                    "duplicate_of": None,
                },
            }
        ],
        "movement": {
            "new": [], "cuts": [], "rises": [], "status_changes": [], "gone": [],
            "history_began": False,
        },
        "newcon": None,
        "curve": [],
        "solds": [],
    }


# -- what the payload refuses to put on the map -------------------------------------------


def test_a_builder_plan_sheet_is_an_ask_curve_and_never_a_listing(sessions):
    _record(
        sessions,
        [
            make_listing("h1", address="10 Real St", price=700_000, sqft=3000,
                         status_text="New construction"),
            make_listing("p1", address="GRANTLEY Plan, Walsh Ranch 70'", price=780_000,
                         sqft=3400, status_text="New construction"),
            make_listing("p2", address="BRINKLEY Plan, Walsh Ranch 70'", price=690_000,
                         sqft=3050, status_text="New construction"),
            make_listing("p3", address="CAMBORNE Plan, Walsh Ranch 70'", price=640_000,
                         sqft=2800, status_text="New construction"),
        ],
    )
    payload = _payload(sessions, _config(WATCH))

    assert [r["zpid"] for r in payload["listings"]] == ["h1"]
    assert payload["counts"]["plan_sheets"] == 3
    assert payload["newcon"]["n_plans"] == 3
    assert payload["newcon"]["communities"][0]["community"] == "Walsh Ranch 70'"
    assert payload["newcon"]["communities"][0]["n"] == 3


def test_a_spec_home_is_scored_against_the_builders_ask_not_against_sales(sessions):
    _record(sessions, _sold_market(), watch_name=SOLD.name, status="sold")
    _record(
        sessions,
        [
            make_listing("spec", address="10 Real St", price=640_000, sqft=3000,
                         status_text="New construction"),
            make_listing("p1", address="GRANTLEY Plan, Walsh Ranch 70'", price=780_000,
                         sqft=3400, status_text="New construction"),
            make_listing("p2", address="BRINKLEY Plan, Walsh Ranch 70'", price=750_000,
                         sqft=3050, status_text="New construction"),
            make_listing("p3", address="CAMBORNE Plan, Walsh Ranch 70'", price=730_000,
                         sqft=2900, status_text="New construction"),
        ],
    )
    payload = _payload(sessions)
    spec = next(r for r in payload["listings"] if r["zpid"] == "spec")

    assert spec["track"] == "newcon"
    assert spec["deal"]["track"] == "newcon"
    assert spec["deal"]["basis"] == "builder_ask"
    assert spec["deal"]["ask"]["discount_pct"] > 0  # under the builder's own list
    assert spec["deal"]["comps"] == []  # a new subdivision has no sales to quote
    assert sum(line["points"] for line in spec["deal"]["ledger"]) == pytest.approx(
        spec["deal"]["score"], abs=0.05
    )


def test_a_home_the_feed_listed_twice_appears_once_and_the_page_is_told_how_often(sessions):
    _record(
        sessions,
        [
            make_listing("real", address="1820 Crested Ridge Rd, Aledo, TX 76008",
                         price=850_000, sqft=3600, lot_sqft=9000),
            make_listing("twin", address="1820 Crested Rdg, Fort Worth, TX 76008",
                         price=850_000, sqft=3600),
        ],
    )
    payload = _payload(sessions, _config(WATCH))

    assert [r["zpid"] for r in payload["listings"]] == ["real"]
    assert payload["counts"]["duplicates_dropped"] == 1
    assert payload["counts"]["active"] == 1


def test_a_home_gone_since_the_last_sweep_is_not_on_the_map(sessions):
    _record(sessions, [make_listing("111", price=500_000), make_listing("222", price=700_000)])
    _record(sessions, [make_listing("111", price=465_000)], ts=T2)
    payload = _payload(sessions, _config(WATCH))

    assert [r["zpid"] for r in payload["listings"]] == ["111"]
    assert payload["sweep_ts"] == T2
    assert [g["zpid"] for g in payload["movement"]["gone"]] == ["222"]
    assert payload["listings"][0]["price_cut_dollars"] == 35_000
    assert payload["listings"][0]["price_cut_pct"] == 7.0


def test_the_module_builds_data_and_never_markup():
    """The rule this whole stage exists to enforce (docs/REBUILD.md, post-mortem item 1)."""
    from pathlib import Path

    from propertyfinder import mapdata

    source = Path(mapdata.__file__).read_text()
    for marker in ("<html", "<div", "<script", "<style", "<table", "</"):
        assert marker not in source, f"{marker!r} found in mapdata.py"
