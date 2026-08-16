"""TREC verification, design-fit ledger, specialization, and migration isolation."""
from __future__ import annotations

from propertyfinder.domain import Base

from agentfinder.attribution import CONFIRMED, INFERRED
from agentfinder.designfit import BASE, score_fit
from agentfinder.migrations import ANNEX_FLOOR, discover
from agentfinder.specialization import concentration, rank_agents
from agentfinder.trec import verify
from conftest import trec_client


# -- TREC -----------------------------------------------------------------------------
def test_trec_verifies_active_name_match():
    client = trec_client({"549218": [{"license_number": "549218-SA",
                                      "license_full_name": "KELLY MARCONTELL",
                                      "license_status": "Active",
                                      "related_license_full_name": "EBBY HALLIDAY"}]})
    chk = verify(client, "0549218", "Kelly Marcontell")
    assert chk.found and chk.verified and chk.brokerage == "EBBY HALLIDAY"


def test_trec_name_diff_is_not_verified():
    client = trec_client({"549218": [{"license_number": "549218-SA",
                                      "license_full_name": "SOMEONE ELSE",
                                      "license_status": "Active"}]})
    chk = verify(client, "0549218", "Kelly Marcontell")
    assert chk.found and not chk.verified and not chk.name_match


def test_trec_no_record():
    assert not verify(trec_client({}), "0999999", "Nobody").found


# -- design fit -----------------------------------------------------------------------
def test_ledger_sums_to_score_and_absence_scores_nothing():
    bare = score_fit({"zpid": "x", "price": 2_000_000, "days_on_zillow": 5}, {}, 1_500_000)
    assert bare.total() == bare.score
    # base + the luxury-ask line (+4 for >=floor, <2x floor); showcase/3d/builder unknown -> 0
    assert bare.score == BASE + 4

    rich = score_fit({"zpid": "y", "price": 5_000_000, "days_on_zillow": 120},
                     {"is_showcase": 1, "has_3d_model": 1}, 1_500_000)
    assert rich.total() == rich.score and rich.score > bare.score
    assert rich.verdict == "STRONG"


def test_showcase_only_counts_when_true_not_unknown():
    unknown = score_fit({"zpid": "z", "price": 2_000_000}, {"is_showcase": None}, 1_500_000)
    assert all("Showcase" not in l.label for l in unknown.ledger)


# -- specialization -------------------------------------------------------------------
def test_ranking_and_concentration():
    current = {
        "A": {"zpid": "A", "tier": CONFIRMED, "agent_key": "john|zimmerman",
              "agent_name": "John Zimmerman", "brokerage": "Compass", "licence": "0437098",
              "phone": None, "trec_status": "Active"},
        "B": {"zpid": "B", "tier": CONFIRMED, "agent_key": "john|zimmerman",
              "agent_name": "John Zimmerman", "brokerage": "Compass", "licence": "0437098",
              "phone": None, "trec_status": "Active"},
        "C": {"zpid": "C", "tier": INFERRED, "agent_key": "amy|trott",
              "agent_name": "Amy Trott", "brokerage": "Ebby", "licence": None,
              "phone": None, "trec_status": None},
    }
    snaps = [{"zpid": "A", "price": 5_000_000}, {"zpid": "B", "price": 3_000_000},
             {"zpid": "C", "price": 2_000_000}]
    ranked = rank_agents(current, snaps)
    assert ranked[0].name == "John Zimmerman" and ranked[0].n_listings == 2
    assert ranked[0].specialist == "SPECIALIST" and ranked[1].specialist == "ACTIVE"
    conc = concentration(ranked, total_volume=10_000_000)
    assert conc["unique_agents"] == 2
    assert conc["attributed_volume"] == 10_000_000
    assert conc["repeat_specialists"] == 1


# -- migration isolation --------------------------------------------------------------
def test_annex_migrations_are_in_the_reserved_range():
    for m in discover():
        assert m.version >= ANNEX_FLOOR


def test_annex_tables_are_not_on_cores_declarative_base():
    # If annex tables were mapped onto core's Base, core's create_all would make them on a
    # core-only install — the leak in the other direction. They must be absent.
    core_tables = set(Base.metadata.tables)
    assert "agents" not in core_tables
    assert "listing_attributions" not in core_tables
    assert "listing_extras" not in core_tables
