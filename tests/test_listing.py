"""The seam is the contract every other layer will be written against.

If these tests are right, a home that reaches storage means what storage thinks it
means; if they are wrong, every number downstream is wrong in the same quiet way.
"""
import dataclasses

import pytest

from conftest import load_fixture

from propertyfinder.adapters import Listing, to_listing
from propertyfinder.adapters.models import SearchResponse


def _results(fixture: str):
    return SearchResponse.model_validate(load_fixture(fixture)).properties


def test_the_converter_round_trips_a_real_row():
    tolleson = to_listing(_results("search_for_sale_page1")[0], "for_sale")
    assert tolleson == Listing(
        zpid="29584711",
        address="1420 Tolleson Dr, Aledo, TX 76008",
        lat=32.741913,
        lon=-97.560241,
        price=674900,
        beds=4,
        baths=3,
        sqft=3012,
        lot_sqft=8712,
        home_type="SINGLE_FAMILY",
        listing_status="for_sale",
        status_text="House for sale",
        days_on_zillow=27,
        zestimate=689400,
        rent_zestimate=3450,
        tax_assessed_value=641230,
        date_sold=None,
        link="https://www.zillow.com/homedetails/1420-Tolleson-Dr-Aledo-TX-76008/29584711_zpid/",
        image_url="https://photos.zillowstatic.com/fp/fixture-tolleson-p.jpg",
    )


def test_missing_fields_become_none_and_never_zero():
    """A coordinate of 0.0 is a real place in the Gulf of Guinea. Absence must stay absent."""
    sunrise = to_listing(_results("search_for_sale_page1")[2], "for_sale")
    assert sunrise.lat is None and sunrise.lon is None
    assert sunrise.zestimate is None and sunrise.lot_sqft is None
    assert sunrise.price == 449000  # what is present is still present


def test_the_zpid_becomes_a_string_identity():
    assert to_listing(_results("search_for_sale_page1")[0]).zpid == "29584711"


def test_a_row_without_a_zpid_is_dropped():
    result = _results("search_for_sale_page1")[0].model_copy(update={"zpid": None})
    assert to_listing(result) is None


def test_the_feeds_status_word_is_translated_when_the_caller_gives_none():
    sold = to_listing(_results("search_sold_page1")[0])  # home_status RECENTLY_SOLD
    assert sold.listing_status == "sold" and sold.date_sold == "2026-07-18"


def test_what_the_caller_asked_for_wins_over_the_rows_own_word():
    result = _results("search_for_sale_page1")[0].model_copy(
        update={"home_status": "PENDING"}
    )
    assert to_listing(result, "for_sale").listing_status == "for_sale"


def test_an_unknown_status_word_is_left_unclaimed():
    result = _results("search_for_sale_page1")[0].model_copy(
        update={"home_status": "SOMETHING_NEW"}
    )
    assert to_listing(result).listing_status is None


def test_price_per_square_foot_needs_both_numbers():
    listing = to_listing(_results("search_for_sale_page1")[0])
    assert round(listing.price_per_sqft, 2) == round(674900 / 3012, 2)
    undisclosed = to_listing(_results("search_sold_page1")[0])
    assert undisclosed.price_per_sqft is None  # no price, so no rate — not a zero


def test_a_listing_is_frozen_because_an_observation_cannot_be_edited():
    listing = to_listing(_results("search_for_sale_page1")[0])
    with pytest.raises(dataclasses.FrozenInstanceError):
        listing.price = 1


def test_the_seam_carries_no_zestimate_derived_judgement():
    """The estimate is a displayed reference. A helper that scores against it invites use."""
    assert not hasattr(Listing(zpid="1"), "price_vs_zestimate_pct")
