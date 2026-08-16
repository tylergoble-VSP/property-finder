"""Storage, window queries, and the end-to-end payload/report — all offline."""
from __future__ import annotations

import re

from agentfinder.adapters import LuxeExtras
from agentfinder.attribution import CONFIRMED, INFERRED, UNRESOLVED, AgentAttribution
from agentfinder.config import LuxuryConfig
from agentfinder.reportdata import build_payload
from agentfinder.store import (
    current_attribution, record_attribution, record_luxury_sweep,
    zpids_needing_attribution,
)
from agentfinder.trec import TrecCheck
from conftest import make_listing

CFG = LuxuryConfig(name="walsh-luxury-25mi", center_address="c", lat=32.741913,
                   lon=-97.560241, radius_miles=25.0, price_floor=1_500_000,
                   queries=["Fort Worth, TX 76107"])
T1, T2 = "2026-08-16T10:00:00Z", "2026-08-17T10:00:00Z"


def _seed(sessions):
    rows = [
        (make_listing("A", price=5_000_000, days_on_zillow=120),
         LuxeExtras(is_showcase=1, has_3d_model=1), 3.0),
        (make_listing("B", price=2_500_000, days_on_zillow=10),
         LuxeExtras(builder_name="Toll Brothers"), 5.0),
        (make_listing("C", price=2_000_000), LuxeExtras(), 8.0),
    ]
    with sessions() as s:
        record_luxury_sweep(s, rows, CFG.name, T1)
        s.commit()


def test_sweep_stores_base_and_extras(sessions):
    _seed(sessions)
    with sessions() as s:
        needing = zpids_needing_attribution(s, CFG.name)
    assert set(needing) == {"A", "B", "C"}  # nothing attributed yet


def test_attribution_is_append_only_latest_wins(sessions):
    _seed(sessions)
    with sessions() as s:
        record_attribution(s, "A", CFG.name,
                           AgentAttribution("A", tier=UNRESOLVED, reason="no result"), None, T1)
        s.commit()
    with sessions() as s:
        record_attribution(s, "A", CFG.name,
                           AgentAttribution("A", agent="John Zimmerman", licence="0437098",
                                            tier=CONFIRMED, sources=("homes.com",)),
                           TrecCheck(found=True, status="Active", brokerage="Compass RE",
                                     name_match=True), T2)
        s.commit()
    with sessions() as s:
        cur = current_attribution(s, CFG.name)
    assert cur["A"]["tier"] == CONFIRMED           # the newer attempt wins
    assert cur["A"]["agent_name"] == "John Zimmerman"
    assert cur["A"]["brokerage"] == "Compass RE"   # backfilled from TREC


def test_resolved_home_drops_off_the_worklist(sessions):
    _seed(sessions)
    with sessions() as s:
        record_attribution(s, "A", CFG.name,
                           AgentAttribution("A", agent="X", tier=CONFIRMED), None, T2)
        s.commit()
    with sessions() as s:
        assert "A" not in zpids_needing_attribution(s, CFG.name)


def test_payload_and_report_render_self_contained(sessions):
    _seed(sessions)
    with sessions() as s:
        for zpid, agent, lic, tier in [("A", "John Zimmerman", "0437098", CONFIRMED),
                                       ("B", "John Zimmerman", "0437098", CONFIRMED),
                                       ("C", None, None, UNRESOLVED)]:
            record_attribution(s, zpid, CFG.name,
                               AgentAttribution(zpid, agent=agent, licence=lic, tier=tier,
                                                brokerage="Compass RE" if agent else None,
                                                sources=("homes.com",),
                                                reason=None if tier == CONFIRMED else "none"),
                               TrecCheck(found=True, status="Active", name_match=True) if lic
                               else None, T2)
        s.commit()
        payload = build_payload(s, CFG, T2)

    # John Zimmerman holds A+B -> a repeat specialist, ranked #1 by volume.
    assert payload["agents"][0]["name"] == "John Zimmerman"
    assert payload["agents"][0]["n_listings"] == 2
    assert payload["agents"][0]["specialist"] == "SPECIALIST"
    assert payload["concentration"]["unique_agents"] == 1
    assert payload["coverage"]["resolved_to_agent"] == 2
    assert payload["coverage"]["unresolved"] == 1

    from agentfinder.cli import _pagebuild_render
    html = _pagebuild_render(payload)
    assert "John Zimmerman" in html and "Luxury Listing Agents" in html
    # Self-contained except the deliberately-hotlinked photos: no external css/js/font.
    externals = re.findall(r'(?:<script[^>]+src|<link[^>]+href|@import)\s*=?\s*["\']?https?://', html)
    assert externals == []
