"""The new-construction payload, and the one seam it is shaped to keep visible.

The invariant this file exists for is the derived/curated boundary. Every top-level key is
recomputed from the database on every build except `curated`, which is read from
`data/walsh-newcon-curated.yaml` and which no build writes. The original tool kept both kinds
in one JSON file and refreshed it in place; builders appeared holding plans they no longer had
and the cheapest-plan column rendered `undefined` (docs/PORTING-THE-REPORTS.md, lesson 5).
A boundary that is asserted rather than intended is a boundary that survives a refactor.

The rest is the arithmetic a buyer report rests on: what is standing today and not ever, what
each builder is asking, and what the absorption figures mean given the window they were
measured over.
"""
import statistics

import pytest
from conftest import make_listing
from test_mapdata import WALSH_FINANCE, _config, _record, _watch

from propertyfinder.newconreport import (
    CURATED_KEY,
    UNRESOLVED_BUILDER,
    build_payload,
)

WATCH = _watch()
GENERATED = "2026-08-21T12:00:00Z"
T1, T2, T3 = "2026-07-11T10:00:00Z", "2026-08-01T10:00:00Z", "2026-08-21T10:00:00Z"


def _plan(zpid, address, price, sqft, **kw):
    return make_listing(
        zpid, address=address, price=price, sqft=sqft, status_text="New construction", **kw
    )


def _spec(zpid, address, price, sqft, dom=30, **kw):
    return make_listing(
        zpid,
        address=address,
        price=price,
        sqft=sqft,
        status_text="New construction",
        days_on_zillow=dom,
        **kw,
    )


def _resale(zpid, address, price, sqft, **kw):
    return make_listing(zpid, address=address, price=price, sqft=sqft, **kw)


# A price list wide enough for the ±20% band to have three plans in it at 3,000 feet, so the
# spec homes below are scored on a real comp rather than on a community fallback.
PRICE_LIST = [
    _plan(f"pl{i}", f"P{i} Plan, Walsh Ranch 60'", 600_000 + i * 20_000, 2800 + i * 100)
    for i in range(6)
]


def _payload(sessions, cfg=None, **kwargs):
    cfg = cfg or _config(WATCH, finance=WALSH_FINANCE)
    with sessions() as s:
        return build_payload(s, WATCH, cfg, GENERATED, **kwargs)


# -- the boundary ------------------------------------------------------------------------


def test_exactly_one_top_level_key_is_curated_and_the_rest_are_derived(sessions):
    """The sentence this module's docstring makes, asserted.

    A derived key is one the database can move. Proved by moving it: the payload is built
    twice over two different sweeps, and every derived block has to differ while the curated
    block is byte-identical. A key that appeared in neither category — or in both — would be
    a key nobody can say the provenance of, which is the state the original was in.
    """
    _record(sessions, [*PRICE_LIST, _spec("s1", "1 Oak Trail Dr", 700_000, 3000)], ts=T1)
    first = _payload(sessions)

    _record(sessions, [*PRICE_LIST, _spec("s1", "1 Oak Trail Dr", 640_000, 3000)], ts=T2)
    second = _payload(sessions)

    derived = set(first) - {CURATED_KEY}
    assert derived, "a payload of nothing but curated data is not a report"
    assert first[CURATED_KEY] == second[CURATED_KEY]
    moved = {k for k in derived if first[k] != second[k]}
    # Not every derived key has to move on every sweep — the watch block and the curated
    # file cannot — but the ones describing the market must, and none of them may be curated.
    assert {"market", "specs", "builders"} <= moved
    assert CURATED_KEY not in moved


def test_the_curated_block_is_the_file_and_nothing_else(sessions):
    from propertyfinder.dataquality import curated
    from propertyfinder.newconreport import CURATED_FILE

    payload = _payload(sessions)

    assert payload[CURATED_KEY] == dict(curated(CURATED_FILE))


def test_the_builder_roster_is_recomputed_and_never_carried_forward(sessions):
    """The exact block that rendered `undefined`: a builder holding a plan it dropped."""
    plans = [
        _plan("a1", "Alpha Plan, Walsh Gardens", 400_000, 2000),
        _plan("a2", "Beta Plan, Walsh Gardens", 450_000, 2200),
    ]
    _record(sessions, plans, ts=T1)
    _record(sessions, plans[:1], ts=T2)  # Beta leaves the sheet

    roster = _payload(sessions)["builders"]

    assert [b["n_plans"] for b in roster] == [1]
    assert roster[0]["plan_price_max"] == 400_000  # not Beta's 450,000


# -- today, not ever ---------------------------------------------------------------------


def test_a_home_that_left_the_market_is_not_on_the_page(sessions):
    _record(
        sessions,
        [*PRICE_LIST, _spec("gone", "9 Gone St", 500_000, 3000),
         _spec("here", "1 Here St", 500_000, 3000)],
        ts=T1,
    )
    _record(sessions, [*PRICE_LIST, _spec("here", "1 Here St", 500_000, 3000)], ts=T2)

    payload = _payload(sessions)

    assert [r["zpid"] for r in payload["specs"]] == ["here"]
    assert payload["market"]["n_specs"] == 1


def test_the_three_kinds_of_row_are_kept_apart(sessions):
    _record(
        sessions,
        [
            *PRICE_LIST,
            _spec("s1", "1 Oak Trail Dr", 700_000, 3000),
            _resale("r1", "2 Dunstan Dr", 780_000, 3600),
        ],
        ts=T1,
    )
    payload = _payload(sessions)

    assert {r["kind"] for r in payload["plans"]} == {"plan"}
    assert [r["zpid"] for r in payload["specs"]] == ["s1"]
    assert [r["zpid"] for r in payload["resale"]] == ["r1"]
    # A plan sheet is an offer, not a home, so it is never scored; a resale in a
    # no-disclosure market has nothing to be scored against.
    assert all(r["score"] is None for r in payload["plans"] + payload["resale"])
    assert payload["specs"][0]["score"] is not None


def test_a_duplicate_listing_is_dropped_from_every_table(sessions):
    _record(
        sessions,
        [
            *PRICE_LIST,
            _spec("keeper", "1820 Crested Ridge Rd, Aledo, TX 76008", 700_000, 3000),
            _spec("twin", "1820 Crested Rdg, Fort Worth, TX 76008", 700_000, 3000),
        ],
        ts=T1,
    )
    payload = _payload(sessions)

    assert len(payload["specs"]) == 1
    assert payload["market"]["n_duplicates_dropped"] == 1


# -- the window, and the rates measured over it ------------------------------------------


def test_a_rate_carries_the_window_it_was_measured_over(sessions):
    """Three sweeps, forty-one days, one home absorbed out of three standing.

    The number on its own is meaningless and the original shipped it that way: absorption
    across the last two sweeps found one home in a fortnight and put months-of-supply at 18.2
    on a sample of one. Whatever window a page uses, the page has to say which (lesson 7).
    """
    standing = [
        _spec("s1", "1 Oak Trail Dr", 600_000, 3000),
        _spec("s2", "2 Oak Trail Dr", 620_000, 3000),
        _spec("sold", "3 Oak Trail Dr", 640_000, 3000),
    ]
    _record(sessions, [*PRICE_LIST, *standing], ts=T1)
    _record(sessions, [*PRICE_LIST, *standing], ts=T2)
    _record(sessions, [*PRICE_LIST, *standing[:2]], ts=T3)

    window = _payload(sessions)["market"]["window"]

    assert window["n_sweeps"] == 3
    assert (window["window_from"], window["window_to"]) == (T1, T3)
    assert window["window_days"] == 41
    assert window["inventory_start"] == 3 and window["inventory_end"] == 2
    assert window["absorbed"] == 1
    # 1 home / 41 days × 30 = 0.73 a month; 2 standing ÷ 0.73 = 2.7 months of supply.
    assert window["months_supply"] == 2.7


def test_no_key_is_named_for_the_value_it_held_one_day(sessions):
    """`absorbed_26d` outlived the 26-day window because renaming it meant touching the
    template. Payload keys are named for what they are."""
    window = _payload(sessions)["market"]["window"]

    assert "absorbed" in window
    assert not [k for k in window if any(c.isdigit() for c in k)]


def test_a_market_watched_once_says_so_rather_than_inventing_a_rate(sessions):
    """One sweep is a photograph, not a history. There is no honest rate to print."""
    _record(sessions, [*PRICE_LIST, _spec("s1", "1 Oak Trail Dr", 600_000, 3000)], ts=T1)

    window = _payload(sessions)["market"]["window"]

    assert window["n_sweeps"] == 1 and window["window_days"] == 0
    assert window["absorbed"] == 0
    assert window["months_supply"] is None


def test_an_empty_watch_produces_a_whole_payload_with_honest_holes(sessions):
    payload = _payload(sessions)

    assert payload["sweep_ts"] is None
    assert payload["plans"] == [] and payload["specs"] == [] and payload["resale"] == []
    assert payload["market"]["window"]["n_sweeps"] == 0
    assert payload[CURATED_KEY]  # the research is still the research


# -- the movement ledger -----------------------------------------------------------------


def test_cuts_are_cumulative_against_the_first_ask_ever_recorded(sessions):
    _record(sessions, [*PRICE_LIST, _spec("s1", "1 Oak Trail Dr", 700_000, 3000)], ts=T1)
    _record(sessions, [*PRICE_LIST, _spec("s1", "1 Oak Trail Dr", 660_000, 3000)], ts=T2)
    _record(sessions, [*PRICE_LIST, _spec("s1", "1 Oak Trail Dr", 640_000, 3000)], ts=T3)

    market = _payload(sessions)["market"]
    spec = _payload(sessions)["specs"][0]

    assert market["n_cuts"] == 1 and market["total_cut_dollars"] == 60_000
    assert market["deepest_cut_dollars"] == 60_000
    assert spec["first_price"] == 700_000 and spec["price_cut_dollars"] == 60_000
    assert spec["n_observations"] == 3


def test_a_stale_spec_is_counted_against_the_threshold_the_page_states(sessions):
    _record(
        sessions,
        [
            *PRICE_LIST,
            _spec("fresh", "1 Oak Trail Dr", 600_000, 3000, dom=30),
            _spec("stale", "2 Oak Trail Dr", 600_000, 3000, dom=140),
        ],
        ts=T1,
    )
    market = _payload(sessions)["market"]

    assert market["specs_over_stale_days"] == 1
    assert market["stale_spec_days"] == 90  # in the payload, so the sentence cannot drift


# -- attribution, medians, money ---------------------------------------------------------


def test_the_researched_roster_reaches_the_page(sessions):
    """A real Walsh plan address, attributed from the file rather than from a guess."""
    _record(sessions, [_plan("p1", "Plan 216 Plan, Walsh", 800_000, 3200)], ts=T1)

    plan = _payload(sessions)["plans"][0]

    assert plan["builder"] == "Highland Homes"
    assert plan["builder_tier"] == "RESEARCHED"


def test_a_home_nobody_can_attribute_is_a_bucket_and_sorts_last(sessions):
    _record(
        sessions,
        [
            _plan("p1", "Plan 216 Plan, Walsh", 800_000, 3200),
            _spec("s9", "999 Nowhere Ln, Aledo, TX 76008", 600_000, 3000),
        ],
        ts=T1,
    )
    roster = _payload(sessions)["builders"]

    assert roster[-1]["builder"] == UNRESOLVED_BUILDER
    assert roster[-1]["unresolved"] is True


def test_medians_are_computed_over_what_is_actually_known(sessions):
    """A home with no footage has no price per foot, and does not get a made-up one."""
    _record(
        sessions,
        [
            *PRICE_LIST,
            _spec("known", "1 Oak Trail Dr", 600_000, 3000),
            _spec("sizeless", "2 Oak Trail Dr", 700_000, None),
        ],
        ts=T1,
    )
    payload = _payload(sessions)

    sizeless = next(r for r in payload["specs"] if r["zpid"] == "sizeless")
    assert sizeless["price_per_sqft"] is None
    assert payload["market"]["spec_median_ppsf"] == pytest.approx(200.0)
    assert payload["market"]["spec_median_price"] == statistics.median([600_000, 700_000])


def test_the_carry_column_states_the_assumptions_it_rests_on(sessions):
    _record(sessions, [*PRICE_LIST, _spec("s1", "1 Oak Trail Dr", 600_000, 3000)], ts=T1)

    payload = _payload(sessions)

    assert payload["finance"]["special_assessment"]["flat_annual"] == 3271.0
    assert payload["finance"]["tax_rate_citation"]
    carry = payload["specs"][0]["carry"]
    assert carry["assessment_basis"] == "flat"
    assert carry["total"] > carry["principal_interest"]


def test_a_report_with_no_finance_block_simply_has_no_monthly_column(sessions):
    from propertyfinder.config import WatchConfig

    _record(sessions, [*PRICE_LIST, _spec("s1", "1 Oak Trail Dr", 600_000, 3000)], ts=T1)
    payload = _payload(sessions, cfg=WatchConfig(watches=[WATCH]))

    # The model's own market-neutral defaults, not a household's — and no assessment.
    assert payload["finance"]["special_assessment"]["flat_annual"] is None
    assert payload["specs"][0]["carry"]["assessment"] == 0


def test_an_explicit_finance_block_overrides_the_watch(sessions):
    """What `report --public` leans on: neutral assumptions, not a hand-edited config."""
    from propertyfinder.config import FinanceAssumptions

    _record(sessions, [*PRICE_LIST, _spec("s1", "1 Oak Trail Dr", 600_000, 3000)], ts=T1)
    payload = _payload(sessions, finance=FinanceAssumptions())

    assert payload["finance"]["special_assessment"]["flat_annual"] is None
    assert payload["finance"]["tax_rate_citation"] == ""


def test_the_ask_curve_is_the_price_list_and_nothing_but(sessions):
    _record(
        sessions,
        [*PRICE_LIST, _spec("s1", "1 Oak Trail Dr", 600_000, 3000)],
        ts=T1,
    )
    curve = _payload(sessions)["askcurve"]

    assert curve["n_plans"] == len(PRICE_LIST)
    assert all("Plan," in p["plan"] for p in curve["points"])
    assert [c["community"] for c in curve["communities"]] == ["Walsh Ranch 60'"]
