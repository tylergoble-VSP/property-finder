"""The plan-sheet split and the ask curve built from it.

The plan names and sizes below are the real ones the seed sweep returned for Walsh —
sixty-eight plan rows across four community price lists — because the classifier is a
rule about scraped text and a made-up address would only prove it works on made-up text.
"""
from conftest import make_listing

from propertyfinder.newcon import (
    CommunityAsk,
    compute_plan_baseline,
    is_plan_sheet,
    is_spec,
    plan_community,
    plan_name,
)
from propertyfinder.store import record_snapshot, upsert_property

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
