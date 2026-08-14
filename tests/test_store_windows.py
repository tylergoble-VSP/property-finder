"""The two window queries, against three sweeps of hand-built history.

The data below is small enough to reason about by eye and awkward enough to be worth
asking: one home holds still, one is cut twice, one arrives late, and one belongs to a
different watch entirely. If either query is wrong, it is wrong here.
"""
from conftest import make_listing

from propertyfinder.store import (
    latest_snapshot_rows,
    previous_snapshot_map,
    record_snapshot,
    upsert_property,
)

WATCH = "walsh-aledo"
T1, T2, T3 = "2026-07-10T10:00:00Z", "2026-07-11T10:00:00Z", "2026-07-12T10:00:00Z"

# zpid -> price at each of the three sweeps. None means "not on the market that day".
HISTORY = {
    "111": (500_000, 480_000, 465_000),  # cut, then cut again
    "222": (700_000, 700_000, 700_000),  # holds its ask
    "333": (None, None, 615_000),        # arrives at the third sweep
}


def _sweep(sessions, ts: str, watch: str = WATCH) -> None:
    for zpid, prices in HISTORY.items():
        price = prices[[T1, T2, T3].index(ts)]
        if price is None:
            continue
        listing = make_listing(zpid, price=price)
        with sessions() as s:
            upsert_property(s, listing, ts)
            s.flush()
            record_snapshot(s, listing, watch, ts, 0.5, "for_sale")
            s.commit()


def _three_sweeps(sessions) -> None:
    for ts in (T1, T2, T3):
        _sweep(sessions, ts)


def test_latest_rows_are_one_per_home_and_carry_identity(sessions):
    _three_sweeps(sessions)
    with sessions() as s:
        rows = {r["zpid"]: r for r in latest_snapshot_rows(s, WATCH)}

    assert set(rows) == {"111", "222", "333"}
    assert rows["111"]["price"] == 465_000  # the newest observation, not the first
    assert rows["333"]["price"] == 615_000
    assert rows["111"]["snapshot_ts"] == T3
    # joined to identity, and to plain values rather than ORM objects
    assert rows["111"]["sqft"] == 3012 and rows["111"]["address"].endswith("TX 76008")
    assert rows["111"]["first_seen"] == T1
    assert isinstance(rows["111"], dict)


def test_latest_rows_are_scoped_to_one_watch(sessions):
    _three_sweeps(sessions)
    _sweep(sessions, T1, watch="walsh-aledo-sold")
    with sessions() as s:
        assert len(latest_snapshot_rows(s, WATCH)) == 3
        assert len(latest_snapshot_rows(s, "walsh-aledo-sold")) == 2
        assert latest_snapshot_rows(s, "nobody-watches-this") == []


def test_the_baseline_is_the_sweep_before_the_one_being_written(sessions):
    """A sweep asks for the state of the world *strictly before* its own timestamp. Ask
    inclusively and every home compares equal to itself — the diff goes quiet and the
    product disappears."""
    _three_sweeps(sessions)
    with sessions() as s:
        before_third = previous_snapshot_map(s, WATCH, T3)
        assert before_third["111"]["price"] == 480_000
        assert "333" not in before_third  # not yet on the market at the second sweep

        before_second = previous_snapshot_map(s, WATCH, T2)
        assert before_second["111"]["price"] == 500_000
        assert set(before_second) == {"111", "222"}

        assert previous_snapshot_map(s, WATCH, T1) == {}  # nothing precedes the first


def test_without_a_cutoff_the_baseline_is_simply_the_latest_state(sessions):
    _three_sweeps(sessions)
    with sessions() as s:
        current = previous_snapshot_map(s, WATCH)
        latest = {r["zpid"]: r for r in latest_snapshot_rows(s, WATCH)}
    assert {z: r["price"] for z, r in current.items()} == {
        z: r["price"] for z, r in latest.items()
    }


def test_the_baseline_carries_what_a_diff_needs_and_no_more(sessions):
    _three_sweeps(sessions)
    with sessions() as s:
        row = previous_snapshot_map(s, WATCH, T3)["222"]
    assert set(row) == {
        "zpid",
        "price",
        "listing_status",
        "status_text",
        "days_on_zillow",
        "snapshot_ts",
    }
