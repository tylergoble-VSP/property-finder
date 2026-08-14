"""Persisting a sweep, and saying what moved since the last one.

Two properties are worth more than the rest: the write is all-or-nothing (a half-stored
sweep would not merely lose data, it would invent history for the next sweep to compare
against), and the diff answers "what changed" and "what left" against different
baselines, because they are different questions.
"""
import pytest
from sqlalchemy import text

from conftest import RoutedSearchApi

from propertyfinder.config import Watch
from propertyfinder.domain import PropertySnapshot, WatchedProperty
from propertyfinder.sweep import run_sweep

ALEDO = "Aledo, TX 76008"
T1, T2, T3 = "2026-07-10T10:00:00Z", "2026-07-11T10:00:00Z", "2026-07-12T10:00:00Z"


def _watch(name: str = "walsh-aledo", status: str = "for_sale") -> Watch:
    return Watch(
        name=name,
        center_address="2112 Eastus Ln, Aledo, TX 76008",
        lat=32.73665,
        lon=-97.55626,
        radius_miles=2.0,
        listing_status=status,
        queries=[ALEDO],
    )


def _row(zpid: str, price: float, status_text: str = "House for sale") -> dict:
    return {
        "zpid": zpid,
        "address": f"{zpid} Walsh Ave, Aledo, TX 76008",
        "extracted_price": price,
        "beds": 4,
        "baths": 3,
        "sqft": 3000,
        "home_type": "SINGLE_FAMILY",
        "home_status": "FOR_SALE",
        "status_text": status_text,
        "latitude": 32.7400,
        "longitude": -97.5600,
    }


def _market(*rows: dict) -> dict:
    return {"properties": list(rows), "pagination": {"current_page": 1, "total_pages": 1}}


def _sweep(sessions, make_adapter, market: dict, ts: str, watch: Watch | None = None):
    adapter = make_adapter(RoutedSearchApi({ALEDO: market}))
    with sessions() as s:
        return run_sweep(s, adapter, watch or _watch(), now=ts)


# -- the first sweep -------------------------------------------------------------------


def test_a_first_sweep_stores_everything_and_calls_it_all_new(sessions, make_adapter):
    summary = _sweep(sessions, make_adapter, _market(_row("111", 500_000), _row("222", 700_000)), T1)

    assert summary.in_radius == 2
    assert {listing.zpid for listing in summary.new} == {"111", "222"}
    assert summary.cuts == [] and summary.rises == [] and summary.gone == []
    assert summary.snapshot_ts == T1 and summary.api_calls == 1

    with sessions() as s:
        assert s.query(WatchedProperty).count() == 2
        assert s.query(PropertySnapshot).count() == 2
        assert s.get(WatchedProperty, "111").first_seen == T1


def test_every_observation_points_at_a_home_the_database_already_knew(sessions, make_adapter):
    """The parents-before-children ordering, proved the only way that matters: with
    foreign keys enforced, a sweep that inserted a snapshot before its property would
    have raised rather than reached this assertion."""
    _sweep(sessions, make_adapter, _market(_row("111", 500_000), _row("222", 700_000)), T1)
    with sessions() as s:
        orphans = s.execute(
            text(
                "SELECT COUNT(*) FROM snapshots s "
                "LEFT JOIN properties p ON p.zpid = s.zpid WHERE p.zpid IS NULL"
            )
        ).scalar()
    assert orphans == 0


def test_the_spend_reported_is_this_watch_and_not_the_whole_morning(sessions, make_adapter):
    """A daily run sweeps several watches through one adapter. Reporting the adapter's
    running total per watch would make the second watch look twice as expensive as it
    was, and the budget arithmetic downstream leans on this number."""
    adapter = make_adapter(RoutedSearchApi({ALEDO: _market(_row("111", 500_000))}))
    with sessions() as s:
        first = run_sweep(s, adapter, _watch(), now=T1)
    with sessions() as s:
        second = run_sweep(s, adapter, _watch("walsh-aledo-sold", "sold"), now=T1)
    assert first.api_calls == 1 and second.api_calls == 1
    assert adapter.request_count == 2


# -- the transaction -------------------------------------------------------------------


def test_a_failure_partway_through_leaves_the_database_exactly_as_it_was(
    sessions, make_adapter, monkeypatch
):
    """The injected failure lands after several homes have been written and flushed —
    the worst moment, and the one that would leave a plausible-looking partial sweep."""
    import propertyfinder.sweep as sweep_module

    real = sweep_module.record_snapshot
    calls = {"n": 0}

    def fails_on_the_third(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 3:
            raise RuntimeError("the provider's page turned to soup mid-write")
        return real(*args, **kwargs)

    monkeypatch.setattr(sweep_module, "record_snapshot", fails_on_the_third)
    market = _market(*[_row(str(z), 500_000) for z in range(111, 116)])

    with pytest.raises(RuntimeError):
        _sweep(sessions, make_adapter, market, T1)

    with sessions() as s:
        assert s.query(WatchedProperty).count() == 0
        assert s.query(PropertySnapshot).count() == 0


def test_a_failed_sweep_does_not_damage_the_history_already_stored(
    sessions, make_adapter, monkeypatch
):
    _sweep(sessions, make_adapter, _market(_row("111", 500_000)), T1)

    import propertyfinder.sweep as sweep_module

    monkeypatch.setattr(
        sweep_module,
        "record_snapshot",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    with pytest.raises(RuntimeError):
        _sweep(sessions, make_adapter, _market(_row("111", 480_000), _row("222", 700_000)), T2)

    with sessions() as s:
        assert s.query(WatchedProperty).count() == 1
        assert s.query(PropertySnapshot).one().price == 500_000  # yesterday, intact


# -- the diff --------------------------------------------------------------------------


def test_the_second_sweep_reports_cuts_rises_status_flips_and_departures(sessions, make_adapter):
    _sweep(
        sessions,
        make_adapter,
        _market(_row("111", 500_000), _row("222", 700_000), _row("333", 600_000)),
        T1,
    )
    summary = _sweep(
        sessions,
        make_adapter,
        _market(
            _row("111", 480_000),
            _row("222", 720_000, status_text="Pending"),
            _row("444", 425_000),
        ),
        T2,
    )

    assert [listing.zpid for listing in summary.new] == ["444"]
    assert [(c.zpid, c.delta) for c in summary.cuts] == [("111", -20_000)]
    assert [(r.zpid, r.delta) for r in summary.rises] == [("222", 20_000)]
    assert [(f.zpid, f.previous, f.current) for f in summary.status_changes] == [
        ("222", "House for sale", "Pending")
    ]
    assert summary.gone == ["333"]
    assert summary.in_radius == 3


def test_a_home_that_holds_still_is_reported_as_nothing_at_all(sessions, make_adapter):
    market = _market(_row("111", 500_000))
    _sweep(sessions, make_adapter, market, T1)
    summary = _sweep(sessions, make_adapter, market, T2)
    assert not (summary.new or summary.cuts or summary.rises or summary.status_changes)
    assert summary.gone == [] and summary.in_radius == 1


def test_a_change_is_measured_against_the_last_sighting_however_long_ago(sessions, make_adapter):
    """A home vanishes for a sweep and returns cheaper. The interesting number is the
    whole cut since we last saw it, not a comparison against a sweep it was absent
    from — and it is not 'new', because we have met it before."""
    _sweep(sessions, make_adapter, _market(_row("111", 500_000), _row("222", 700_000)), T1)
    _sweep(sessions, make_adapter, _market(_row("222", 700_000)), T2)
    summary = _sweep(sessions, make_adapter, _market(_row("111", 465_000), _row("222", 700_000)), T3)

    assert summary.new == []
    assert [(c.zpid, c.delta, c.since) for c in summary.cuts] == [("111", -35_000, T1)]


def test_a_home_long_gone_is_not_reported_gone_again_every_morning(sessions, make_adapter):
    """'Gone' means it left since the last sweep. A July sale that kept turning up in
    the departures list until Christmas would train everyone to ignore the list."""
    _sweep(sessions, make_adapter, _market(_row("111", 500_000), _row("222", 700_000)), T1)
    left = _sweep(sessions, make_adapter, _market(_row("111", 500_000)), T2)
    still_away = _sweep(sessions, make_adapter, _market(_row("111", 500_000)), T3)

    assert left.gone == ["222"]
    assert still_away.gone == []


def test_the_headline_says_what_happened_in_one_line(sessions, make_adapter):
    _sweep(sessions, make_adapter, _market(_row("111", 500_000)), T1)
    summary = _sweep(sessions, make_adapter, _market(_row("111", 480_000)), T2)
    assert summary.headline() == (
        "walsh-aledo: 1 in radius · 0 new · 1 cut · 0 raised · 0 status · 0 gone · 1 call(s)"
    )


def test_two_watches_keep_separate_histories_of_the_same_home(sessions, make_adapter):
    """The for-sale circle and its sold companion overlap by design, and neither may
    see the other's observations as its own past."""
    _sweep(sessions, make_adapter, _market(_row("111", 500_000)), T1)
    sold_side = _sweep(
        sessions, make_adapter, _market(_row("111", 500_000)), T2, _watch("walsh-aledo-sold", "sold")
    )
    assert [listing.zpid for listing in sold_side.new] == ["111"]  # new to *this* watch

    with sessions() as s:
        assert s.query(WatchedProperty).count() == 1  # one home, two observers
        assert s.query(PropertySnapshot).count() == 2
