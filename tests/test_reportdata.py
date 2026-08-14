"""`build_payload` against hand-built history: a market with a hole in it on purpose.

Two homes report everything, one is missing its square footage, and one has left the
market since the last sweep. If the payload ever fakes a $ per square foot or lets a
sold-and-gone home masquerade as an active listing, it is wrong here.
"""
from conftest import make_listing

from propertyfinder.config import Watch
from propertyfinder.reportdata import build_payload
from propertyfinder.store import record_snapshot, upsert_property

WATCH = Watch(
    name="walsh-aledo",
    center_address="2112 Eastus Ln, Aledo, TX 76008",
    lat=32.73665,
    lon=-97.55626,
    radius_miles=2.0,
    listing_status="for_sale",
    queries=["Aledo, TX 76008"],
)
T1, T2 = "2026-07-10T10:00:00Z", "2026-07-11T10:00:00Z"
GENERATED = "2026-07-11T12:00:00Z"


def _sweep(sessions, ts: str, listings) -> None:
    with sessions() as s:
        for listing in listings:
            upsert_property(s, listing, ts)
        s.flush()
        for listing in listings:
            record_snapshot(s, listing, WATCH.name, ts, distance_miles=1.2)
        s.commit()


def test_payload_carries_watch_metadata_and_the_sweep_date(sessions):
    _sweep(sessions, T1, [make_listing("111", price=500_000)])
    with sessions() as s:
        payload = build_payload(s, WATCH, GENERATED)

    assert payload["watch"] == {
        "name": "walsh-aledo",
        "center_address": "2112 Eastus Ln, Aledo, TX 76008",
        "radius_miles": 2.0,
        "listing_status": "for_sale",
    }
    assert payload["generated_ts"] == GENERATED
    assert payload["sweep_ts"] == T1  # when the data was collected, not when built


def test_a_home_missing_square_footage_has_no_dollars_per_square_foot(sessions):
    _sweep(
        sessions,
        T1,
        [
            make_listing("111", price=500_000, sqft=2500),
            make_listing("222", price=600_000, sqft=None),
        ],
    )
    with sessions() as s:
        payload = build_payload(s, WATCH, GENERATED)

    by_zpid = {r["zpid"]: r for r in payload["listings"]}
    assert by_zpid["111"]["price_per_sqft"] == 200.0
    assert by_zpid["222"]["sqft"] is None
    assert by_zpid["222"]["price_per_sqft"] is None  # omitted, never faked


def test_medians_are_computed_over_the_homes_that_actually_report_a_value(sessions):
    _sweep(
        sessions,
        T1,
        [
            make_listing("111", price=400_000, sqft=2000),  # $200/sqft
            make_listing("222", price=600_000, sqft=None),  # no $/sqft at all
            make_listing("333", price=None, sqft=3000),  # no price at all
        ],
    )
    with sessions() as s:
        payload = build_payload(s, WATCH, GENERATED)

    # price: only 111 and 222 report one -> median of two values
    assert payload["medians"]["price"] == 500_000.0
    # $/sqft: only 111 reports one -> the median of one value is itself, not a blend
    assert payload["medians"]["price_per_sqft"] == 200.0


def test_a_home_gone_since_the_last_sweep_is_not_an_active_listing(sessions):
    """111 is seen at both sweeps; 222 is seen only at the first. By the time the second
    sweep runs, 222 has left the market — its *latest* row still exists (history is never
    deleted), but a report built after that sweep must not list it as for sale."""
    _sweep(sessions, T1, [make_listing("111", price=500_000), make_listing("222", price=700_000)])
    _sweep(sessions, T2, [make_listing("111", price=480_000)])

    with sessions() as s:
        payload = build_payload(s, WATCH, GENERATED)

    zpids = {r["zpid"] for r in payload["listings"]}
    assert zpids == {"111"}
    assert payload["counts"]["total"] == 1
    assert payload["sweep_ts"] == T2


def test_an_empty_database_is_an_honest_empty_payload(sessions):
    with sessions() as s:
        payload = build_payload(s, WATCH, GENERATED)

    assert payload["sweep_ts"] is None
    assert payload["listings"] == []
    assert payload["counts"] == {"total": 0}
    assert payload["medians"] == {"price": None, "price_per_sqft": None, "days_on_market": None}


def test_the_payload_snapshots_cleanly_for_a_small_known_market(sessions):
    """A whole-payload equality check, not just field-by-field spot checks — the shape of
    the dict is part of the contract the template relies on."""
    _sweep(
        sessions,
        T1,
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
    with sessions() as s:
        payload = build_payload(s, WATCH, GENERATED)

    assert payload == {
        "watch": {
            "name": "walsh-aledo",
            "center_address": "2112 Eastus Ln, Aledo, TX 76008",
            "radius_miles": 2.0,
            "listing_status": "for_sale",
        },
        "generated_ts": GENERATED,
        "sweep_ts": T1,
        "counts": {"total": 1},
        "medians": {
            "price": 674_900.0,
            "price_per_sqft": 674_900.0 / 3012,
            "days_on_market": 27,
        },
        "listings": [
            {
                "zpid": "111",
                "address": "111 Tolleson Dr, Aledo, TX 76008",
                "price": 674_900.0,
                "beds": 4,
                "baths": 3,
                "sqft": 3012,
                "price_per_sqft": 674_900.0 / 3012,
                "days_on_market": 27,
                "status": "House for sale",
                "link": "https://www.zillow.com/homedetails/111_zpid/",
                "distance_miles": 1.2,
            }
        ],
    }
