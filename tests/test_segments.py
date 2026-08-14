"""Subdivision membership: the three offline signals, and the data file behind them.

`in_subdivision` has to read either shape history hands it — a fresh `Listing` off the
adapter, or a plain dict off `store.latest_snapshot_rows` — so most tests exercise it
through a small `Listing`, and one proves the dict path works identically.
"""
import pytest

from propertyfinder.adapters import Listing
from propertyfinder.segments import get_subdivision, in_subdivision, subdivision_name_matches


def _lst(address: str) -> Listing:
    return Listing(zpid="1", address=address)


# -- signal 1: the plan-sheet community named in the address ---------------------------


def test_plan_sheet_community_matches():
    assert in_subdivision(_lst("Camborne Plan, Walsh Cottage"), "walsh")
    assert in_subdivision(_lst("BENSON Plan, Walsh Ranch 60'"), "walsh")
    assert in_subdivision(_lst("Charleston Plan, Walsh"), "walsh")


def test_a_neighbouring_communitys_plan_sheet_is_not_walsh():
    assert not in_subdivision(_lst("Jasmine Plan, Parks of Aledo"), "walsh")
    assert not in_subdivision(_lst("Some Plan, Morningstar"), "walsh")


# -- signal 2: the curated street allowlist ---------------------------------------------


def test_street_allowlist_matches_resale_and_spec_homes():
    assert in_subdivision(_lst("13648 Leatherstem Ln, Aledo, TX 76008"), "walsh")
    assert in_subdivision(_lst("2529 Green Plateau Dr, Fort Worth, TX 76008"), "walsh")
    # house-number-only differences and directional suffixes normalize the same
    assert in_subdivision(_lst("1713 Roundtree Cir E, Aledo, TX 76008"), "walsh")


def test_streets_from_the_2026_07_26_triage_match():
    """Filter-dropped in-radius listings, confirmed Walsh by spatial interleave with an
    already-verified Walsh home (see the allowlist's own header)."""
    assert in_subdivision(_lst("14308 Distant Rock Trl, Fort Worth, TX 76008"), "walsh")
    assert in_subdivision(_lst("1820 Crested Rdg, Fort Worth, TX 76008"), "walsh")
    assert in_subdivision(_lst("1917 Bending Oak St, Aledo, TX 76008"), "walsh")


def test_a_street_not_in_the_allowlist_fails_membership_even_in_the_right_town():
    """The same-named-street case this signal must get right on its own: an address that
    reads exactly like a Walsh row — right city, right ZIP — is not a member unless its
    street is actually on the list."""
    assert not in_subdivision(_lst("100 Nowhere Rd, Aledo, TX 76008"), "walsh")
    assert not in_subdivision(_lst("123 Main St, Fort Worth, TX 76126"), "walsh")


def test_membership_reads_a_plain_store_row_the_same_as_a_listing():
    """`store.latest_snapshot_rows` hands back dicts, not `Listing` objects — every
    signal above must work off either shape."""
    row = {"address": "13648 Leatherstem Ln, Aledo, TX 76008"}
    assert in_subdivision(row, "walsh")
    assert not in_subdivision({"address": "100 Nowhere Rd, Aledo, TX 76008"}, "walsh")


# -- signal 3: the address-token backstop ------------------------------------------------


def test_walsh_ave_address_token_backstop():
    assert in_subdivision(_lst("14545 Walsh Ave"), "walsh")


def test_the_backstop_does_not_readmit_a_different_communitys_plan_sheet():
    """A plan-sheet row is ruled on by signal 1 alone. If the rest of the address happens
    to mention "Walsh" in passing, the address-token backstop must not overrule a plan
    community that already, correctly, said no."""
    assert not in_subdivision(_lst("Cottage Plan, Morningstar near Walsh Ranch"), "walsh")


# -- degrading rather than guessing ------------------------------------------------------


def test_unknown_subdivision_raises():
    with pytest.raises(KeyError):
        in_subdivision(_lst("anything"), "not-a-subdivision")


# -- the detail engine's own field, reconciled offline -----------------------------------


def test_detail_subdivision_name_mapping():
    assert subdivision_name_matches("Walsh Ranch Quail Vly", "walsh")
    assert not subdivision_name_matches("Morningstar Ph 2", "walsh")
    assert not subdivision_name_matches(None, "walsh")


# -- the data file itself -----------------------------------------------------------------


def test_the_allowlist_loads_as_data_not_code():
    sub = get_subdivision("walsh")
    assert sub.key == "walsh"
    assert "tolleson dr" in sub.streets
    assert len(sub.streets) > 50
    assert sub.plan_prefixes == ("walsh",)
