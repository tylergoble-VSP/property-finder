"""The score, its ledger, and the two things the score must never do.

It must never fail to add up — a card whose named components sum to something other than
its own number is a card that cannot be explained to the person acting on it. And it must
never be moved by the listing site's own published estimate, which is the mistake the
original tool made first and had to retire in public.
"""
from pathlib import Path

import pytest
from test_stats import home, priced, synthetic_market, two_pocket_market

from propertyfinder import deals
from propertyfinder.deals import (
    BASE_SCORE,
    OVERPRICED,
    build_deal_cards,
    condition_flags,
    deal_card,
    dom_percentiles,
    verdict_for,
)
from propertyfinder.stats import HedonicModel


@pytest.fixture(scope="module")
def market():
    """One fitted market, shared across the module — fitting is the slow part and nothing
    here mutates it.

    The scatter is set to 13%, roughly a real market's, because these tests are about how
    a price *reads*: on the near-noiseless surface `test_stats` uses for recovering
    coefficients, every discount worth having would be a statistical extreme."""
    solds = synthetic_market(noise=0.13)
    return HedonicModel.fit(solds), solds


# -- the ledger invariant ------------------------------------------------------------------


def test_the_ledger_always_sums_to_the_score(market):
    """The invariant, over every shape of card this module produces: fair homes, bargains,
    overpriced homes, stale ones, cut ones, and the extremes that clamp."""
    model, solds = market
    cases = [
        deal_card(home("fair", priced(3000), sqft=3000), model, solds),
        deal_card(home("cheap", priced(3000) * 0.80, sqft=3000), model, solds),
        deal_card(home("dear", priced(3000) * 1.30, sqft=3000), model, solds),
        deal_card(home("stale", priced(3000), sqft=3000), model, solds, dom_pctile=0.95),
        deal_card(home("cut", priced(3000), sqft=3000), model, solds, cut_pct=8.0),
        deal_card(
            home("everything", priced(3000) * 0.55, sqft=3000),
            model, solds, dom_pctile=0.99, cut_pct=15.0,
        ),
        deal_card(home("absurd", priced(3000) * 3.0, sqft=3000), model, solds),
    ]
    for card in cases:
        assert card is not None
        assert card.ledger_total() == pytest.approx(card.score, abs=0.05), card.zpid


def test_a_score_that_would_overflow_records_its_own_clamp(market):
    """Without the clamp line the ledger would add to 118 while the card showed 100 — an
    explanation that contradicts the number it explains."""
    model, solds = market
    card = deal_card(
        home("everything", priced(3000) * 0.55, sqft=3000),
        model, solds, dom_pctile=0.99, cut_pct=15.0,
    )

    assert card.score == 100.0
    clamp = [e for e in card.ledger if e.label.startswith("Clamped")]
    assert clamp and clamp[0].points < 0
    assert card.ledger_total() == pytest.approx(100.0)


def test_every_ledger_line_names_itself_and_explains_itself(market):
    model, solds = market
    card = deal_card(
        home("cheap", priced(3000) * 0.82, sqft=3000), model, solds, dom_pctile=0.95, cut_pct=6.0
    )

    labels = [e.label for e in card.ledger]
    assert labels[0] == "Starting point" and card.ledger[0].points == BASE_SCORE
    assert "Statistical value" in labels
    assert "Sitting on the market" in labels and "Price cut" in labels
    assert all(e.detail for e in card.ledger)  # no line arrives without its reason


def test_a_fair_home_scores_near_the_starting_point(market):
    model, solds = market
    card = deal_card(home("fair", priced(3000), sqft=3000), model, solds)

    assert abs(card.score - BASE_SCORE) < 8
    assert card.verdict in ("FAIR", "GOOD")


def test_a_genuine_bargain_scores_high_and_both_methods_say_so(market):
    model, solds = market
    card = deal_card(
        home("cheap", priced(3000) * 0.85, sqft=3000), model, solds, dom_pctile=0.95
    )

    assert card.verdict in ("GREAT", "GOOD")
    assert card.agree is True
    assert card.comp_ppsf is not None and card.comp_discount_pct > 3
    assert card.confidence == "HIGH"


def test_an_overpriced_home_says_so(market):
    model, solds = market
    card = deal_card(home("dear", priced(3000) * 1.35, sqft=3000), model, solds)

    assert card.verdict == OVERPRICED
    assert card.agree is False and card.expectation.discount_pct < 0


def test_the_verdict_bands_are_where_they_claim_to_be():
    assert [verdict_for(s) for s in (100, 75, 74.9, 60, 59.9, 45, 44.9, 0)] == [
        "GREAT", "GREAT", "GOOD", "GOOD", "FAIR", "FAIR", "OVERPRICED", "OVERPRICED",
    ]


# -- the ban on the site's own estimate ------------------------------------------------------


def test_the_published_estimate_cannot_move_a_score(market):
    """The listing site's estimate may be shown to a reader; it may never reach a score.
    The same home, told two wildly different estimates, must produce the same card."""
    model, solds = market
    low = deal_card(home("x", priced(3000), sqft=3000, estimate=1), model, solds)
    high = deal_card(home("x", priced(3000), sqft=3000, estimate=99_000_000), model, solds)
    absent = deal_card(home("x", priced(3000), sqft=3000), model, solds)

    assert low.score == high.score == absent.score
    assert low.ledger == high.ledger == absent.ledger
    assert low.expectation.expected == high.expectation.expected


def test_the_scoring_module_never_even_names_the_estimate_field():
    """A guard against the field creeping back in as a convenience. The proxy basis in
    stats.py reads it for *sold* comps, which is a different thing entirely — nothing in
    the scoring path may touch it, so the name does not appear here at all."""
    source = Path(deals.__file__).read_text().lower()
    assert "zestimate" not in source


# -- condition flags -------------------------------------------------------------------------


def test_distress_words_in_the_feeds_own_status_raise_a_flag():
    flags = condition_flags(
        {"status_text": "Foreclosure", "home_type": "SINGLE_FAMILY"},
        z=-0.5, dom_pctile=0.2, cut_pct=0.0,
    )
    assert flags == ["Foreclosure"]


def test_a_statistical_extreme_a_stale_listing_and_a_steep_cut_all_flag():
    flags = condition_flags(
        {"status_text": "House for sale", "home_type": "SINGLE_FAMILY"},
        z=-3.0, dom_pctile=0.95, cut_pct=12.0,
    )
    assert any("Statistical outlier" in f for f in flags)
    assert any(f.startswith("Stale") for f in flags)
    assert any(f.startswith("Steep cut") for f in flags)


def test_land_is_flagged_as_a_different_asset():
    flags = condition_flags({"home_type": "LOT"}, z=None, dom_pctile=None, cut_pct=0.0)
    assert flags == ["Land, not a house — priced a different way entirely"]


def test_an_ordinary_home_carries_no_flags():
    assert condition_flags(
        {"status_text": "House for sale", "home_type": "SINGLE_FAMILY"},
        z=-0.4, dom_pctile=0.3, cut_pct=0.0,
    ) == []


def test_a_statistical_extreme_lowers_confidence_rather_than_raising_the_score(market):
    """Half price is not a discount, it is a question. The card still scores well — the
    statistics say what they say — but it stops claiming to be sure, and both methods
    agreeing does not rescue it: they read the same asking price, and neither of them has
    been inside the house."""
    model, solds = market
    card = deal_card(home("deep", priced(2400) * 0.5, sqft=2400), model, solds)

    assert any(f.startswith("Statistical outlier") for f in card.flags)
    assert card.agree is True and card.score >= 75
    assert card.confidence == "LOW"


def test_a_lot_scored_by_a_model_fitted_on_houses_is_never_confident(market):
    model, solds = market
    card = deal_card(
        home("lot", priced(2400) * 0.7, sqft=2400, home_type="LOT"), model, solds
    )

    assert any(f.startswith("Land,") for f in card.flags)
    assert card.confidence == "LOW"


def test_a_market_that_discloses_no_prices_never_reaches_top_confidence():
    """Texas. The fit can be as tidy as it likes; the state still did not say what these
    homes sold for, and a card built on estimates must not claim otherwise."""
    solds = synthetic_market(disclosed=False, noise=0.13)
    model = HedonicModel.fit(solds)
    card = deal_card(home("cheap", priced(3000) * 0.85, sqft=3000), model, solds)

    assert model.basis == "proxy" and card.basis == "proxy"
    assert card.confidence == "MED"  # not HIGH, however many comps it found


# -- the whole market ------------------------------------------------------------------------


def test_cards_come_back_best_first(market):
    model, solds = market
    actives = [
        home("cheap", priced(3000) * 0.82, sqft=3000),
        home("mid", priced(2500), sqft=2500),
        home("rich", priced(2200) * 1.15, sqft=2200),
    ]
    cards = build_deal_cards(actives, solds, model)

    assert [c.zpid for c in cards] == ["cheap", "mid", "rich"]
    assert [c.score for c in cards] == sorted((c.score for c in cards), reverse=True)


def test_a_market_with_too_few_sales_produces_no_cards_at_all():
    """Not weaker cards. None — and the caller says why."""
    assert build_deal_cards([home("x", 500_000)], synthetic_market(n=5)) == []


def test_a_home_the_feed_barely_described_gets_no_card(market):
    model, solds = market
    assert build_deal_cards([home("blank", 500_000, sqft=None)], solds, model) == []


def test_days_on_market_percentiles_skip_homes_the_feed_never_dated():
    rows = [
        {"zpid": "a", "days_on_zillow": 5},
        {"zpid": "b", "days_on_zillow": 40},
        {"zpid": "c", "days_on_zillow": 120},
        {"zpid": "d"},
    ]
    pctiles = dom_percentiles(rows)

    assert set(pctiles) == {"a", "b", "c"}  # "d" is absent, not assumed fresh
    assert pctiles["a"] == 0.0 and pctiles["c"] == pytest.approx(2 / 3)
    assert dom_percentiles([{"zpid": "d"}]) == {}


def test_a_cut_recorded_by_the_store_reaches_the_ledger(market):
    model, solds = market
    cards = build_deal_cards(
        [home("cut", priced(3000), sqft=3000)],
        solds,
        model,
        cuts={"cut": {"cut_pct": 6.4}},
    )
    cut_lines = [e for e in cards[0].ledger if e.label == "Price cut"]

    assert cut_lines and cut_lines[0].points == 12.0
    assert "6%" in cut_lines[0].detail


def test_a_home_whose_ask_has_only_risen_gets_no_cut_credit(market):
    """`price_change_map` records a rise as a negative cut. That is not a motivated seller
    and must not be scored as one."""
    model, solds = market
    cards = build_deal_cards(
        [home("rose", priced(3000), sqft=3000)], solds, model, cuts={"rose": {"cut_pct": -4.0}}
    )
    assert not [e for e in cards[0].ledger if e.label == "Price cut"]


def test_location_appears_in_the_ledger_without_scoring_points():
    """The pocket adjustment moves the yardstick, not the score — and the ledger says
    exactly that, so a reader is not left wondering where the points went."""
    solds = two_pocket_market()
    model = HedonicModel.fit(solds)
    card = deal_card(home("x", 140 * 2400, sqft=2400, lat=27.60, lon=-82.20), model, solds)

    line = [e for e in card.ledger if e.label == "Location"]
    assert line and line[0].points == 0.0
    assert "folded into the expected price" in line[0].detail
    assert card.ledger_total() == pytest.approx(card.score, abs=0.05)
