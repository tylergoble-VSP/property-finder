"""digest.build_digest: a pure summary over whatever the database and config already say.

No mail config appears anywhere below — that is the point of separating `digest.py` from
`notify.py`. Every scenario is built by writing directly to the store (or, where a real
sold-comps fit is the thing under test, by running an actual sweep against a fake
transport) and then asking `build_digest` what it has to say about it.
"""
from __future__ import annotations

import httpx

from conftest import RoutedSearchApi, make_listing

from propertyfinder import sweep as sweep_mod
from propertyfinder.adapters import ZillowAdapter
from propertyfinder.config import Watch, WatchConfig
from propertyfinder.digest import build_digest
from propertyfinder.store import record_snapshot, upsert_property

NOW = "2026-08-14T10:00:00Z"


def _watch(name: str, status: str = "for_sale", query: str = "Aledo, TX 76008") -> Watch:
    return Watch(
        name=name,
        center_address="2112 Eastus Ln, Aledo, TX 76008",
        lat=32.73665,
        lon=-97.55626,
        radius_miles=2.0,
        listing_status=status,
        queries=[query],
    )


# -- movement, with no sold companion at all -------------------------------------------


def test_first_sweep_reads_as_history_beginning_today(sessions):
    cfg = WatchConfig(watches=[_watch("solo")])
    with sessions() as session:
        listing = make_listing("111", price=500_000.0)
        upsert_property(session, listing, NOW)
        session.flush()
        record_snapshot(session, listing, "solo", NOW)
        session.commit()

        subject, body = build_digest(session, cfg, NOW)

    assert "0 deals" in subject
    assert "== solo · 1 active ==" in body
    assert "History begins today — this is the first sweep on record." in body
    assert "not scored" not in body  # no companion configured, so nothing claims to skip one


def test_movement_and_top_cuts_read_off_the_last_two_sweeps(sessions):
    cfg = WatchConfig(watches=[_watch("solo")])
    with sessions() as session:
        first_a, first_b = make_listing("111", price=500_000.0), make_listing("222", price=700_000.0)
        for listing in (first_a, first_b):
            upsert_property(session, listing, "2026-08-13T10:00:00Z")
        session.flush()
        for listing in (first_a, first_b):
            record_snapshot(session, listing, "solo", "2026-08-13T10:00:00Z")
        session.commit()

        # 111 comes back cheaper; 222 does not come back at all, so it reads as "gone".
        cheaper = make_listing("111", price=465_000.0)
        upsert_property(session, cheaper, NOW)
        session.flush()
        record_snapshot(session, cheaper, "solo", NOW)
        session.commit()

        _, body = build_digest(session, cfg, NOW)

    assert (
        "since last sweep: 0 new · 1 cuts · 0 raised · 0 status changes · 1 gone" in body
    )
    assert (
        "down  111 Tolleson Dr, Aledo, TX 76008: $500,000 -> $465,000 "
        "(-35,000 since 2026-08-13)" in body
    )


# -- deals, which require a sold companion -----------------------------------------------


def test_a_watch_with_no_sold_companion_gets_no_deals_section(sessions):
    cfg = WatchConfig(watches=[_watch("solo")])
    with sessions() as session:
        listing = make_listing("111", price=500_000.0)
        upsert_property(session, listing, NOW)
        session.flush()
        record_snapshot(session, listing, "solo", NOW)
        session.commit()

        _, body = build_digest(session, cfg, NOW)

    # No "deals" line and no "not scored" line — a watch that never named a companion has
    # nothing to say about scoring at all, which is different from a companion that tried
    # and came up short.
    assert "deals (" not in body
    assert "not scored" not in body


def test_a_thin_sold_companion_reads_as_not_scored_with_its_own_reason(sessions):
    cfg = WatchConfig(watches=[_watch("solo"), _watch("solo-sold", status="sold")])
    with sessions() as session:
        active = make_listing("111", price=500_000.0)
        sold = make_listing("999", price=480_000.0, listing_status="sold", status_text="Sold")
        upsert_property(session, active, NOW)
        upsert_property(session, sold, NOW)
        session.flush()
        record_snapshot(session, active, "solo", NOW)
        record_snapshot(session, sold, "solo-sold", NOW, listing_status="sold")
        session.commit()

        _, body = build_digest(session, cfg, NOW)

    assert "not scored: solo-sold holds too few usable sales to fit a model" in body
    assert "deals (" not in body


def _row(zpid: str, price: float, sqft: int = 3000, baths: int = 3,
         lat: float = 32.7400, lon: float = -97.5600, status_text: str = "House for sale") -> dict:
    """The raw shape the search engine returns, for the one test that needs a real fit.

    Same fixture idiom `test_cli.py` uses (`_row`/`_market`/`RoutedSearchApi`) — a digest
    test that wants an actual `HedonicModel` behind it has to feed the adapter enough varied
    comps to fit one, and hand-building `Listing`s would skip the exact parsing path a real
    sweep uses.
    """
    return {
        "zpid": zpid,
        "address": f"{zpid} Walsh Ave, Aledo, TX 76008",
        "extracted_price": price,
        "beds": 4,
        "baths": baths,
        "sqft": sqft,
        "home_type": "SINGLE_FAMILY",
        "home_status": "FOR_SALE",
        "status_text": status_text,
        "latitude": lat,
        "longitude": lon,
    }


def _market(*rows: dict) -> dict:
    return {"properties": list(rows), "pagination": {"current_page": 1, "total_pages": 1}}


def _sold_market(n: int = 26) -> dict:
    """Sold comps varied enough in size and price for a real hedonic fit — MIN_COMPS is 20."""
    rows = []
    for i in range(n):
        sqft = 1800 + (i % 13) * 150
        price = 200 * 2400 * (sqft / 2400) ** 0.83 * (1 + 0.05 * ((i % 5) - 2) / 2)
        rows.append(
            _row(
                f"h{i}", round(price), sqft=sqft, baths=2 + (i % 3),
                lat=32.7360 + (i % 7) * 0.001, lon=-97.5560 + (i % 5) * 0.001,
                status_text="Sold",
            )
        )
    return _market(*rows)


FORSALE_QUERY = "Aledo, TX 76008"
SOLD_QUERY = "Aledo, TX 76008 (sold-comps query)"


def test_a_scored_deal_shows_up_ranked_and_counted_in_the_subject(sessions):
    cfg = WatchConfig(watches=[
        _watch("solo", "for_sale", FORSALE_QUERY),
        _watch("solo-sold", "sold", SOLD_QUERY),
    ])
    transport = RoutedSearchApi({
        # Priced at roughly a quarter of what the fitted curve expects a 2400-sqft home to
        # fetch here — comfortably past the two-standard-deviation cap, so the verdict does
        # not depend on the exact shape of the synthetic market.
        FORSALE_QUERY: _market(_row("bargain", 120_000, sqft=2400, baths=2)),
        SOLD_QUERY: _sold_market(),
    })
    adapter = ZillowAdapter(
        "test-key", client=httpx.Client(transport=transport), sleep=lambda _s: None
    )
    with sessions() as session:
        sweep_mod.run_sweep(session, adapter, cfg.watch("solo"), now=NOW)
        sweep_mod.run_sweep(session, adapter, cfg.watch("solo-sold"), now=NOW)

        subject, body = build_digest(session, cfg, NOW)

    assert "1 deal" in subject and "1 deals" not in subject
    assert "deals (1, valued against solo-sold):" in body
    assert "[GREAT]" in body
    assert "bargain Walsh Ave, Aledo, TX 76008" in body
    assert "$120,000" in body
