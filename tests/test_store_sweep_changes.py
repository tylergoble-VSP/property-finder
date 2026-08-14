"""`sweep_changes` — the read-side twin of run_sweep's live diff, over hand-built history.

Same comparison as `sweep._diff`, same two baselines (the immediately preceding sweep
decides who left; the last sighting of a home, however long ago, decides what changed on
it) — just recovered from stored snapshots instead of a listing collected moments ago.
`test_sweep_run.py` proves the live version against a fake provider; this proves the
stored version answers identically once the sweeps are already in the database.
"""
from conftest import make_listing

from propertyfinder.store import record_snapshot, sweep_changes, upsert_property

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


def test_a_single_sweep_has_nothing_to_compare_against(sessions):
    _sweep(sessions, T1, [make_listing("111", price=500_000)])
    with sessions() as s:
        changes = sweep_changes(s, WATCH)

    assert changes == {
        "new": [],
        "cuts": [],
        "rises": [],
        "status_changes": [],
        "gone": [],
        "history_began": False,
    }


def test_an_empty_database_also_has_nothing_to_compare_against(sessions):
    with sessions() as s:
        assert sweep_changes(s, WATCH)["history_began"] is False


def test_the_second_sweep_yields_every_bucket_at_once(sessions):
    _sweep(
        sessions,
        T1,
        [
            make_listing("111", price=500_000, address="111 A St"),
            make_listing("222", price=700_000, address="222 B St"),
            make_listing("333", price=600_000, address="333 C St"),
        ],
    )
    _sweep(
        sessions,
        T2,
        [
            make_listing("111", price=480_000, address="111 A St"),
            make_listing("222", price=720_000, address="222 B St", status_text="Pending"),
            make_listing("444", price=425_000, address="444 D St"),
        ],
    )

    with sessions() as s:
        changes = sweep_changes(s, WATCH)

    assert changes["history_began"] is True
    assert [n["zpid"] for n in changes["new"]] == ["444"]
    assert [(c["zpid"], c["delta"]) for c in changes["cuts"]] == [("111", -20_000)]
    assert [(r["zpid"], r["delta"]) for r in changes["rises"]] == [("222", 20_000)]
    assert [
        (f["zpid"], f["previous"], f["current"]) for f in changes["status_changes"]
    ] == [("222", "House for sale", "Pending")]
    assert [g["zpid"] for g in changes["gone"]] == ["333"]
    assert changes["gone"][0]["address"] == "333 C St"
    assert changes["gone"][0]["price"] == 600_000


def test_a_home_long_gone_is_not_reported_gone_again_every_morning(sessions):
    _sweep(sessions, T1, [make_listing("111", price=500_000), make_listing("222", price=700_000)])
    _sweep(sessions, T2, [make_listing("111", price=500_000)])  # 222 leaves here
    _sweep(sessions, T3, [make_listing("111", price=500_000)])  # still away — not news

    with sessions() as s:
        changes = sweep_changes(s, WATCH)

    assert changes["gone"] == []


def test_a_change_is_measured_against_the_last_sighting_however_long_ago(sessions):
    _sweep(sessions, T1, [make_listing("111", price=500_000), make_listing("222", price=700_000)])
    _sweep(sessions, T2, [make_listing("222", price=700_000)])  # 111 absent this sweep
    _sweep(sessions, T3, [make_listing("111", price=465_000), make_listing("222", price=700_000)])

    with sessions() as s:
        changes = sweep_changes(s, WATCH)

    assert changes["new"] == []  # met before — absence is not a debut
    assert [(c["zpid"], c["delta"], c["since"]) for c in changes["cuts"]] == [
        ("111", -35_000, T1)
    ]


def test_cuts_and_rises_are_ordered_biggest_first(sessions):
    _sweep(
        sessions,
        T1,
        [
            make_listing("111", price=500_000),
            make_listing("222", price=500_000),
            make_listing("333", price=500_000),
        ],
    )
    _sweep(
        sessions,
        T2,
        [
            make_listing("111", price=490_000),  # -10,000
            make_listing("222", price=450_000),  # -50,000, the bigger cut
            make_listing("333", price=560_000),  # +60,000
        ],
    )

    with sessions() as s:
        changes = sweep_changes(s, WATCH)

    assert [c["zpid"] for c in changes["cuts"]] == ["222", "111"]
    assert [r["zpid"] for r in changes["rises"]] == ["333"]


def test_two_watches_keep_separate_change_histories_of_the_same_home(sessions):
    _sweep(sessions, T1, [make_listing("111", price=500_000)])
    _sweep(sessions, T2, [make_listing("111", price=480_000)])
    _sweep(sessions, T1, [make_listing("111", price=500_000)], watch="walsh-aledo-sold")

    with sessions() as s:
        sold_changes = sweep_changes(s, "walsh-aledo-sold")
        aledo_changes = sweep_changes(s, WATCH)

    assert sold_changes["history_began"] is False  # only one sweep of the sold watch
    assert aledo_changes["history_began"] is True
    assert [(c["zpid"], c["delta"]) for c in aledo_changes["cuts"]] == [("111", -20_000)]
