"""The buyer's brief: what it keeps, what it drops, and whether it says so honestly.

The behaviour worth defending here is the unglamorous half. Any filter can keep the homes
that match. What this one has to get right is the homes it *cannot judge* — the feed gives
no square footage on a meaningful slice of any rural market — and the arithmetic that lets
a page state a shortlist as a fraction of the market it came from without the two numbers
drifting apart.
"""
from __future__ import annotations

import pytest
from conftest import make_listing

from propertyfinder.criteria import REASONS, Criteria, screen

# The brief this was built for: four bedrooms, three thousand feet, a house, in one ZIP.
BRIEF = Criteria(zip="75835", min_beds=4, min_sqft=3000, home_types=["SINGLE_FAMILY"])


def row(address="712 Lamar Ave, Crockett, TX 75835", **kw) -> dict:
    """A store row, which is the shape a report actually screens."""
    return {
        "zpid": "1",
        "address": address,
        "beds": 5.0,
        "sqft": 4912.0,
        "home_type": "SINGLE_FAMILY",
        **kw,
    }


# -- what passes -------------------------------------------------------------------------


def test_a_home_meeting_every_clause_passes():
    assert BRIEF.test(row()) is None


def test_exactly_the_minimum_passes_because_the_brief_says_at_least():
    assert BRIEF.test(row(beds=4.0, sqft=3000.0)) is None


def test_an_undeclared_brief_keeps_everything_untouched():
    rows = [row(), row(beds=1.0, sqft=400.0, home_type="LOT")]
    result = screen(rows, Criteria())

    assert result.kept == rows
    assert result.considered == 2
    assert result.dropped == {}
    assert result.as_payload()["declared"] is False


def test_no_brief_at_all_is_the_same_as_an_empty_one():
    """`screen(rows, None)` is the path every watch that configures nothing takes."""
    rows = [row(), row(beds=1.0)]

    assert screen(rows, None).kept == rows


# -- what fails, and under which name ------------------------------------------------------


@pytest.mark.parametrize(
    "overrides, reason",
    [
        ({"beds": 3.0}, "too_few_beds"),
        ({"sqft": 1950.0}, "too_small"),
        ({"home_type": "LOT"}, "wrong_home_type"),
        ({"address": "12 Elm St, Grapeland, TX 75844"}, "outside_zip"),
    ],
)
def test_each_clause_drops_under_its_own_name(overrides, reason):
    assert BRIEF.test(row(**overrides)) == reason


def test_every_reason_the_screen_can_emit_has_a_sentence_for_the_page():
    """The payload ships reason keys; the page prints sentences. A key with no sentence
    would render as a blank line on a published report rather than fail anywhere."""
    emitted = {
        BRIEF.test(row(**o))
        for o in (
            {"beds": 3.0}, {"sqft": 1950.0}, {"home_type": "LOT"}, {"beds": None},
            {"sqft": None}, {"home_type": None}, {"address": "Nowhere"},
            {"address": "12 Elm St, Grapeland, TX 75844"},
        )
    } - {None}

    assert emitted, "this test proves nothing if the brief drops nothing"
    assert emitted <= set(REASONS)


# -- the half that matters: a number the feed never gave -------------------------------------


@pytest.mark.parametrize(
    "overrides, reason",
    [
        ({"sqft": None}, "sqft_unknown"),
        ({"beds": None}, "beds_unknown"),
        ({"home_type": None}, "wrong_home_type"),
        ({"address": "County Road 3355"}, "zip_unknown"),
        ({"address": None}, "zip_unknown"),
    ],
)
def test_a_missing_number_fails_rather_than_passes(overrides, reason):
    """A home the feed never measured cannot be shown to be big enough, so it is not on a
    page promising big enough. The alternative — letting unknowns through — publishes a
    1,100 sq ft cottage under a headline that says 3,000+."""
    assert BRIEF.test(row(**overrides)) == reason


def test_unknowns_are_counted_apart_from_genuine_misses():
    """"No home here is that big" and "the feed did not measure eleven of them" are
    different facts about a market, and the page has to be able to tell them apart."""
    rows = [row(sqft=1200.0), row(sqft=1400.0), row(sqft=None)]
    dropped = screen(rows, Criteria(min_sqft=3000)).dropped

    assert dropped == {"too_small": 2, "sqft_unknown": 1}


# -- the arithmetic a page quotes ------------------------------------------------------------


def test_considered_always_equals_kept_plus_dropped():
    """The page states the shortlist as a fraction of the market. If these ever disagree,
    that sentence is wrong, and it is the kind of wrong nobody notices."""
    rows = [
        row(),                                         # keeps
        row(beds=3.0),                                 # too few beds
        row(sqft=None),                                # unmeasured
        row(home_type="LOT", beds=3.0, sqft=1600.0),   # a bare lot, failing three clauses
        row(address="12 Elm St, Grapeland, TX 75844"),  # next ZIP over
    ]
    result = screen(rows, BRIEF)

    assert result.considered == 5
    assert len(result.kept) == 1
    assert result.considered == len(result.kept) + result.n_dropped


def test_a_home_failing_three_clauses_is_dropped_once():
    """Only the first failure is recorded — a bare lot that is also too small is one lot,
    and double-counting it would break the arithmetic above."""
    result = screen([row(home_type="LOT", beds=1.0, sqft=100.0)], BRIEF)

    assert result.n_dropped == 1
    assert result.dropped == {"wrong_home_type": 1}


def test_the_payload_orders_reasons_by_how_much_they_removed():
    """A reader scanning the section should meet the biggest cause first."""
    rows = [row(beds=3.0)] * 2 + [row(sqft=100.0)] * 5 + [row(home_type="LOT")]
    reasons = screen(rows, BRIEF).as_payload()["reasons"]

    assert [r["n"] for r in reasons] == [5, 2, 1]
    assert reasons[0]["key"] == "too_small"
    assert all(r["why"] for r in reasons)


# -- the brief describing itself ---------------------------------------------------------------


def test_the_brief_describes_itself_the_way_a_person_would_say_it():
    assert BRIEF.describe() == ["4+ bedrooms", "3,000+ sq ft", "houses only", "ZIP 75835"]


def test_a_whole_number_of_bedrooms_is_not_printed_with_a_decimal_point():
    assert Criteria(min_beds=4).describe() == ["4+ bedrooms"]
    assert Criteria(min_beds=3.5).describe() == ["3.5+ bedrooms"]


def test_an_empty_brief_describes_nothing_rather_than_something_vague():
    assert Criteria().describe() == []
    assert Criteria().declared is False


# -- the shapes a brief refuses to be -----------------------------------------------------------


@pytest.mark.parametrize("bad", ["7580", "758350", "TX 75835", ""])
def test_a_zip_that_is_not_five_digits_is_refused_at_load_time(bad):
    """A misconfigured brief that screens anyway publishes an empty page and blames the
    market — so it fails where every other config mistake in this tool fails: at load."""
    with pytest.raises(ValueError, match="five digits"):
        Criteria(zip=bad)


@pytest.mark.parametrize("field", ["min_beds", "min_sqft"])
def test_a_minimum_of_zero_or_less_is_refused(field):
    with pytest.raises(ValueError):
        Criteria(**{field: 0})


def test_an_empty_home_type_allowlist_is_refused_rather_than_matching_nothing():
    with pytest.raises(ValueError):
        Criteria(home_types=[])


# -- the two shapes it is asked about ------------------------------------------------------------


def test_a_listing_off_the_adapter_answers_the_same_as_a_store_row():
    """The brief is asked about rows read back out of the database and about records fresh
    off the feed. Neither should have to be converted into the other to answer it."""
    listing = make_listing("1", address="712 Lamar Ave, Crockett, TX 75835",
                           beds=5, sqft=4912, home_type="SINGLE_FAMILY")

    assert BRIEF.test(listing) is None
    assert BRIEF.test(make_listing("2", beds=2, sqft=900, home_type="LOT")) is not None


def test_home_types_match_regardless_of_case():
    assert Criteria(home_types=["single_family"]).test(row()) is None


# -- the ZIP is read off the end of the address, not out of the middle ------------------------------


def test_a_five_digit_street_number_is_not_mistaken_for_a_zip():
    """"3168 Fm 2076, Crockett, TX 75835" contains two numbers that look postal. Only the
    one at the end is."""
    assert Criteria(zip="75835").test(row(address="3168 Fm 20765, Crockett, TX 75835")) is None
    assert Criteria(zip="20765").test(row(address="3168 Fm 20765, Crockett, TX 75835")) == (
        "outside_zip"
    )


def test_a_zip_plus_four_still_reads_as_its_five_digit_zip():
    assert Criteria(zip="75835").test(row(address="1 Main St, Crockett, TX 75835-0112")) is None
