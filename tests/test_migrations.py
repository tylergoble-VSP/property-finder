"""Schema versioning, which exists so that "which schema is this file?" has an answer.

The behaviour worth guarding is boring and absolute: a migration runs once, on databases
that have not run it, in version order, and the run is safe to repeat forever.
"""
import pytest
from sqlalchemy import inspect, text

from propertyfinder.config import Settings, build_engine
from propertyfinder.migrations import Migration, discover
from propertyfinder.store import run_migrations, schema_version


@pytest.fixture
def fresh(tmp_path):
    """An empty database file — no schema, no version stamp."""
    return build_engine(Settings(_env_file=None, db_path=str(tmp_path / "fresh.db")))


def _counter(version: int, calls: list):
    """A migration that records being run and creates something checkable."""

    def apply(conn):
        calls.append(version)
        conn.execute(text(f"CREATE TABLE step_{version} (id INTEGER PRIMARY KEY)"))

    return Migration(version=version, name=f"m{version:03d}_fake", apply=apply)


def test_an_unmigrated_database_reports_version_zero(fresh):
    assert schema_version(fresh) == 0


def test_the_baseline_migration_builds_the_two_tables(fresh):
    applied = run_migrations(fresh)
    assert [m.version for m in applied] == [m.version for m in discover()]
    assert {"properties", "snapshots"} <= set(inspect(fresh).get_table_names())
    assert schema_version(fresh) == max(m.version for m in discover())


def test_running_twice_applies_once(fresh):
    """`init` is safe to run daily, and every command may bring its own database up to
    date without asking whether someone already did."""
    first = run_migrations(fresh)
    second = run_migrations(fresh)
    assert first and second == []
    assert schema_version(fresh) == max(m.version for m in first)


def test_a_registered_migration_bumps_the_version_exactly_once(fresh):
    calls: list[int] = []
    steps = [_counter(1, calls), _counter(2, calls)]

    run_migrations(fresh, steps)
    run_migrations(fresh, steps)
    run_migrations(fresh, steps)

    assert calls == [1, 2]  # never re-run, in order, whatever the caller does
    assert schema_version(fresh) == 2


def test_a_new_step_added_later_runs_alone(fresh):
    """The ordinary case: a database is at version 1, the code ships version 2, and only
    the new step runs on the next command."""
    calls: list[int] = []
    run_migrations(fresh, [_counter(1, calls)])
    applied = run_migrations(fresh, [_counter(1, calls), _counter(2, calls)])

    assert calls == [1, 2]
    assert [m.version for m in applied] == [2]


def test_steps_are_applied_in_version_order_however_they_arrive(fresh):
    calls: list[int] = []
    run_migrations(fresh, [_counter(3, calls), _counter(1, calls), _counter(2, calls)])
    assert calls == [1, 2, 3]


def test_a_failing_step_is_left_unstamped_and_retried_next_run(fresh):
    """The honest guarantee. Python's SQLite driver commits schema statements
    implicitly, so a step that raises has already left some of its work behind — which
    is exactly why the runner records nothing until the step returns, and why migrations
    are required to be safe to run twice."""
    calls: list[int] = []
    attempts: list[int] = []

    def flaky(conn):
        attempts.append(1)
        conn.execute(text("CREATE TABLE IF NOT EXISTS step_2 (id INTEGER PRIMARY KEY)"))
        if len(attempts) == 1:
            raise RuntimeError("the migration author was wrong about something")

    steps = [_counter(1, calls), Migration(2, "m002_flaky", flaky)]
    with pytest.raises(RuntimeError):
        run_migrations(fresh, steps)

    assert schema_version(fresh) == 1  # the failed version was never recorded
    assert calls == [1]

    assert [m.version for m in run_migrations(fresh, steps)] == [2]
    assert attempts == [1, 1] and schema_version(fresh) == 2
    assert {"step_1", "step_2"} <= set(inspect(fresh).get_table_names())


def test_enrichment_columns_land_on_a_brand_new_database(fresh):
    """m001 *is* `create_all` over today's mapped metadata, and `properties` has carried
    the enrichment columns in that metadata since this commit — so a fresh database gets
    them at step 001, and m003 simply finds them already there."""
    run_migrations(fresh)
    columns = {c["name"] for c in inspect(fresh).get_columns("properties")}
    assert {"year_built", "hoa_monthly", "tax_rate", "enriched_ts"} <= columns


def test_enrichment_columns_are_added_to_a_database_that_predates_them(fresh):
    """The database m003 actually exists for: `properties` created before the
    enrichment columns joined the mapped metadata — impossible to reproduce by calling
    today's m001 (its `create_all` already carries them), so this builds the old shape
    by hand.
    """
    from propertyfinder.migrations import m003_property_enrichment

    with fresh.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE properties (
                    zpid TEXT PRIMARY KEY, address TEXT, home_type TEXT, beds REAL,
                    baths REAL, sqft REAL, lot_sqft REAL, lat REAL, lon REAL, link TEXT,
                    image_url TEXT, date_sold TEXT,
                    first_seen TEXT NOT NULL, last_seen TEXT NOT NULL
                )
                """
            )
        )
    columns_before = {c["name"] for c in inspect(fresh).get_columns("properties")}
    assert "enriched_ts" not in columns_before

    with fresh.begin() as conn:
        m003_property_enrichment.apply(conn)
    columns_after = {c["name"] for c in inspect(fresh).get_columns("properties")}
    assert {"year_built", "hoa_monthly", "tax_rate", "enriched_ts"} <= columns_after


def test_running_m003_twice_does_not_fail_on_a_duplicate_column(fresh):
    """SQLite has no `ADD COLUMN IF NOT EXISTS`; the migration guards itself instead."""
    from propertyfinder.migrations import m003_property_enrichment

    run_migrations(fresh)  # properties exists and already carries these columns
    with fresh.begin() as conn:
        m003_property_enrichment.apply(conn)
        m003_property_enrichment.apply(conn)  # must not raise "duplicate column name"
    columns = {c["name"] for c in inspect(fresh).get_columns("properties")}
    assert "enriched_ts" in columns


def test_two_migrations_may_not_claim_the_same_version(fresh):
    """A version is an identity. The runner refuses ambiguity rather than picking one,
    and refuses it before touching the database."""
    calls: list[int] = []
    with pytest.raises(ValueError, match="version 1"):
        run_migrations(fresh, [_counter(1, calls), _counter(1, calls)])
    assert calls == [] and schema_version(fresh) == 0
