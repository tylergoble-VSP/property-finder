"""Five sweeps of a market that will not hold still.

Real inventory does not arrive once and sit there. It appears, gets cut, goes under
contract and vanishes, then comes back in three weeks at a different price — and the
store has to keep all of that straight without ever losing a home's identity or its
past. This is the shape of history the reports are built on, so it is worth proving
against a market that misbehaves in every way at once before anything reads from it.

The market below, sweep by sweep (a dash means "not on the market that day"):

    home   sweep 1   sweep 2   sweep 3   sweep 4   sweep 5
    111    500,000   500,000   480,000   480,000   465,000   cut, and cut again
    222    700,000   700,000      –         –         –      sold and gone for good
    333       –      615,000      –      590,000   590,000   under contract, then back
    444       –         –         –         –      425,000   arrives at the last minute
    555    350,000      –         –         –      375,000   a long absence, then a rise
"""
from conftest import make_listing

from propertyfinder.domain import PropertySnapshot, WatchedProperty
from propertyfinder.store import (
    latest_snapshot_rows,
    previous_snapshot_map,
    record_snapshot,
    upsert_property,
)

WATCH = "walsh-aledo"
TIMELINE = [
    "2026-07-10T10:00:00Z",
    "2026-07-11T10:00:00Z",
    "2026-07-12T10:00:00Z",
    "2026-07-13T10:00:00Z",
    "2026-07-14T10:00:00Z",
]
MARKET = {
    "111": [500_000, 500_000, 480_000, 480_000, 465_000],
    "222": [700_000, 700_000, None, None, None],
    "333": [None, 615_000, None, 590_000, 590_000],
    "444": [None, None, None, None, 425_000],
    "555": [350_000, None, None, None, 375_000],
}


def _sweep(sessions, index: int) -> None:
    """Persist everything on the market at sweep `index`, as a sweep would."""
    ts = TIMELINE[index]
    on_market = [
        make_listing(zpid, price=prices[index])
        for zpid, prices in MARKET.items()
        if prices[index] is not None
    ]
    with sessions() as s:
        for listing in on_market:
            upsert_property(s, listing, ts)
        s.flush()  # parents before children
        for listing in on_market:
            record_snapshot(s, listing, WATCH, ts, 0.5, "for_sale")
        s.commit()


def _newest_prices_through(index: int) -> dict[str, float]:
    """What the newest sighting of each home says, considering sweeps 0..index."""
    newest = {}
    for zpid, prices in MARKET.items():
        seen = [p for p in prices[: index + 1] if p is not None]
        if seen:
            newest[zpid] = seen[-1]
    return newest


def test_the_newest_sighting_wins_after_every_single_sweep(sessions):
    """Checked at each step rather than only at the end: a store that is right about
    the final state but wrong in the middle would still have shipped four wrong
    reports."""
    for index in range(len(TIMELINE)):
        _sweep(sessions, index)
        with sessions() as s:
            rows = {r["zpid"]: r["price"] for r in latest_snapshot_rows(s, WATCH)}
        assert rows == _newest_prices_through(index)


def test_a_home_that_leaves_the_market_is_remembered_not_deleted(sessions):
    """222 sells at the third sweep and is never seen again. Its identity and its whole
    price history stay — 'gone' is something a report concludes, never something the
    store forgets."""
    for index in range(len(TIMELINE)):
        _sweep(sessions, index)
    with sessions() as s:
        home = s.get(WatchedProperty, "222")
        assert home is not None and home.last_seen == TIMELINE[1]
        history = (
            s.query(PropertySnapshot)
            .filter_by(zpid="222")
            .order_by(PropertySnapshot.snapshot_ts)
            .all()
        )
        assert [h.price for h in history] == [700_000, 700_000]


def test_a_home_that_comes_back_keeps_the_identity_row_it_left_with(sessions):
    """555 disappears for three sweeps and returns at the fifth. One home, one row —
    if a return minted a second identity, every history query about it would split in
    two and 'back on market, $25,000 higher' could never be said."""
    for index in range(len(TIMELINE)):
        _sweep(sessions, index)
    with sessions() as s:
        assert s.query(WatchedProperty).filter_by(zpid="555").count() == 1
        home = s.get(WatchedProperty, "555")
        assert home.first_seen == TIMELINE[0]  # still the day we first saw it
        assert home.last_seen == TIMELINE[4]
        prices = [
            r.price
            for r in s.query(PropertySnapshot)
            .filter_by(zpid="555")
            .order_by(PropertySnapshot.snapshot_ts)
        ]
        assert prices == [350_000, 375_000]  # two sightings, three sweeps apart


def test_one_identity_row_per_home_and_one_observation_per_sighting(sessions):
    for index in range(len(TIMELINE)):
        _sweep(sessions, index)
    sightings = sum(1 for prices in MARKET.values() for p in prices if p is not None)
    with sessions() as s:
        assert s.query(WatchedProperty).count() == len(MARKET)
        assert s.query(PropertySnapshot).count() == sightings


def test_the_baseline_compares_a_returning_home_to_when_we_last_saw_it(sessions):
    """Not to the last sweep — to the last *sighting*. 555 was 350,000 three sweeps ago
    and is 375,000 today, and that is the interesting sentence."""
    for index in range(len(TIMELINE) - 1):
        _sweep(sessions, index)
    with sessions() as s:
        baseline = previous_snapshot_map(s, WATCH, TIMELINE[4])
    assert baseline["555"]["price"] == 350_000
    assert baseline["555"]["snapshot_ts"] == TIMELINE[0]
    assert MARKET["555"][4] - baseline["555"]["price"] == 25_000


def test_the_baseline_dates_each_home_so_left_and_changed_stay_different_questions(
    sessions,
):
    """The baseline is the newest sighting per home over all of history, which is right
    for 'what changed' and wrong for 'what left' — a home that sold in July must not be
    re-reported as newly gone every day until Christmas. The timestamp on each baseline
    row is what separates the two: only homes last seen in the immediately preceding
    sweep can have just left."""
    for index in range(len(TIMELINE) - 1):  # through sweep 4
        _sweep(sessions, index)
    with sessions() as s:
        baseline = previous_snapshot_map(s, WATCH, TIMELINE[4])

    assert set(baseline) == {"111", "222", "333", "555"}  # everything ever seen
    previous_sweep_ts = max(row["snapshot_ts"] for row in baseline.values())
    in_previous_sweep = {z for z, r in baseline.items() if r["snapshot_ts"] == previous_sweep_ts}
    assert in_previous_sweep == {"111", "333"}

    on_market_now = {z for z, prices in MARKET.items() if prices[4] is not None}
    assert in_previous_sweep - on_market_now == set()  # nothing left at the fifth sweep
    assert "222" not in in_previous_sweep  # long gone, and not gone again today
