"""`price_change_map` — first ask versus latest, cumulative across however many sweeps.

The whole point is that it does not care how many times a price moved in between: a home
cut twice nets to one number, a home that only ever rose nets to a negative one, and a
home that never changed is not in the map at all.
"""
import pytest
from conftest import make_listing

from propertyfinder.store import price_change_map, record_snapshot, upsert_property

WATCH = "walsh-aledo"
T1, T2, T3 = "2026-07-10T10:00:00Z", "2026-07-11T10:00:00Z", "2026-07-12T10:00:00Z"


def _sweep(sessions, ts: str, listings, watch: str = WATCH) -> None:
    with sessions() as s:
        for listing in listings:
            upsert_property(s, listing, ts)
        s.flush()
        for listing in listings:
            record_snapshot(s, listing, watch, ts, distance_miles=0.5)
        s.commit()


def test_a_home_cut_twice_shows_the_cumulative_number_not_two_entries(sessions):
    _sweep(sessions, T1, [make_listing("111", price=500_000)])
    _sweep(sessions, T2, [make_listing("111", price=480_000)])
    _sweep(sessions, T3, [make_listing("111", price=465_000)])

    with sessions() as s:
        changes = price_change_map(s, WATCH)

    change = changes["111"]
    assert change["first"] == 500_000
    assert change["last"] == 465_000
    assert change["cut_dollars"] == 35_000
    assert change["cut_pct"] == pytest.approx(7.0)


def test_a_never_cut_home_shows_nothing(sessions):
    _sweep(sessions, T1, [make_listing("111", price=500_000), make_listing("222", price=700_000)])
    _sweep(sessions, T2, [make_listing("111", price=500_000), make_listing("222", price=680_000)])

    with sessions() as s:
        changes = price_change_map(s, WATCH)

    assert "111" not in changes  # held its ask across two sweeps
    assert changes["222"]["cut_dollars"] == 20_000


def test_a_home_seen_only_once_has_nothing_to_compare_and_shows_nothing(sessions):
    _sweep(sessions, T1, [make_listing("111", price=500_000)])

    with sessions() as s:
        changes = price_change_map(s, WATCH)

    assert changes == {}


def test_a_rise_nets_negative_the_map_is_not_cuts_only(sessions):
    _sweep(sessions, T1, [make_listing("111", price=500_000)])
    _sweep(sessions, T2, [make_listing("111", price=525_000)])

    with sessions() as s:
        changes = price_change_map(s, WATCH)

    assert changes["111"]["cut_dollars"] == -25_000
    assert changes["111"]["cut_pct"] == pytest.approx(-5.0)


def test_a_cut_then_a_full_recovery_to_the_original_ask_shows_nothing(sessions):
    """First and last are the whole story here — a home that dipped and bounced all the
    way back to its original ask has, by this map's honest question, not moved at all."""
    _sweep(sessions, T1, [make_listing("111", price=500_000)])
    _sweep(sessions, T2, [make_listing("111", price=450_000)])
    _sweep(sessions, T3, [make_listing("111", price=500_000)])

    with sessions() as s:
        changes = price_change_map(s, WATCH)

    assert "111" not in changes


def test_two_watches_keep_separate_price_histories_of_the_same_home(sessions):
    _sweep(sessions, T1, [make_listing("111", price=500_000)])
    _sweep(sessions, T2, [make_listing("111", price=480_000)])
    _sweep(sessions, T1, [make_listing("111", price=500_000)], watch="walsh-aledo-sold")

    with sessions() as s:
        aledo = price_change_map(s, WATCH)
        sold = price_change_map(s, "walsh-aledo-sold")

    assert aledo["111"]["cut_dollars"] == 20_000
    assert sold == {}  # one sighting on the sold side — nothing to compare
