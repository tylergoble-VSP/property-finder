"""The monthly carry, and the one modelling flaw the whole rebuild was called for.

The original expressed a special assessment as a percentage of value because that is how
an ad-valorem tax works. The Walsh improvement district bills a flat dollar amount per lot
instead, and forcing that fact through a percentage field mispriced early-phase homes by
about $200 a month. Most of what follows is that fix, held to arithmetic: both forms must
compute, they must differ by exactly what the two bills differ by, and a block claiming to
be both at once must refuse to load rather than pick one.
"""
import math

import pytest
from pydantic import ValidationError

from propertyfinder.costmodel import (
    FinanceAssumptions,
    SpecialAssessment,
    _round,
    monthly_cost,
    rent_metrics,
)

# The Walsh numbers, verified against adopted rates on 2026-08-06 (docs/REBUILD.md
# appendix): the ad-valorem stack, and the district's new-70-foot-lot bill.
WALSH = FinanceAssumptions(
    mortgage_rate=6.5,
    down_pct=20.0,
    loan_term_years=30,
    insurance_annual_per_1000=5.0,
    default_tax_rate=2.339427,
    special_assessment=SpecialAssessment(flat_annual=3271.0),
)


def test_monthly_cost_itemises_and_the_lines_sum_to_the_total():
    fin = FinanceAssumptions(
        mortgage_rate=6.0,
        down_pct=20.0,
        loan_term_years=30,
        insurance_annual_per_1000=4.0,
        default_tax_rate=1.8,
    )
    cost = monthly_cost(500_000, tax_rate=1.9, hoa_monthly=100, fin=fin)

    assert cost.tax == _round(500_000 * 0.019 / 12)
    assert cost.insurance == _round(500 * 4 / 12)
    assert cost.dues == 100
    assert cost.assessment == 0
    assert 2350 < cost.principal_interest < 2450  # $400k at 6% over 30 years is ~$2,398
    assert cost.total == (
        cost.principal_interest + cost.tax + cost.insurance + cost.dues + cost.assessment
    )


def test_a_homes_own_tax_rate_beats_the_market_default_and_says_which():
    fin = FinanceAssumptions(default_tax_rate=1.8)
    own = monthly_cost(500_000, tax_rate=2.4, hoa_monthly=0, fin=fin)
    fallback = monthly_cost(500_000, tax_rate=None, hoa_monthly=0, fin=fin)

    assert (own.tax_rate_used, own.tax_basis) == (2.4, "home")
    assert (fallback.tax_rate_used, fallback.tax_basis) == (1.8, "default")


# -- the fix: percent OR flat dollars --------------------------------------------------


def test_a_percentage_assessment_scales_with_price():
    fin = FinanceAssumptions(
        default_tax_rate=2.0, special_assessment=SpecialAssessment(pct=0.35)
    )
    small = monthly_cost(400_000, None, 0, fin)
    large = monthly_cost(800_000, None, 0, fin)

    assert small.assessment_basis == "percent"
    assert small.assessment_annual == _round(400_000 * 0.0035)  # $1,400
    assert large.assessment_annual == 2 * small.assessment_annual  # doubles with the price


def test_a_flat_assessment_is_the_same_bill_at_any_price():
    fin = FinanceAssumptions(
        default_tax_rate=2.0, special_assessment=SpecialAssessment(flat_annual=3271.0)
    )
    small = monthly_cost(400_000, None, 0, fin)
    large = monthly_cost(800_000, None, 0, fin)

    assert small.assessment_basis == "flat"
    assert small.assessment_annual == large.assessment_annual == 3271
    assert small.assessment == large.assessment == _round(3271 / 12)  # $273/month


def test_the_two_forms_differ_by_exactly_what_the_two_bills_differ_by():
    """A $700,000 home under a 0.35% district and under a $3,271 flat one. This is the
    comparison the original could not make at all: it had only the percentage field, so
    the flat bill had to be guessed at as a rate, and the guess was wrong for every home
    priced away from whatever price the guess was calibrated at."""
    as_pct = monthly_cost(
        700_000, None, 0, FinanceAssumptions(special_assessment=SpecialAssessment(pct=0.35))
    )
    as_flat = monthly_cost(
        700_000,
        None,
        0,
        FinanceAssumptions(special_assessment=SpecialAssessment(flat_annual=3271.0)),
    )

    assert as_pct.assessment_annual == 2450  # 0.35% of $700,000
    assert as_flat.assessment_annual == 3271  # the district's actual bill
    assert as_flat.assessment_annual - as_pct.assessment_annual == 821
    assert as_pct.assessment == 204 and as_flat.assessment == 273  # $821/yr is $68.42/mo

    # Nothing else about the two homes differs, so the totals carry the same gap. They
    # differ by 68 rather than 69 because each total is rounded to the dollar once, from
    # its own un-rounded sum — the lines are the itemisation, not the addends.
    for line in ("principal_interest", "tax", "insurance", "dues"):
        assert getattr(as_pct, line) == getattr(as_flat, line)
    assert as_flat.total - as_pct.total == 68


def test_the_all_in_rate_is_derived_from_the_dollars_not_assumed():
    """The flat district's share of value falls as price rises — which is precisely why a
    single "all-in percentage" cannot express it, and why this figure is computed per home
    rather than configured."""
    cheap = monthly_cost(500_000, None, 0, WALSH)
    dear = monthly_cost(1_000_000, None, 0, WALSH)

    assert cheap.tax_rate_used == dear.tax_rate_used == 2.339427
    assert cheap.all_in_tax_rate == pytest.approx(2.339427 + 3271 / 500_000 * 100, abs=1e-4)
    assert dear.all_in_tax_rate < cheap.all_in_tax_rate


def test_an_assessment_claiming_both_forms_refuses_to_load():
    with pytest.raises(ValidationError) as exc:
        SpecialAssessment(pct=0.35, flat_annual=3271.0)
    assert "never both" in str(exc.value)


def test_an_assessment_claiming_neither_is_the_ordinary_no_district_case():
    empty = SpecialAssessment()
    assert empty.declared is False
    cost = monthly_cost(500_000, None, 0, FinanceAssumptions())
    assert (cost.assessment, cost.assessment_annual, cost.assessment_basis) == (0, 0, "none")


def test_one_lot_may_override_the_watchs_district():
    """Two homes on one street can sit in different improvement areas: a new 70-foot lot
    bills $3,271 a year, an early-phase one $928. Only the lot's own plan knows which, so
    the assessment is overridable per property."""
    new_lot = monthly_cost(700_000, None, 0, WALSH)
    early_phase = monthly_cost(
        700_000, None, 0, WALSH, assessment=SpecialAssessment(flat_annual=928.0)
    )

    assert new_lot.assessment_annual == 3271 and early_phase.assessment_annual == 928
    assert new_lot.total - early_phase.total == _round((3271 - 928) / 12)  # $195 a month


# -- rounding, and agreeing with a page that recomputes ---------------------------------


def test_rounding_is_half_up_where_pythons_own_is_bankers():
    """The parity bug, in one line. Python rounds an exact .5 tie to the nearest *even*
    number; JavaScript's Math.round — which a page uses when it recomputes a figure the
    reader has nudged — always rounds up. A dollar of disagreement is enough for a reader
    to stop trusting both numbers."""
    assert round(2102.5) == 2102 and _round(2102.5) == 2103
    assert round(0.5) == 0 and _round(0.5) == 1
    assert [_round(v) for v in (1.5, 2.5, 3.5, -0.4)] == [2, 3, 4, 0]
    assert all(_round(v) == math.floor(v + 0.5) for v in (10.49, 10.5, 10.51))


def test_a_cost_landing_on_an_exact_half_dollar_rounds_up():
    """The same tie, reached through `monthly_cost` rather than asserted about `_round`:
    a $1,000 home, all-cash, no tax, insured at $6 per $1,000 a year — 50 cents a month."""
    fin = FinanceAssumptions(
        down_pct=100.0, default_tax_rate=0.0, insurance_annual_per_1000=6.0
    )
    cost = monthly_cost(1_000, tax_rate=None, hoa_monthly=0, fin=fin)

    assert cost.principal_interest == 0 and cost.tax == 0
    assert cost.insurance == 1 and cost.total == 1  # not 0, which banker's rounding gives


def test_a_zero_interest_loan_amortises_in_a_straight_line():
    fin = FinanceAssumptions(mortgage_rate=0.0, down_pct=0.0, loan_term_years=30)
    cost = monthly_cost(360_000, tax_rate=1.0, hoa_monthly=0, fin=fin)
    assert cost.principal_interest == _round(360_000 / (30 * 12))


def test_dues_fall_back_to_the_market_default_but_a_homes_own_zero_is_respected():
    fin = FinanceAssumptions(hoa_default_monthly=150.0)
    assert monthly_cost(500_000, None, None, fin).dues == 150
    assert monthly_cost(500_000, None, 90, fin).dues == 90
    assert monthly_cost(500_000, None, 0, fin).dues == 0  # known to be nothing, not unknown


def test_there_is_no_monthly_figure_without_a_price():
    assert monthly_cost(None, 1.0, 0, FinanceAssumptions()) is None


# -- the rental lens --------------------------------------------------------------------


def test_rent_metrics_yield_cap_rate_and_cash_flow():
    cost = monthly_cost(500_000, tax_rate=1.5, hoa_monthly=0, fin=FinanceAssumptions())
    rm = rent_metrics(500_000, rent_estimate=3_000, cost=cost)

    assert rm.gross_yield == round(3_000 * 12 / 500_000 * 100, 2)  # 7.2%
    assert rm.cash_flow == _round(3_000 - cost.total)
    assert rm.cap_rate is not None and rm.cap_rate < rm.gross_yield  # operating costs drag


def test_a_flat_district_can_flip_a_rental_negative():
    """The reason carry, not list price, is the ranking that matters: the same home at the
    same rent is cash-flow positive without the district and negative with it."""
    fin = FinanceAssumptions(mortgage_rate=6.5, down_pct=60.0, default_tax_rate=2.339427)
    without = rent_metrics(500_000, 2_700, monthly_cost(500_000, None, 0, fin))
    walsh = fin.model_copy(update={"special_assessment": SpecialAssessment(flat_annual=6_000.0)})
    with_district = rent_metrics(500_000, 2_700, monthly_cost(500_000, None, 0, walsh))

    assert without.cash_flow > 0 > with_district.cash_flow
    assert with_district.cap_rate < without.cap_rate


def test_no_rent_estimate_means_no_rental_metrics():
    assert rent_metrics(500_000, None, None) is None
    assert rent_metrics(None, 3_000, None) is None
