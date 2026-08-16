"""Luxury discovery — the early-stop budget lever and the authoritative radius."""
from __future__ import annotations

from agentfinder.adapters import LuxeExtras
from agentfinder.config import LuxuryConfig
from agentfinder.discover import below_floor, collect_luxury, is_luxury
from conftest import make_listing

FLOOR = 1_500_000
CFG = LuxuryConfig(name="t", center_address="c", lat=32.741913, lon=-97.560241,
                   radius_miles=25.0, price_floor=FLOOR, max_pages=5,
                   queries=["Fort Worth, TX 76107"])


_ZID = [0]


def _rows(*prices):
    # Unique zpids across every page, so dedup never conflates two different homes.
    out = []
    for p in prices:
        _ZID[0] += 1
        out.append((make_listing(f"z{_ZID[0]}", price=p), LuxeExtras()))
    return out


def test_below_floor_logic():
    assert not below_floor(_rows(5_000_000, 2_000_000), FLOOR)   # all above
    assert below_floor(_rows(2_000_000, 900_000), FLOOR)          # straddles -> stop
    assert below_floor(_rows(800_000, 700_000), FLOOR)            # all below
    assert not below_floor([], FLOOR)                             # empty is not "below"


def test_is_luxury_excludes_plan_sheets_and_sizeless():
    assert is_luxury(make_listing(price=2_000_000), FLOOR)
    assert not is_luxury(make_listing(price=800_000), FLOOR)
    assert not is_luxury(make_listing(price=2_000_000, sqft=None), FLOOR)
    assert not is_luxury(make_listing(price=2_000_000,
                                      address="Clearfork Plan, Contemporary Homes"), FLOOR)


class FakeApi:
    """A stub adapter that answers by (query, page) and records how many pages it walked."""

    def __init__(self, pages):
        self.pages = pages           # {(query, page): (rows, pagination)}
        self.walked = []

    def zillow_page(self, query, page=1, sort_by="price_desc"):
        self.walked.append((query, page))
        return self.pages.get((query, page), ([], {"total_pages": page}))


def test_collect_stops_paging_once_below_floor():
    q = "Fort Worth, TX 76107"
    api = FakeApi({
        (q, 1): (_rows(5_000_000, 3_000_000), {"total_pages": 9}),   # all above -> page 2
        (q, 2): (_rows(2_000_000, 900_000), {"total_pages": 9}),      # straddles -> STOP
        (q, 3): (_rows(4_000_000), {"total_pages": 9}),               # must never be fetched
    })
    out = collect_luxury(api, CFG)
    assert (q, 3) not in api.walked            # early-stop saved the call
    prices = sorted(l.price for l, _, _ in out)
    assert prices == [2_000_000, 3_000_000, 5_000_000]  # sub-floor 900k dropped


def test_radius_is_authoritative():
    q = "Fort Worth, TX 76107"
    far = make_listing("far", price=3_000_000, lat=30.0, lon=-95.0)  # ~200mi away
    near = make_listing("near", price=3_000_000)
    api = FakeApi({(q, 1): ([(far, LuxeExtras()), (near, LuxeExtras())], {"total_pages": 1})})
    out = collect_luxury(api, CFG)
    assert [l.zpid for l, _, _ in out] == ["near"]


def test_no_coordinates_is_outside():
    q = "Fort Worth, TX 76107"
    nocoord = make_listing("nc", price=3_000_000, lat=None, lon=None)
    api = FakeApi({(q, 1): ([(nocoord, LuxeExtras())], {"total_pages": 1})})
    assert collect_luxury(api, CFG) == []


def test_dedup_keeps_one_per_home():
    cfg = CFG.model_copy(update={"queries": ["A, TX 76107", "B, TX 76107"]})
    dupe = make_listing("dupe", price=3_000_000)
    api = FakeApi({
        ("A, TX 76107", 1): ([(dupe, LuxeExtras())], {"total_pages": 1}),
        ("B, TX 76107", 1): ([(dupe, LuxeExtras())], {"total_pages": 1}),
    })
    out = collect_luxury(api, cfg)
    assert len(out) == 1
