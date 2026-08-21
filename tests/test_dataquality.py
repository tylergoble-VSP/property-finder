"""Each of the feed's known lies, reproduced from the incident that taught it to us.

The rows below are not invented: the plan names, footages, prices and misspelled street
are the ones the seed sweeps actually returned. That matters more here than anywhere else
in the suite — a detection tuned against imaginary data is a detection that has never met
the thing it claims to catch.
"""
from collections import Counter

import pytest

from propertyfinder.dataquality import (
    BATH_CORRECTION_STALE,
    BATHS_CORRECTED,
    CONFIRMED,
    DUPLICATE_LISTING,
    INFERRED,
    RESEARCHED,
    SQFT_IS_BASE_OF_RANGE,
    SUSPECT_HALF_BATH_ROUNDUP,
    UNRESOLVED,
    apply_corrections,
    assess,
    attribute_builder,
    bath_corrections,
    builder_attributions,
    find_duplicates,
    plan_sqft_ranges,
    same_address,
)


def _row(zpid, address, **fields):
    """One stored row, in the shape `store.latest_snapshot_rows` returns."""
    return {
        "zpid": zpid,
        "address": address,
        "listing_status": "for_sale",
        "status_text": "New construction",
        "price": None,
        "sqft": None,
        "beds": None,
        "baths": None,
        "first_seen": "2026-07-11T00:00:00Z",
        **fields,
    }


# -- (a) the half-bath round-up ---------------------------------------------------------


def test_the_correction_files_carry_their_provenance():
    """A correction without a source and a date is tribal knowledge in a YAML costume."""
    for entry in bath_corrections().values():
        assert entry["source"] and entry["verified_on"]
        assert entry["verified"] == entry["listed"] - 0.5  # the feed rounds up, only up
    for entry in plan_sqft_ranges().values():
        assert entry["source"] and entry["verified_on"]
        assert entry["max"] > entry["base"]


def test_a_verified_bath_count_travels_with_the_record_and_corrects_a_copy():
    """GRANTLEY: the feed says five baths, the builder's plan page says four and a half."""
    row = _row("p1", "GRANTLEY Plan, Walsh Ranch 70'", price=824_200, sqft=4121, baths=5.0)

    quality = assess([row])["p1"]

    assert quality.has(BATHS_CORRECTED)
    assert quality.corrections["baths"]["listed"] == 5.0
    assert quality.corrections["baths"]["verified"] == 4.5
    assert quality.corrections["baths"]["source"]

    corrected = apply_corrections(row, quality)
    assert corrected["baths"] == 4.5
    assert row["baths"] == 5.0, "the observation the feed gave is never edited in place"
    assert corrected is not row


def test_a_correction_the_feed_no_longer_matches_is_stale_and_is_not_applied():
    """The builder revised the plan to four baths. Our note says 'listed 5' — so the note
    describes a listing that no longer exists, and applying it would invent a number."""
    row = _row("p1", "GRANTLEY Plan, Walsh Ranch 70'", price=824_200, sqft=4121, baths=4.0)

    quality = assess([row])["p1"]

    assert quality.has(BATH_CORRECTION_STALE)
    assert not quality.has(BATHS_CORRECTED)
    assert quality.corrections["baths"]["verified"] is None
    assert apply_corrections(row, quality)["baths"] == 4.0


def test_one_half_bath_in_a_price_list_makes_its_whole_number_siblings_suspect():
    """The feed printed a .5 for one plan in this community, which proves it can. Every
    whole-number plan beside it is then suspect — flagged for verification, not corrected.
    """
    rows = [
        _row("a", "PRESLEY III Plan, Walsh Ranch 70'", price=590_200, sqft=2951, baths=3.5),
        _row("b", "ELLIOT Plan, Walsh Ranch 70'", price=623_000, sqft=3115, baths=4.0),
        _row("c", "OVERLOOK Plan, Walsh Ranch 70'", price=656_200, sqft=3281, baths=5.0),
        # A different price list, with no half-bath in it: nothing is proved there.
        _row("d", "MARANDA Plan, Walsh Ranch 60'", price=509_200, sqft=2546, baths=3.0),
        # A spec home is not a plan row, and this heuristic is about price lists.
        _row("e", "1820 Crested Ridge Rd, Aledo, TX 76008", price=694_245, sqft=3482, baths=4.0),
    ]

    quality = assess(rows)

    assert quality["b"].has(SUSPECT_HALF_BATH_ROUNDUP)
    assert quality["c"].has(SUSPECT_HALF_BATH_ROUNDUP)
    assert not quality["a"].has(SUSPECT_HALF_BATH_ROUNDUP)  # the one that printed a half
    assert not quality["d"].has(SUSPECT_HALF_BATH_ROUNDUP)
    assert not quality["e"].has(SUSPECT_HALF_BATH_ROUNDUP)
    # Suspicion is not correction: nothing here changes a bath count.
    assert apply_corrections(rows[1], quality["b"])["baths"] == 4.0


# -- (b) base versus maximum square footage ---------------------------------------------


def test_a_plan_row_says_its_footage_is_a_floor_and_carries_the_ceiling_when_known():
    """4,121 listed, 4,896 real — 775 square feet of headroom the listing never mentions."""
    plan = _row("p1", "GRANTLEY Plan, Walsh Ranch 70'", price=824_200, sqft=4121, baths=5.0)
    spec = _row("s1", "1820 Crested Ridge Rd, Aledo, TX 76008", price=694_245, sqft=3482)

    quality = assess([plan, spec])

    assert quality["p1"].has(SQFT_IS_BASE_OF_RANGE)
    assert quality["p1"].corrections["sqft"] == {
        "listed": 4121,
        "sqft_max": 4896,
        "source": "builder plan page",
        "verified_on": "2026-07-26",
    }
    corrected = apply_corrections(plan, quality["p1"])
    assert corrected["sqft"] == 4121, "the base is a true number about a real build"
    assert corrected["sqft_max"] == 4896

    # A spec home is a built house, not a range, and is never flagged.
    assert not quality["s1"].has(SQFT_IS_BASE_OF_RANGE)
    assert "sqft" not in quality["s1"].corrections


def test_a_plan_with_no_looked_up_range_is_still_flagged():
    """The flag is a fact about the feed; the ceiling is a fact somebody had to go and
    read. The first does not wait for the second."""
    plan = _row("p1", "MARANDA Plan, Walsh Ranch 60'", price=509_200, sqft=2546, baths=3.0)

    quality = assess([plan])["p1"]

    assert quality.has(SQFT_IS_BASE_OF_RANGE)
    assert "sqft" not in quality.corrections
    assert "sqft_max" not in apply_corrections(plan, quality)


# -- (c) one home, two listings ---------------------------------------------------------

# The real pair, exactly as stored: same price, same footage, same house number, two
# spellings of one street and two different cities, eleven days apart.
CRESTED_A = _row(
    "345829319",
    "1820 Crested Ridge Rd, Aledo, TX 76008",
    price=694_245,
    sqft=3482,
    beds=4.0,
    baths=4.0,
    first_seen="2026-07-26T14:58:09Z",
)
CRESTED_B = _row(
    "464003071",
    "1820 Crested Rdg, Fort Worth, TX 76008",
    price=694_245,
    sqft=3482,
    beds=4.0,
    baths=4.0,
    first_seen="2026-08-06T20:59:52Z",
)


def test_one_home_listed_twice_is_pinned_to_the_listing_it_duplicates():
    quality = assess([CRESTED_A, CRESTED_B])

    assert quality["464003071"].duplicate_of == "345829319"
    assert quality["464003071"].has(DUPLICATE_LISTING)
    assert quality["464003071"].is_duplicate
    assert quality["345829319"].duplicate_of is None, "the older listing is the real one"
    assert not quality["345829319"].is_duplicate


def test_two_identical_spec_homes_on_one_street_are_two_homes():
    """14204 and 14217 Fountainhead Cir: same builder, same plan, same $999,900 ask, same
    3,522 feet, different houses. Matching on street name alone would delete one."""
    a = _row("d1", "14204 Fountainhead Cir, Fort Worth, TX 76008", price=999_900, sqft=3522)
    b = _row("d2", "14217 Fountainhead Cir, Fort Worth, TX 76008", price=999_900, sqft=3522)

    assert find_duplicates([a, b]) == {}


@pytest.mark.parametrize(
    "a,b,same",
    [
        ("1820 Crested Ridge Rd, Aledo, TX", "1820 Crested Rdg, Fort Worth, TX", True),
        ("1820 Crested Ridge Rd", "1820 CRESTED RIDGE ROAD", True),
        ("1820 Crested Ridge Rd", "1820 Crested Creek Rd", False),
        ("1725 Roundtree Cir E", "1725 Roundtree Cir W", False),  # two arms of one loop
        ("1725 Roundtree Cir E", "1725 Roundtree Circle E", True),
    ],
)
def test_two_spellings_of_one_street(a, b, same):
    assert same_address(a, b) is same


def test_a_sale_is_not_a_duplicate_of_its_own_listing():
    """The same home for sale in one watch and sold in another shares price and footage.
    That is history working, and history is what this tool is for."""
    for_sale = _row("z1", "1820 Crested Ridge Rd, Aledo, TX", price=694_245, sqft=3482)
    sold = _row(
        "z2",
        "1820 Crested Rdg, Fort Worth, TX",
        price=694_245,
        sqft=3482,
        listing_status="sold",
        status_text="Sold",
    )

    assert find_duplicates([for_sale, sold]) == {}


def test_rows_missing_a_price_or_a_footage_cannot_be_matched():
    """Two nulls are not a coincidence worth acting on."""
    a = _row("z1", "1820 Crested Ridge Rd, Aledo, TX", price=None, sqft=None)
    b = _row("z2", "1820 Crested Rdg, Fort Worth, TX", price=None, sqft=None)

    assert find_duplicates([a, b]) == {}


def test_the_thinner_record_is_the_one_flagged():
    """Same price, same footage, same address, but one row knows almost nothing about the
    home. The full record survives even though it is the newer of the two."""
    thin = _row("thin", "1820 Crested Rdg, Fort Worth, TX", price=694_245, sqft=3482,
                first_seen="2026-07-01T00:00:00Z")
    full = _row("full", "1820 Crested Ridge Rd, Aledo, TX", price=694_245, sqft=3482,
                beds=4.0, baths=4.0, lot_sqft=8712, lat=32.74, lon=-97.56,
                link="https://example.invalid/1820", days_on_zillow=12,
                first_seen="2026-08-06T00:00:00Z")

    assert find_duplicates([thin, full]) == {"thin": "full"}


# -- (d) who built it -------------------------------------------------------------------

# What a caller knows: each builder's own plan sheets, as the feed writes them.
PLANS_BY_BUILDER = {
    "Highland Homes": [
        _row("h1", "GRANTLEY Plan, Walsh Ranch 70'", price=824_200, sqft=4121),
        _row("h2", "BRINKLEY Plan, Walsh Ranch 70'", price=842_000, sqft=4210),
    ],
    "David Weekley Homes": [
        _row("w1", "Camborne Plan, Walsh Cottage", price=542_600, sqft=2713),
        _row("w2", "Huntmere Plan, Walsh Cottage", price=535_800, sqft=2679),
    ],
}


def test_a_builder_that_names_itself_is_confirmed():
    row = _row(
        "s1",
        "1820 Crested Ridge Rd, Aledo, TX 76008",
        sqft=3482,
        description="Beautiful new Highland Homes residence in Walsh Ranch.",
    )
    assert attribute_builder(row, PLANS_BY_BUILDER) == ("Highland Homes", CONFIRMED)


def test_an_exact_plan_name_infers_its_builder():
    row = _row("p1", "GRANTLEY Plan, Walsh Ranch 70'", sqft=4121)
    assert attribute_builder(row, PLANS_BY_BUILDER) == ("Highland Homes", INFERRED)


def test_an_exact_footage_match_infers_its_builder():
    """A spec home's address names no plan, but a builder builds its plans to the foot."""
    row = _row("s1", "1820 Crested Ridge Rd, Aledo, TX 76008", sqft=2713)
    assert attribute_builder(row, PLANS_BY_BUILDER) == ("David Weekley Homes", INFERRED)


def test_evidence_pointing_at_two_builders_is_not_weaker_evidence():
    """Both builders happen to sell a 2,713-foot plan, so the footage proves nothing.
    Nothing is what gets returned."""
    plans = {
        **PLANS_BY_BUILDER,
        "Perry Homes": [_row("q1", "Bartley Plan, Walsh Gardens", price=520_000, sqft=2713)],
    }
    row = _row("s1", "1820 Crested Ridge Rd, Aledo, TX 76008", sqft=2713)

    assert attribute_builder(row, plans) == (None, UNRESOLVED)


def test_a_market_whose_builders_nobody_has_mapped_resolves_nothing():
    row = _row("s1", "1820 Crested Ridge Rd, Aledo, TX 76008", sqft=3482)
    assert attribute_builder(row, {}) == (None, UNRESOLVED)
    assert assess([row])["s1"].builder_tier == UNRESOLVED


def test_assess_carries_the_builder_onto_every_record():
    """With no roster to consult, the caller's plan map is all there is, and it infers."""
    rows = [_row("p1", "GRANTLEY Plan, Walsh Ranch 70'", price=824_200, sqft=4121, baths=5.0)]

    quality = assess(rows, plans_by_builder=PLANS_BY_BUILDER, attributions={})["p1"]

    assert (quality.builder, quality.builder_tier) == ("Highland Homes", INFERRED)


def test_assess_reads_the_researched_roster_from_disk_and_it_wins():
    """The one input `assess` defaults to a file, and the reason it does.

    The fixture map above claims GRANTLEY is a Highland plan. It is a Drees plan, which is
    what the roster in `data/builder-attribution.yaml` records, and the roster is what a
    caller who forgot to think about builders gets. A caller cannot lose the researched
    truth by handing in a worse map.
    """
    rows = [_row("p1", "GRANTLEY Plan, Walsh Ranch 70'", price=824_200, sqft=4121, baths=5.0)]

    quality = assess(rows, plans_by_builder=PLANS_BY_BUILDER)["p1"]

    assert quality.builder == "Drees Homes"


# -- the record as a whole --------------------------------------------------------------


def test_a_row_with_nothing_wrong_with_it_says_nothing():
    row = _row("s1", "1820 Crested Ridge Rd, Aledo, TX 76008", price=694_245, sqft=3482,
               baths=4.0)

    quality = assess([row])["s1"]

    assert quality.flags == ()
    assert quality.corrections == {}
    assert apply_corrections(row, quality) == row


def test_corrections_can_be_supplied_rather_than_read_from_disk():
    """The data files are a default, not a dependency — a caller with better knowledge
    passes it in, and a test never has to edit shipped data to make a point."""
    row = _row("s1", "1820 Crested Ridge Rd, Aledo, TX 76008", sqft=3482, baths=3.0)
    supplied = {"s1": {"listed": 3.0, "verified": 2.5, "source": "walk-through",
                       "verified_on": "2026-08-09"}}

    quality = assess([row], corrections=supplied)["s1"]

    assert quality.has(BATHS_CORRECTED)
    assert apply_corrections(row, quality)["baths"] == 2.5


def test_a_row_with_no_identity_is_skipped_rather_than_keyed_on_nothing():
    assert assess([_row(None, "1820 Crested Ridge Rd")]) == {}


# -- (e) the researched roster ----------------------------------------------------------
#
# The file these prove is the one thing in this repository that was already lost once and
# recovered by luck (docs/PORTING-THE-REPORTS.md, lesson 4). The tests are therefore about
# the data as much as about the code: a roster that quietly loses half its plan sheets in a
# careless edit would otherwise be discovered by a reader of a published page.

AMBIGUOUS_SPEC = "461741661"  # 14217 Fountainhead Cir — Perry Homes or Britton Homes
RESEARCHED_PLAN = "Plan 216 Plan, Walsh"  # a "Plan NNNN" name: Highland, not Perry
RESEARCHED_THE_PLAN = "The Bradley Plan, Walsh"  # a "The <name>" name: Village, not GFO


def test_the_whole_walsh_roster_round_trips_out_of_the_file():
    """Sixty-eight plan sheets and forty spec homes, and every entry says where it came from."""
    roster = builder_attributions()
    plans = {k: v for k, v in roster.items() if not k.isdigit()}
    specs = {k: v for k, v in roster.items() if k.isdigit()}

    assert len(plans) == 68 and len(specs) == 40
    for key, entry in roster.items():
        assert entry["source"] and entry["verified_on"], f"{key} carries no provenance"
        assert entry["basis"] in ("description", "plan-match", "ambiguous", "no-evidence")

    by_builder = Counter(e["builder"] for e in plans.values())
    assert by_builder == {
        "David Weekley Homes": 20,
        "Drees Homes": 18,
        "Highland Homes": 12,
        "Village Homes": 10,
        "GFO Home": 8,
    }


def test_the_ambiguous_home_loads_as_ambiguous_and_stays_that_way():
    """The entry whose whole purpose is to stop someone re-researching it into certainty."""
    entry = builder_attributions()[AMBIGUOUS_SPEC]

    assert entry["builder"] is None
    assert entry["candidates"] == ["Perry Homes", "Britton Homes"]

    row = _row(AMBIGUOUS_SPEC, entry["address"], sqft=2713)
    # Both the module's own footage heuristic and the roster are consulted; the roster's
    # "two builders fit" wins, because it is the stronger statement about the evidence.
    assert attribute_builder(row, PLANS_BY_BUILDER, builder_attributions()) == (
        None,
        UNRESOLVED,
    )


@pytest.mark.parametrize(
    "address, builder, wrong_guess",
    [
        (RESEARCHED_PLAN, "Highland Homes", "Perry Homes"),
        (RESEARCHED_THE_PLAN, "Village Homes", "GFO Home"),
    ],
)
def test_the_two_heuristic_failures_now_resolve_from_data(address, builder, wrong_guess):
    """The exact two shapes the original's heuristics got confidently wrong.

    "Plan 1234" read as Perry Homes when it is Highland; "The <name>" read as GFO Home when
    it is Village. Between them they misassigned 22 of 68 plan sheets. From data they resolve
    correctly, and with the file taken away they resolve to *nothing* — never to the wrong
    builder, which is the property that matters.
    """
    row = _row("x1", address, sqft=3000)

    assert attribute_builder(row, {}, builder_attributions()) == (builder, RESEARCHED)
    assert attribute_builder(row, {}, {}) == (None, UNRESOLVED)
    assert attribute_builder(row, {}, {})[0] != wrong_guess


def test_a_plan_match_entry_earns_the_matching_tier_and_not_the_strongest_one():
    """GRANTLEY was attributed by matching a plan sheet, not by anyone naming Drees.

    So it comes back INFERRED. A file of researched entries is not a file of certainties,
    and flattening the two would be exactly the "guess dressed as a fact" this module exists
    to refuse.
    """
    row = _row("p1", "GRANTLEY Plan, Walsh Ranch 70'", sqft=4121)

    assert attribute_builder(row, {}, builder_attributions()) == ("Drees Homes", INFERRED)


def test_an_entry_the_feed_no_longer_agrees_with_is_stale_and_is_not_applied():
    """A plan renamed under an entry's key is a re-verify, not a builder to trust."""
    attributions = {
        "Plan 216 Plan, Walsh": {
            "builder": "Highland Homes",
            "basis": "description",
            "source": "the listing's own description names the builder",
            "verified_on": "2026-08-21",
            "plan_name": "Plan 216",
            "community": "Walsh 60s",  # the feed now files it under "Walsh"
        }
    }
    row = _row("x1", "Plan 216 Plan, Walsh", sqft=3000)

    assert attribute_builder(row, {}, attributions) == (None, UNRESOLVED)


def test_a_home_the_roster_looked_at_and_found_nothing_on_still_gets_the_heuristics():
    """"Nobody wrote a description" is not "the evidence conflicts", so matching may run."""
    attributions = {
        "s1": {
            "builder": None,
            "basis": "no-evidence",
            "source": "the listing carries no description text at all",
            "verified_on": "2026-08-21",
        }
    }
    row = _row("s1", "1820 Crested Ridge Rd, Aledo, TX 76008", sqft=2713)

    assert attribute_builder(row, PLANS_BY_BUILDER, attributions) == (
        "David Weekley Homes",
        INFERRED,
    )
