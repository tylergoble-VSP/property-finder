"""The plan-sheet split and the ask curve built from it.

The plan names and sizes below are the real ones the seed sweep returned for Walsh —
sixty-eight plan rows across four community price lists — because the classifier is a
rule about scraped text and a made-up address would only prove it works on made-up text.
"""
from conftest import make_listing

from propertyfinder.dataquality import BATHS_CORRECTED
from propertyfinder.newcon import (
    CommunityAsk,
    compute_plan_baseline,
    is_plan_sheet,
    is_spec,
    plan_community,
    plan_name,
    score_specs,
)
from propertyfinder.store import latest_snapshot_rows, record_snapshot, upsert_property

NOW = "2026-07-10T00:00:00Z"
WATCH = "walsh-aledo"


def _plan(zpid: str, address: str, price: float, sqft: float):
    """One row of a builder's price list, as a sweep records it."""
    return make_listing(
        zpid, address=address, price=price, sqft=sqft, status_text="New construction"
    )


def _spec(zpid: str, address: str, price: float, sqft: float, dom: int = 10):
    """A real, buyable new home."""
    return make_listing(
        zpid,
        address=address,
        price=price,
        sqft=sqft,
        status_text="New construction",
        days_on_zillow=dom,
    )


def _record(sessions, listings, watch=WATCH, ts=NOW):
    with sessions() as s:
        for listing in listings:
            upsert_property(s, listing, ts)
        s.flush()
        for listing in listings:
            record_snapshot(s, listing, watch, ts, distance_miles=0.5)
        s.commit()


def _baseline(sessions, watch=WATCH):
    with sessions() as s:
        return compute_plan_baseline(s, watch)


def test_a_plan_row_is_told_from_a_home_by_its_address():
    assert is_plan_sheet({"address": "GRANTLEY Plan, Walsh Ranch 70'"})
    assert is_plan_sheet({"address": "The Kennedy II Plan, Walsh"})
    assert not is_plan_sheet({"address": "1820 Crested Ridge Rd, Aledo, TX 76008"})
    assert not is_plan_sheet({})


def test_a_plan_row_names_its_plan_and_its_community():
    assert plan_name("GRANTLEY Plan, Walsh Ranch 70'") == "GRANTLEY"
    assert plan_community("GRANTLEY Plan, Walsh Ranch 70'") == "Walsh Ranch 70'"
    assert plan_name("The Kennedy II Plan, Walsh") == "The Kennedy II"
    assert plan_community("Camborne Plan, Walsh Cottage") == "Walsh Cottage"
    # A street address is not a plan, however new the home on it is.
    assert plan_name("1820 Crested Ridge Rd, Aledo, TX 76008") is None
    assert plan_community(None) is None


def test_a_spec_is_new_construction_at_a_real_address():
    assert is_spec({"address": "1820 Crested Ridge Rd", "status_text": "New construction"})
    # Case is display text, not a promise.
    assert is_spec({"address": "1820 Crested Ridge Rd", "status_text": "NEW CONSTRUCTION"})
    # The price list is new construction too, and is emphatically not a spec home.
    assert not is_spec({"address": "GRANTLEY Plan, Walsh", "status_text": "New construction"})
    assert not is_spec({"address": "1820 Crested Ridge Rd", "status_text": "House for sale"})


def test_the_ask_curve_is_built_from_plan_rows_only(sessions):
    """Spec homes are what the curve judges, so they cannot also be what builds it."""
    _record(
        sessions,
        [
            _plan("p1", "BRENNER Plan, Walsh Ranch 60'", 552_200, 2761),
            _plan("p2", "BURKHART Plan, Walsh Ranch 60'", 758_800, 3794),
            _plan("p3", "Camborne Plan, Walsh Cottage", 542_600, 2713),
            _spec("s1", "1820 Crested Ridge Rd, Aledo, TX 76008", 694_245, 3482),
            make_listing("r1", price=674_900, sqft=3012, status_text="House for sale"),
        ],
    )
    baseline = _baseline(sessions)

    assert baseline.n_plans == 3
    assert {p.plan for p in baseline.plans} == {
        "BRENNER Plan, Walsh Ranch 60'",
        "BURKHART Plan, Walsh Ranch 60'",
        "Camborne Plan, Walsh Cottage",
    }


def test_communities_are_summarised_biggest_price_list_first(sessions):
    _record(
        sessions,
        [
            _plan("p1", "BRENNER Plan, Walsh Ranch 60'", 552_200, 2761),  # $200/sqft
            _plan("p2", "MARANDA Plan, Walsh Ranch 60'", 636_500, 2546),  # $250
            _plan("p3", "SYDNEY Plan, Walsh Ranch 60'", 673_800, 3369),  # $200
            _plan("p4", "Camborne Plan, Walsh Cottage", 542_600, 2713),  # $200
        ],
    )
    baseline = _baseline(sessions)

    assert [c.community for c in baseline.communities] == ["Walsh Ranch 60'", "Walsh Cottage"]
    walsh_60 = baseline.communities[0]
    assert walsh_60 == CommunityAsk("Walsh Ranch 60'", 3, 2761.0, 200.0)


def test_a_plan_missing_a_price_or_a_size_is_not_on_the_curve(sessions):
    _record(
        sessions,
        [
            _plan("p1", "BRENNER Plan, Walsh Ranch 60'", 552_200, 2761),
            _plan("p2", "MARANDA Plan, Walsh Ranch 60'", None, 2546),
            _plan("p3", "SYDNEY Plan, Walsh Ranch 60'", 673_800, None),
        ],
    )
    baseline = _baseline(sessions)

    assert baseline.n_plans == 1
    assert baseline.communities[0].n == 1


def test_the_comp_comes_from_plans_of_comparable_size(sessions):
    """Five plans at $200/sqft around 2,000 feet, one dear little plan far below the band:
    a 2,000-foot spec is compared against the five, not against all six."""
    _record(
        sessions,
        [
            *[
                _plan(f"p{i}", f"P{i} Plan, Walsh Ranch 60'", 400_000, 2000)
                for i in range(5)
            ],
            _plan("tiny", "Tiny Plan, Walsh Ranch 60'", 400_000, 1000),  # $400/sqft
        ],
    )
    comp = _baseline(sessions).comparable_ppsf(2000)

    assert comp.ppsf == 200.0
    assert comp.n_in_band == 5
    assert comp.basis == "band"


def test_a_thin_band_says_so_rather_than_widening_quietly(sessions):
    """No plan within ±20% of 9,000 feet, so the whole price list stands in — and the
    comp carries `n_in_band = 0` and a community basis to say the size match failed."""
    _record(
        sessions,
        [
            _plan(f"p{i}", f"P{i} Plan, Walsh Ranch 70'", 400_000 + i * 80_000, 2000 + i * 400)
            for i in range(5)
        ],
    )
    comp = _baseline(sessions).comparable_ppsf(9000)

    assert comp.ppsf is not None
    assert comp.n_in_band == 0
    assert comp.basis == "community"


def test_a_home_with_no_size_falls_back_the_same_way(sessions):
    _record(sessions, [_plan("p1", "BRENNER Plan, Walsh Ranch 60'", 552_200, 2761)])
    assert _baseline(sessions).comparable_ppsf(None).basis == "community"


def test_a_watch_with_no_price_list_has_no_comp_to_give(sessions):
    """A resale-only market is not an error; it simply has no Track B answer."""
    _record(sessions, [make_listing("r1", price=674_900, sqft=3012)])
    baseline = _baseline(sessions)

    assert baseline.n_plans == 0
    assert baseline.communities == ()
    assert baseline.comparable_ppsf(3000) == (None, 0, "none")


# -- scoring a spec against that curve --------------------------------------------------

# Five plans at 2,000 feet and $200 a foot: a price list specific enough to judge a
# 2,000-foot spec home against.
PRICE_LIST = [
    (f"p{i}", f"P{i} Plan, Walsh Ranch 60'", 400_000, 2000) for i in range(5)
]


def _scored(sessions, watch=WATCH, **kwargs):
    with sessions() as s:
        cards = score_specs(s, watch, compute_plan_baseline(s, watch), **kwargs)
    return {card.zpid: card for card in cards}


def _stored(sessions, zpid, watch=WATCH):
    """The row as the database still holds it — corrections must never have reached it."""
    with sessions() as s:
        return next(r for r in latest_snapshot_rows(s, watch) if r["zpid"] == zpid)


def test_a_spec_under_the_builders_own_ask_and_sitting_unsold_is_a_deal(sessions):
    _record(
        sessions,
        [
            *[_plan(*p) for p in PRICE_LIST],
            # 10% under the plan ask for its size, and three months unsold.
            _spec("cheap", "1820 Crested Ridge Rd, Aledo, TX 76008", 360_000, 2000, dom=90),
            # The list price, fresh on the market.
            _spec("full", "1901 Crested Ridge Rd, Aledo, TX 76008", 400_000, 2000, dom=10),
        ],
    )
    cards = _scored(sessions)

    cheap = cards["cheap"]
    assert round(cheap.discount_pct) == 10
    # 50 + (4 × 10, capped at 25) + 8 for sitting = 83
    assert cheap.score == 83.0
    assert cheap.verdict == "GREAT"
    assert cheap.confidence == "HIGH"  # five plans of comparable size set the comp
    assert cheap.ledger_total() == cheap.score, "the ledger sums to the score exactly"
    assert [line.label for line in cheap.ledger] == [
        "Starting point",
        "Against the builder's ask",
        "Sitting unsold",
    ]

    full = cards["full"]
    assert full.score == 50.0 and full.verdict == "FAIR"


def test_asking_more_than_the_price_list_costs_points(sessions):
    _record(
        sessions,
        [
            *[_plan(*p) for p in PRICE_LIST],
            _spec("dear", "1928 Crested Ridge Rd, Aledo, TX 76008", 480_000, 2000),
        ],
    )
    card = _scored(sessions)["dear"]

    assert round(card.discount_pct) == -20
    assert card.score == 25.0  # 50 − 25, the cap biting in both directions
    assert card.verdict == "OVERPRICED"
    assert "capped" in card.ledger[1].detail


def test_an_observed_cut_is_worth_more_than_a_shallow_one(sessions):
    """Cuts come out of history, so this is two sweeps: a first ask, then a lower one.

    Both sweeps carry the whole market, price list included, because that is what a sweep
    is — every row the watch saw that morning. A fixture that recorded the plan sheets in a
    sweep of their own would be describing a market where the builder's price list and its
    houses were never on sale at the same time.
    """
    _record(
        sessions,
        [
            *[_plan(*p) for p in PRICE_LIST],
            _spec("deep", "1725 Crested Ridge Rd, Aledo, TX 76008", 400_000, 2000),
            _spec("shallow", "1727 Crested Ridge Rd, Aledo, TX 76008", 400_000, 2000),
        ],
        ts="2026-07-01T00:00:00Z",
    )
    _record(
        sessions,
        [
            *[_plan(*p) for p in PRICE_LIST],
            _spec("deep", "1725 Crested Ridge Rd, Aledo, TX 76008", 380_000, 2000),  # −5%
            _spec("shallow", "1727 Crested Ridge Rd, Aledo, TX 76008", 396_000, 2000),  # −1%
        ],
        ts="2026-07-08T00:00:00Z",
    )
    cards = _scored(sessions)

    # deep: 50 + (5% under the ask × 4 = 20) + 12 for the cut
    assert cards["deep"].score == 82.0
    assert cards["deep"].ledger[-1].points == 12.0
    # shallow: 50 + (1% × 4) + 8
    assert cards["shallow"].score == 62.0
    assert cards["shallow"].ledger[-1].points == 8.0


def test_a_thin_price_list_scores_but_says_it_is_unsure(sessions):
    _record(
        sessions,
        [
            _plan("p1", "BRENNER Plan, Walsh Ranch 60'", 552_200, 2761),
            _spec("s1", "1820 Crested Ridge Rd, Aledo, TX 76008", 694_245, 3482),
        ],
    )
    card = _scored(sessions)["s1"]

    assert card.confidence == "LOW"
    assert card.comp.basis == "community"
    assert "no plan being close in size" in card.ledger[1].detail


def test_a_spec_the_feed_priced_at_nothing_is_not_scored_on_a_guess(sessions):
    _record(
        sessions,
        [
            *[_plan(*p) for p in PRICE_LIST],
            _spec("nopricing", "1820 Crested Ridge Rd, Aledo, TX 76008", None, 2000),
        ],
    )
    card = _scored(sessions)["nopricing"]

    assert card.discount_pct is None
    assert card.score == 50.0
    assert "not scored" in card.ledger[1].detail


def test_plan_rows_and_resales_are_not_spec_homes(sessions):
    _record(
        sessions,
        [
            *[_plan(*p) for p in PRICE_LIST],
            make_listing("resale", price=674_900, sqft=3012, status_text="House for sale"),
        ],
    )
    assert _scored(sessions) == {}


def test_a_home_the_feed_listed_twice_takes_one_place_on_the_board(sessions):
    """The real incident, scored: 1820 Crested Ridge Rd and 1820 Crested Rdg are one
    house at one price, and a leaderboard showing both is wrong where readers can see."""
    _record(
        sessions,
        [
            *[_plan(*p) for p in PRICE_LIST],
            _spec("345829319", "1820 Crested Ridge Rd, Aledo, TX 76008", 360_000, 2000),
            _spec("464003071", "1820 Crested Rdg, Fort Worth, TX 76008", 360_000, 2000),
        ],
    )
    cards = _scored(sessions)

    assert set(cards) == {"345829319"}


def test_a_verified_bath_count_is_what_the_card_reports(sessions):
    """The feed says four baths; the builder's plan page says three and a half. The card
    a reader sees carries the corrected number, and the stored observation is untouched."""
    _record(
        sessions,
        [
            *[_plan(*p) for p in PRICE_LIST],
            _spec("s1", "1820 Crested Ridge Rd, Aledo, TX 76008", 360_000, 2000),
        ],
    )
    corrections = {
        "s1": {"listed": 3.0, "verified": 2.5, "source": "builder plan page",
               "verified_on": "2026-07-26"}
    }
    card = _scored(sessions, corrections=corrections)["s1"]

    assert card.baths == 2.5
    assert card.quality.has(BATHS_CORRECTED)
    assert card.quality.corrections["baths"]["listed"] == 3.0
    assert _stored(sessions, "s1")["baths"] == 3.0


def test_no_estimate_or_rent_figure_can_reach_the_score(sessions):
    """Two identical spec homes, one carrying a large published estimate and a rent
    figure. The scores must be identical — this is the rule the original tool broke."""
    _record(
        sessions,
        [
            *[_plan(*p) for p in PRICE_LIST],
            _spec("plain", "1820 Crested Ridge Rd, Aledo, TX 76008", 360_000, 2000),
            make_listing(
                "estimated",
                address="1901 Crested Ridge Rd, Aledo, TX 76008",
                price=360_000,
                sqft=2000,
                status_text="New construction",
                days_on_zillow=10,
                zestimate=900_000,
                rent_zestimate=6_000,
            ),
        ],
    )
    cards = _scored(sessions)

    assert cards["plain"].score == cards["estimated"].score


# -- on the market today, not ever ------------------------------------------------------
#
# The 180-homes-when-145-were-live bug (docs/PORTING-THE-REPORTS.md, lesson 2), proved
# closed in the two functions where it was still reachable. Both read
# `latest_snapshot_rows`, which is the right query for history and the wrong one for a page
# dated today, and both now filter to the current sweep inside the module — so no caller
# has to re-learn the distinction.


def test_a_withdrawn_plan_leaves_the_ask_curve(sessions):
    """A price the builder has stopped asking cannot set the yardstick for anything."""
    yesterday, today = "2026-07-01T00:00:00Z", "2026-07-08T00:00:00Z"
    _record(
        sessions,
        [*[_plan(*p) for p in PRICE_LIST], _plan("gone", "GONE Plan, Walsh Ranch 60'", 4_000_000, 2000)],
        ts=yesterday,
    )
    _record(sessions, [_plan(*p) for p in PRICE_LIST], ts=today)

    baseline = _baseline(sessions)

    assert baseline.n_plans == len(PRICE_LIST)  # not len + 1
    assert "GONE Plan, Walsh Ranch 60'" not in [p.plan for p in baseline.plans]
    # And the curve is not dragged by a plan nobody can buy: five plans at $200/sf.
    assert baseline.comparable_ppsf(2000).ppsf == 200.0


def test_a_delisted_spec_home_is_not_scored(sessions):
    """A leaderboard of homes for sale must not rank one that sold a fortnight ago."""
    yesterday, today = "2026-07-01T00:00:00Z", "2026-07-08T00:00:00Z"
    live = _spec("live", "1725 Crested Ridge Rd, Aledo, TX 76008", 380_000, 2000)
    sold = _spec("sold", "1727 Crested Ridge Rd, Aledo, TX 76008", 360_000, 2000)
    _record(sessions, [*[_plan(*p) for p in PRICE_LIST], live, sold], ts=yesterday)
    _record(sessions, [*[_plan(*p) for p in PRICE_LIST], live], ts=today)

    cards = _scored(sessions)

    assert sorted(cards) == ["live"]


def test_a_duplicate_is_still_caught_when_its_twin_was_last_seen_a_sweep_ago(sessions):
    """Why the filter is on the scoring loop and not on the query.

    Data quality reads the whole of history on purpose: a home the feed invented under a
    second zpid has to be measured against the record it duplicates, and that record may
    not have been sighted this morning.
    """
    yesterday, today = "2026-07-01T00:00:00Z", "2026-07-08T00:00:00Z"
    keeper = _spec("keeper", "1820 Crested Ridge Rd, Aledo, TX 76008", 400_000, 2000)
    twin = _spec("twin", "1820 Crested Rdg, Fort Worth, TX 76008", 400_000, 2000)
    _record(sessions, [*[_plan(*p) for p in PRICE_LIST], keeper], ts=yesterday)
    _record(sessions, [*[_plan(*p) for p in PRICE_LIST], twin], ts=today)

    cards = _scored(sessions)

    assert cards == {}, "the twin is dropped, and it was the only home in today's sweep"
