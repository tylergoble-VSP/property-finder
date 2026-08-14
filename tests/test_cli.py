"""The walking skeleton, driven end to end.

These tests run the actual command line — argument parsing, settings, migrations, the
adapter, the sweep, the print — against a fake internet and a database in a temporary
folder. Everything is real except the provider.

The working directory is moved to that folder on purpose. It means no `.env` is in
reach, so no test can pick up the real API key even by accident, and the config file
under test is the one written three lines above.
"""
import itertools

import pytest
from sqlalchemy import create_engine, text

from conftest import RoutedSearchApi

from propertyfinder import cli, sweep
from propertyfinder.adapters import ZillowAdapter
from propertyfinder.cli import main

ALEDO = "Aledo, TX 76008"

WATCH_CONFIG = """
currency: USD
watches:
  - name: walsh-aledo
    center_address: "2112 Eastus Ln, Aledo, TX 76008"
    lat: 32.73665
    lon: -97.55626
    radius_miles: 2.0
    listing_status: for_sale
    max_pages: 4
    queries: ["Aledo, TX 76008"]
  - name: walsh-aledo-sold
    center_address: "2112 Eastus Ln, Aledo, TX 76008"
    lat: 32.73665
    lon: -97.55626
    radius_miles: 2.0
    listing_status: sold
    max_pages: 4
    queries: ["Aledo, TX 76008"]
"""


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


class _WideAwakeAdapter(ZillowAdapter):
    """The real adapter, minus the nap.

    `sweep` builds its own adapter, which is the point of the command — but the 0.35
    second politeness delay is production behaviour already proved in test_budget.py,
    and buying those seconds again here would make the suite slower than the sweep.
    """

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("sleep", lambda _seconds: None)
        super().__init__(*args, **kwargs)


def _daily_clock():
    """A clock that moves a day per sweep, which is this tool's actual cadence.

    Timestamps are stored to the second, so two sweeps of one watch inside the same
    second are one sweep as far as the database is concerned and the unique constraint
    refuses the second — correctly, but it makes a test about *history* depend on how
    fast the machine is. A day between sweeps is both realistic and deterministic.
    """
    day = itertools.count(10)

    def now() -> str:
        return f"2026-07-{next(day):02d}T10:00:00Z"

    return now


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A working directory with a watch config, a database path, and no secrets."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sweep, "utc_now_iso", _daily_clock())
    (tmp_path / "watch-config.yaml").write_text(WATCH_CONFIG)
    monkeypatch.setenv("SEARCHAPI_API_KEY", "test-key-not-a-real-one")
    monkeypatch.setenv("PROPERTYFINDER_DB_PATH", str(tmp_path / "finder.db"))
    monkeypatch.setenv("QUOTA_CAP_SEARCHAPI_MONTHLY", "1000")
    monkeypatch.setattr(cli, "ZillowAdapter", _WideAwakeAdapter)
    return tmp_path


def _client(market: dict):
    import httpx

    transport = RoutedSearchApi({ALEDO: market})
    return httpx.Client(transport=transport), transport


def _stored(home) -> list[tuple]:
    engine = create_engine(f"sqlite:///{home / 'finder.db'}")
    with engine.begin() as conn:
        return conn.execute(
            text(
                "SELECT s.zpid, s.price, s.watch_name, p.address FROM snapshots s "
                "JOIN properties p ON p.zpid = s.zpid ORDER BY s.watch_name, s.zpid"
            )
        ).fetchall()


# -- init and watches ------------------------------------------------------------------


def test_init_builds_the_database_and_says_what_version_it_is(home, capsys):
    from propertyfinder.migrations import discover

    latest = max(m.version for m in discover())
    assert main(["init"]) == 0
    assert (home / "finder.db").exists()
    out = capsys.readouterr().out
    assert f"schema version {latest}" in out and f"applied {latest} migration" in out


def test_init_is_safe_to_run_again(home, capsys):
    main(["init"])
    capsys.readouterr()
    assert main(["init"]) == 0
    assert "already current" in capsys.readouterr().out


def test_watches_lists_what_is_configured(home, capsys):
    assert main(["watches"]) == 0
    out = capsys.readouterr().out
    assert "walsh-aledo: for_sale within 2 mi of 2112 Eastus Ln" in out
    assert "walsh-aledo-sold: sold within 2 mi" in out
    assert "queries: Aledo, TX 76008" in out


# -- the sweep, end to end -------------------------------------------------------------


def test_a_sweep_lands_homes_in_the_database_and_reports_them(home, capsys):
    client, transport = _client(_market(_row("111", 500_000), _row("222", 700_000)))

    assert main(["sweep", "--watch", "walsh-aledo"], client=client) == 0

    out = capsys.readouterr().out
    assert "walsh-aledo: 2 in radius · 2 new · 0 cut" in out
    assert "budget: 1/1000 calls spent (999 left)" in out

    rows = _stored(home)
    assert [(r[0], r[1], r[2]) for r in rows] == [
        ("111", 500_000, "walsh-aledo"),
        ("222", 700_000, "walsh-aledo"),
    ]
    assert transport.queries_asked == [ALEDO]


def test_a_second_sweep_prints_what_moved(home, capsys):
    first, _ = _client(_market(_row("111", 500_000), _row("222", 700_000)))
    main(["sweep", "--watch", "walsh-aledo"], client=first)
    capsys.readouterr()

    second, _ = _client(_market(_row("111", 465_000, status_text="Pending")))
    assert main(["sweep", "--watch", "walsh-aledo"], client=second) == 0

    out = capsys.readouterr().out
    assert "1 in radius · 0 new · 1 cut · 0 raised · 1 status · 1 gone" in out
    assert "down  111 Walsh Ave, Aledo, TX 76008: $500,000 -> $465,000 (-35,000" in out
    assert "now   111 Walsh Ave, Aledo, TX 76008: House for sale -> Pending" in out


def test_sweeping_everything_visits_every_watch(home, capsys):
    client, transport = _client(_market(_row("111", 500_000)))
    assert main(["sweep"], client=client) == 0

    out = capsys.readouterr().out
    assert "walsh-aledo: 1 in radius" in out and "walsh-aledo-sold: 1 in radius" in out
    assert "budget: 2/1000 calls spent" in out
    assert len(_stored(home)) == 2  # one home, observed by both watches
    assert transport.requests[1].url.params.get("listing_status") == "sold"


def test_an_unknown_watch_name_fails_without_spending_anything(home, capsys):
    client, transport = _client(_market(_row("111", 500_000)))
    assert main(["sweep", "--watch", "nowhere"], client=client) == 1
    assert "no watch named 'nowhere'" in capsys.readouterr().out
    assert transport.requests == []


def test_the_budget_flag_is_a_ceiling_the_run_will_not_cross(home, capsys):
    """One call is enough for the first watch and not for the second, so the run stops
    with the second request unsent — and says so rather than half-finishing quietly."""
    client, transport = _client(_market(_row("111", 500_000)))
    assert main(["sweep", "--budget", "1"], client=client) == 1

    out = capsys.readouterr().out
    assert "walsh-aledo: 1 in radius" in out
    assert "stopped before spending more than the ceiling" in out
    assert "budget: 1/1 calls spent (0 left)" in out
    assert len(transport.requests) == 1
    assert {r[2] for r in _stored(home)} == {"walsh-aledo"}  # the first watch, complete


def test_a_budget_of_zero_sends_nothing_at_all(home, capsys):
    client, transport = _client(_market(_row("111", 500_000)))
    assert main(["sweep", "--budget", "0"], client=client) == 1
    assert transport.requests == []
    assert _stored(home) == []


def test_the_sweep_builds_the_schema_it_needs(home):
    """A person who runs `sweep` first should get a sweep, not a lecture about `init`."""
    client, _ = _client(_market(_row("111", 500_000)))
    assert main(["sweep", "--watch", "walsh-aledo"], client=client) == 0
    assert len(_stored(home)) == 1


# -- report, built from whatever sweep has already stored -----------------------------


def test_report_writes_a_dated_archive_and_a_canonical_latest(home, capsys):
    client, _ = _client(_market(_row("111", 500_000)))
    main(["sweep", "--watch", "walsh-aledo"], client=client)
    capsys.readouterr()

    assert main(["report", "--watch", "walsh-aledo"]) == 0
    out = capsys.readouterr().out
    assert "walsh-aledo: 1 listing(s)" in out

    dated = list((home / "reports").glob("walsh-aledo-*.html"))
    latest = home / "reports" / "walsh-aledo.html"
    assert len(dated) == 1
    assert latest.exists()
    assert dated[0].read_text() == latest.read_text()
    assert "111 Walsh Ave, Aledo, TX 76008" in latest.read_text()


def test_two_reports_on_the_same_day_are_byte_for_byte_identical(home, monkeypatch):
    """Idempotence proven the way the rest of this suite proves anything time-shaped: by
    holding the clock still, exactly as `_daily_clock` holds it moving for sweeps."""
    client, _ = _client(_market(_row("111", 500_000)))
    main(["sweep", "--watch", "walsh-aledo"], client=client)

    monkeypatch.setattr(cli, "utc_now_iso", lambda: "2026-07-20T09:00:00Z")
    assert main(["report", "--watch", "walsh-aledo"]) == 0
    first = (home / "reports" / "walsh-aledo.html").read_bytes()

    assert main(["report", "--watch", "walsh-aledo"]) == 0
    second = (home / "reports" / "walsh-aledo.html").read_bytes()

    assert first == second


def test_report_with_no_watch_named_covers_every_watch(home):
    client, _ = _client(_market(_row("111", 500_000)))
    main(["sweep"], client=client)

    assert main(["report"]) == 0
    assert (home / "reports" / "walsh-aledo.html").exists()
    assert (home / "reports" / "walsh-aledo-sold.html").exists()


def test_report_degrades_gracefully_with_no_sweep_on_record(home):
    """A person who runs `report` before ever running `sweep` gets an honest empty page,
    not a crash and not a lecture about running `sweep` first."""
    assert main(["report", "--watch", "walsh-aledo"]) == 0
    page = (home / "reports" / "walsh-aledo.html").read_text()
    assert '"total":0' in page


def test_an_unknown_watch_name_reports_nothing_and_writes_no_files(home, capsys):
    assert main(["report", "--watch", "nowhere"]) == 1
    assert "no watch named 'nowhere'" in capsys.readouterr().out
    assert not (home / "reports").exists()


def test_predictions_reports_an_empty_loop_honestly(home, capsys):
    main(["init"])
    capsys.readouterr()
    assert main(["predictions"]) == 0
    out = capsys.readouterr().out
    assert "0 resolved" in out and "nothing resolved yet" in out
