"""The attribution parser — the tool's heart, and where the three review fixes live."""
from __future__ import annotations

from agentfinder.attribution import (
    CONFIRMED, INFERRED, UNRESOLVED, attribute, is_about_address, _norm,
)
from conftest import organic

ADDR = "6236 Indian Creek Dr, Fort Worth, TX 76107"
URL = "https://www.homes.com/property/6236-indian-creek-dr-fort-worth-tx/abc/"


def test_licensed_confirmed_from_trusted_source():
    res = [organic(URL, "Listed by: Martha Williams 0276718 817-555-0100, Williams Trew",
                   title="6236 Indian Creek Dr")]
    a = attribute(ADDR, res)
    assert a.tier == CONFIRMED
    assert a.agent == "Martha Williams" and a.licence == "0276718"
    assert a.phone and a.reason is None


def test_address_gating_rejects_a_neighbours_page():
    # A result about 6244 (a neighbour) must not testify about 6236, even if it names an agent.
    res = [organic("https://www.homes.com/property/6244-indian-creek-dr/x/",
                   "Listed by: Someone Else 0111111", title="6244 Indian Creek Dr")]
    a = attribute(ADDR, res)
    assert a.tier == UNRESOLVED
    assert "about this exact address" in a.reason


def test_zillow_is_demoted_out_of_confirmed():
    # A Zillow snippet names Premier Agent ADVERTISERS, not the lister — must not be CONFIRMED
    # on its own (no licence, only a TIER_3 source now).
    res = [organic("https://www.zillow.com/homedetails/6236-Indian-Creek-Dr/9_zpid/",
                   "Listed by Jane Advertiser", title="6236 Indian Creek Dr")]
    a = attribute(ADDR, res)
    assert a.tier != CONFIRMED


def test_movoto_is_demoted():
    res = [organic("https://www.movoto.com/fort-worth-tx/6236-indian-creek-dr/",
                   "Listed by Ted Referral", title="6236 Indian Creek Dr")]
    a = attribute(ADDR, res)
    assert a.tier != CONFIRMED  # movoto is TIER_3 now, so no CONFIRMED without corroboration


def test_brokerage_only_is_inferred_with_a_reason():
    res = [organic(URL, "Listed with the Briggs Freeman Sotheby's International Realty.",
                   title="6236 Indian Creek Dr")]
    a = attribute(ADDR, res)
    assert a.tier == INFERRED and a.agent is None
    assert a.brokerage and a.reason


def test_two_different_agents_across_pages_is_unresolved():
    res = [
        organic("https://www.homes.com/property/6236-indian-creek-dr/a/",
                "Listed by: Alice Adams 0111111", title="6236 Indian Creek Dr"),
        organic("https://www.realtor.com/realestateandhomes-detail/6236-Indian-Creek-Dr/b",
                "Listed by: Bob Baker 0222222", title="6236 Indian Creek Dr"),
    ]
    a = attribute(ADDR, res)
    assert a.tier == UNRESOLVED
    assert set(a.conflict) == {"Alice Adams", "Bob Baker"}


def test_co_listers_written_together_survive():
    # Two licences in ONE snippet = a co-listing, kept (primary + co_listers), not a conflict.
    res = [organic(URL, "Listed by: Alice Adams 0111111 and Bob Baker 0222222, Compass",
                   title="6236 Indian Creek Dr")]
    a = attribute(ADDR, res)
    assert a.tier == CONFIRMED
    assert a.agent  # a primary is chosen
    assert "Bob Baker" in a.co_listers or "Alice Adams" in a.co_listers


def test_no_house_number_address_degrades_honestly():
    a = attribute("E Bankhead Hwy, Aledo, TX 76008",
                  [organic(URL, "Listed by: Anyone 0333333", title="E Bankhead Hwy")])
    assert a.tier == UNRESOLVED  # no number to gate on -> honest, not mis-attributed


def test_nothing_found_is_unresolved_with_reason():
    a = attribute(ADDR, [organic(URL, "A lovely home with a pool.", title="6236 Indian Creek Dr")])
    assert a.tier == UNRESOLVED and a.reason


def test_name_normalisation_folds_a_middle_name():
    assert _norm("Joseph Berkes") == _norm("Joseph McCarthy Berkes")


def test_is_about_address_direct():
    assert is_about_address(ADDR, "https://x.com/6236-indian-creek-dr", "6236 Indian Creek")
    assert not is_about_address(ADDR, "https://x.com/1-main-st", "1 Main St")
