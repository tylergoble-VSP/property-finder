"""Detail-engine enrichment: what it extracts, who it asks, and how it fails.

`enrich_watch` never touches the network itself — `make_adapter` hands it the same fake
transport the rest of the suite uses — but it does touch the two things that make
enrichment a batch job rather than a loop: the attempt stamp, and the budget.
"""
from __future__ import annotations

import pytest
from conftest import FakeSearchApi, make_listing

from propertyfinder.adapters import PropertyDetail
from propertyfinder.budget import CallBudget
from propertyfinder.config import Watch
from propertyfinder.domain import WatchedProperty
from propertyfinder.enrich import enrich_watch, extract_detail
from propertyfinder.store import record_snapshot, upsert_property

CENTER = {"lat": 32.73665, "lon": -97.55626}
NOW = "2026-08-01T10:00:00Z"


def _watch(name: str = "walsh-aledo") -> Watch:
    return Watch(
        name=name,
        center_address="2112 Eastus Ln, Aledo, TX 76008",
        radius_miles=2.0,
        queries=["Aledo, TX 76008"],
        **CENTER,
    )


def _seed(session, watch_name: str, *zpids: str) -> None:
    """Put zpids into the latest sweep of a watch, without running a live sweep."""
    for zpid in zpids:
        listing = make_listing(zpid=zpid)
        upsert_property(session, listing, NOW)
        session.flush()
        record_snapshot(session, listing, watch_name, NOW)
    session.commit()


def _detail(prop: dict) -> PropertyDetail:
    return PropertyDetail(zpid="1", raw={"property": prop})


# -- extract_detail: reading the body, whichever depth it hides at ---------------------


def test_extract_reads_top_level_fields():
    v = extract_detail(
        _detail(
            {
                "year_built": 2022,
                "lot_size": 7666,
                "lot_size_units": "square feet",
                "monthly_hoa_fee": 243,
                "property_tax_rate": 1.46,
            }
        )
    )
    assert v == {"year_built": 2022, "lot_sqft": 7666.0, "hoa_monthly": 243.0, "tax_rate": 1.46}


def test_extract_falls_back_to_facts_and_features():
    v = extract_detail(
        _detail(
            {
                "facts_and_features": {
                    "year_built": 2019,
                    "lot_size": "6,054 sqft",
                    "hoa_fee": "$233 monthly",
                }
            }
        )
    )
    assert v["year_built"] == 2019
    assert v["lot_sqft"] == 6054.0  # comma and unit stripped
    assert v["hoa_monthly"] == 233.0


def test_extract_converts_acre_lots_to_square_feet():
    v = extract_detail(_detail({"year_built": 2015, "lot_size": 0.5, "lot_size_units": "Acres"}))
    assert v["lot_sqft"] == 0.5 * 43560


def test_extract_leaves_a_mislabelled_large_lot_alone():
    # Observed on a real detail pull: 12,624 tagged "Acres" is square feet mislabelled,
    # not twelve thousand real acres of back yard.
    v = extract_detail(_detail({"lot_size": 12624, "lot_size_units": "Acres"}))
    assert v["lot_sqft"] == 12624


def test_extract_handles_a_body_with_nothing_in_it():
    v = extract_detail(PropertyDetail(zpid="1", raw={"error": "no data"}))
    assert v == {"year_built": None, "lot_sqft": None, "hoa_monthly": None, "tax_rate": None}


def test_extract_from_the_golden_fixture(make_adapter):
    detail = make_adapter().property("29584711")
    assert extract_detail(detail) == {
        "year_built": 2021,
        "lot_sqft": 8712.0,
        "hoa_monthly": 92.0,
        "tax_rate": 2.34,
    }


# -- enrich_watch: attempts, stamps, and coverage over time -----------------------------


def test_a_home_never_enriched_is_attempted_and_filled(sessions, make_adapter):
    with sessions() as session:
        _seed(session, "walsh-aledo", "29584711")
        summary = enrich_watch(session, make_adapter(), _watch(), limit=10)

        assert summary == {
            "watch": "walsh-aledo",
            "attempted": 1,
            "ok": 1,
            "miss": 0,
            "fields_filled": 4,
            "stopped_by_budget": False,
        }
        row = session.get(WatchedProperty, "29584711")
        assert row.year_built == 2021 and row.lot_sqft == 8712.0
        assert row.hoa_monthly == 92.0 and row.tax_rate == 2.34
        assert row.enriched_ts is not None


def test_a_watch_with_no_sweep_yet_enriches_nothing(sessions, make_adapter):
    with sessions() as session:
        adapter = make_adapter()
        summary = enrich_watch(session, adapter, _watch(), limit=10)
        assert summary["attempted"] == 0
        assert adapter.request_count == 0


def test_limit_bounds_how_many_homes_one_run_touches(sessions, make_adapter):
    with sessions() as session:
        _seed(session, "walsh-aledo", "111", "222", "333")
        adapter = make_adapter()
        summary = enrich_watch(session, adapter, _watch(), limit=2)
        assert summary["attempted"] == 2
        assert adapter.request_count == 2


def test_never_enriched_homes_are_preferred_over_ones_merely_stale(sessions, make_adapter):
    """Coverage fills in gradually. A home tried years ago and a home never tried at all
    are both eligible, but the one nobody has ever asked about goes first."""
    with sessions() as session:
        _seed(session, "walsh-aledo", "111", "29584711")
        stale = session.get(WatchedProperty, "111")
        stale.enriched_ts = "2020-01-01T00:00:00Z"
        session.commit()

        summary = enrich_watch(session, make_adapter(), _watch(), limit=1)

        assert summary["attempted"] == 1
        assert session.get(WatchedProperty, "29584711").enriched_ts is not None
        assert session.get(WatchedProperty, "111").enriched_ts == "2020-01-01T00:00:00Z"


def test_a_stale_enrichment_is_retried_after_the_window(sessions, make_adapter):
    with sessions() as session:
        _seed(session, "walsh-aledo", "88291043")
        old = session.get(WatchedProperty, "88291043")
        old.enriched_ts = "2020-01-01T00:00:00Z"
        session.commit()

        summary = enrich_watch(session, make_adapter(), _watch(), limit=10, stale_days=30)
        assert summary["attempted"] == 1


# -- the flaky fifth: a failure consumes its attempt and does not get re-hammered ------


def test_a_failing_pull_consumes_its_attempt_and_is_not_retried_in_the_same_run(
    sessions, make_adapter
):
    """88291043 is the detail engine's ordinary failure: HTTP 200, "Success", nothing in
    it. One pull in five looks like this, and it must count as tried."""
    with sessions() as session:
        _seed(session, "walsh-aledo", "88291043")
        adapter = make_adapter()

        first = enrich_watch(session, adapter, _watch(), limit=10)
        assert first == {
            "watch": "walsh-aledo",
            "attempted": 1,
            "ok": 0,
            "miss": 1,
            "fields_filled": 0,
            "stopped_by_budget": False,
        }
        stamped = session.get(WatchedProperty, "88291043").enriched_ts
        assert stamped is not None

        second = enrich_watch(session, adapter, _watch(), limit=10)
        assert second["attempted"] == 0  # too fresh a miss to ask again
        assert adapter.request_count == 1  # the second run never sent anything
        assert session.get(WatchedProperty, "88291043").enriched_ts == stamped


# -- the budget: a batch, not an all-or-nothing transaction -----------------------------


def test_budget_exhaustion_mid_batch_commits_what_it_had(sessions, make_adapter):
    with sessions() as session:
        _seed(session, "walsh-aledo", "111", "222")
        transport = FakeSearchApi(details={"111": "property_detail", "222": "property_detail"})
        adapter = make_adapter(transport, budget=CallBudget(max_calls=1))

        summary = enrich_watch(session, adapter, _watch(), limit=10)

        assert summary["attempted"] == 1 and summary["ok"] == 1
        assert summary["stopped_by_budget"] is True
        assert adapter.request_count == 1

        done = session.get(WatchedProperty, "111")
        pending = session.get(WatchedProperty, "222")
        assert done.enriched_ts is not None and done.year_built == 2021
        assert pending.enriched_ts is None  # never sent, never stamped — a real retry


@pytest.mark.parametrize("limit", [0])
def test_a_limit_of_zero_asks_for_nothing(sessions, make_adapter, limit):
    with sessions() as session:
        _seed(session, "walsh-aledo", "29584711")
        adapter = make_adapter()
        summary = enrich_watch(session, adapter, _watch(), limit=limit)
        assert summary["attempted"] == 0
        assert adapter.request_count == 0
