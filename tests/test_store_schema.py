"""The two tables, and the two things the database itself must refuse.

A schema that merely *describes* the history model is a comment. These tests check that
it *enforces* it: one observation per home per watch per sweep, and never an observation
of a home the database has never heard of.
"""
import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from conftest import make_listing

from propertyfinder.domain import PropertySnapshot, WatchedProperty
from propertyfinder.store import record_snapshot, upsert_property
from propertyfinder.timeutil import TS_FORMAT, utc_now_iso

T1 = "2026-07-10T10:00:00Z"
T2 = "2026-07-11T10:00:00Z"


def test_the_schema_is_two_tables_identity_and_observation(engine):
    tables = set(inspect(engine).get_table_names())
    assert {"properties", "snapshots"} <= tables


def test_a_home_and_an_observation_of_it_round_trip(sessions):
    with sessions() as s:
        upsert_property(s, make_listing("111"), T1)
        s.flush()
        record_snapshot(s, make_listing("111"), "walsh-aledo", T1, 0.4, "for_sale")
        s.commit()

    with sessions() as s:
        home = s.get(WatchedProperty, "111")
        assert home.first_seen == T1 and home.last_seen == T1 and home.sqft == 3012
        seen = s.query(PropertySnapshot).one()
        assert seen.watch_name == "walsh-aledo" and seen.price == 674900
        assert seen.distance_miles == pytest.approx(0.4)


def test_the_same_home_twice_in_one_sweep_is_refused_by_the_database(sessions):
    """The unique constraint is the history model's spine: one home, one watch, one
    sweep, one row. A re-run that silently doubled a sweep would double every count the
    tool later reports."""
    with sessions() as s:
        upsert_property(s, make_listing("111"), T1)
        s.flush()
        record_snapshot(s, make_listing("111"), "walsh-aledo", T1)
        record_snapshot(s, make_listing("111"), "walsh-aledo", T1)
        with pytest.raises(IntegrityError):
            s.commit()


def test_the_same_home_in_two_watches_at_one_moment_is_fine(sessions):
    """Two watches may legitimately both see a home — the for-sale circle and its sold
    companion overlap by design. Only (home, watch, sweep) has to be unique."""
    with sessions() as s:
        upsert_property(s, make_listing("111"), T1)
        s.flush()
        record_snapshot(s, make_listing("111"), "walsh-aledo", T1)
        record_snapshot(s, make_listing("111"), "walsh-aledo-sold", T1)
        s.commit()
        assert s.query(PropertySnapshot).count() == 2


def test_an_observation_of_an_unknown_home_is_refused(sessions):
    """Foreign keys are off by default in SQLite, which is why `build_engine` turns them
    on per connection. Without that pragma this row lands happily and the report that
    joins it to identity silently loses the home."""
    with sessions() as s:
        record_snapshot(s, make_listing("999"), "walsh-aledo", T1)
        with pytest.raises(IntegrityError):
            s.commit()


def test_a_later_sighting_backfills_but_never_erases(sessions):
    """The feed drops fields at random. A sighting that reports no square footage means
    'not mentioned', not 'no longer 3,012 feet'."""
    with sessions() as s:
        upsert_property(s, make_listing("111", sqft=None, image_url=None), T1)
        s.commit()
    with sessions() as s:
        upsert_property(s, make_listing("111", sqft=3012, image_url="photo.jpg"), T2)
        s.commit()
    with sessions() as s:
        upsert_property(s, make_listing("111", sqft=None, image_url=None), "2026-07-12T10:00:00Z")
        s.commit()
    with sessions() as s:
        home = s.get(WatchedProperty, "111")
        assert home.sqft == 3012 and home.image_url == "photo.jpg"
        assert home.first_seen == T1  # fixed once, by definition
        assert home.last_seen == "2026-07-12T10:00:00Z"


def test_timestamps_sort_as_strings_because_they_are_fixed_width_utc():
    """Every window query in this tool orders by a text column. That is only sound
    because the format is fixed-width UTC, so string order *is* chronological order."""
    assert TS_FORMAT == "%Y-%m-%dT%H:%M:%SZ"
    now = utc_now_iso()
    assert len(now) == 20 and now.endswith("Z")
    assert sorted(["2026-07-11T09:00:00Z", "2026-07-10T23:00:00Z"])[0].startswith("2026-07-10")
