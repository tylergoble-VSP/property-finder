"""The movement strip, proved end to end: database in, rendered page out.

No browser runs here — there is none, offline, and none is needed — so this cannot watch
the template's own script turn the payload into the "Since the last sweep" sentence. What
it *can* prove, honestly, is that the embedded JSON a browser would read from actually
carries the right history, for both shapes the strip has to degrade to: a market swept
only once, and a market swept twice with something to say about it.
"""
import json

from conftest import make_listing

from propertyfinder.config import Watch
from propertyfinder.pagebuild import render
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
            record_snapshot(s, listing, WATCH.name, ts, distance_miles=0.8)
        s.commit()


def _page(sessions) -> str:
    with sessions() as s:
        payload = build_payload(s, WATCH, GENERATED)
    return render("report.html", payload), payload


def test_a_single_sweep_page_carries_no_history_yet(sessions):
    _sweep(sessions, T1, [make_listing("111", price=500_000)])
    page, _payload = _page(sessions)

    assert '"history_began":false' in page
    assert '"cuts":[]' in page and '"new":[]' in page and '"gone":[]' in page


def test_a_second_sweep_page_carries_the_cut_and_the_departure(sessions):
    _sweep(
        sessions,
        T1,
        [
            make_listing("111", price=500_000, address="111 A St"),
            make_listing("222", price=700_000, address="222 B St"),
        ],
    )
    _sweep(sessions, T2, [make_listing("111", price=480_000, address="111 A St")])

    page, payload = _page(sessions)
    cut = payload["movement"]["cuts"][0]
    gone = payload["movement"]["gone"][0]

    assert '"history_began":true' in page
    assert json.dumps(cut["address"]) in page
    assert json.dumps(cut["delta"]) in page  # -20,000, whatever its exact JSON spelling
    assert json.dumps(gone["address"]) in page


def test_a_home_cut_twice_carries_its_cumulative_cut_into_the_page(sessions):
    _sweep(sessions, T1, [make_listing("111", price=500_000)])
    _sweep(sessions, T2, [make_listing("111", price=480_000)])
    _sweep(sessions, "2026-07-12T10:00:00Z", [make_listing("111", price=465_000)])

    page, payload = _page(sessions)
    row = payload["listings"][0]

    assert row["price_cut_dollars"] == 35_000
    assert json.dumps(row["price_cut_dollars"]) in page


def test_a_never_cut_home_carries_no_cut_figure_into_the_page(sessions):
    _sweep(sessions, T1, [make_listing("111", price=500_000)])
    _sweep(sessions, T2, [make_listing("111", price=500_000)])

    page, payload = _page(sessions)
    row = payload["listings"][0]

    assert row["price_cut_dollars"] is None
    assert '"price_cut_dollars":null' in page
