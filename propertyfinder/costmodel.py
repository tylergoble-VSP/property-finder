"""What a home costs to hold, every month — in the shape reality actually comes in.

List price is the wrong yardstick for what a home costs. Two homes asking the same money
can differ by hundreds a month once a special district, an insurance market, and dues are
counted, and the family this tool serves signs up for the monthly number, not the sticker.

The one modelling decision this module exists to get right is the **special assessment**
(docs/REBUILD.md, post-mortem item 3). The original tool spelled it `special_assessment_pct`
— a percentage of value — because that is how an ad-valorem tax works. But the Walsh Public
Improvement District does not levy a percentage: it apportions a **fixed dollar principal
per lot**, and bills roughly $3,271 a year on a new 70-foot lot against roughly $928 on an
early-phase one. Forcing that fact through a percentage field knowingly mispriced early-phase
resales by about $200 a month for weeks. So here an assessment is a `SpecialAssessment`
carrying *either* `pct` *or* `flat_annual`, never both, declared per finance block and
overridable per property — because two homes on one street can sit in different improvement
areas, and only the lot's own service-and-assessment plan knows which.

A flat assessment is not a rate, so it is never folded into one. It is itemised on its own
line, and `all_in_tax_rate` is *derived* from the dollars rather than the other way round —
which is why that figure falls as price rises on a flat district, exactly as the real bill
does.

Everything here is a pure function of its inputs: no database, no network, no basis caveats
of its own. It inherits whatever honesty label the price it was handed carries.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from pydantic import BaseModel, model_validator

# Share of gross rent reserved for vacancy, maintenance and management in the cap-rate
# estimate. An industry rule of thumb, stated here rather than buried, because it is the
# one assumption in `rent_metrics` a reader might reasonably want to argue with.
_VACANCY_MAINT_FRAC = 0.15


def _round(x: float) -> int:
    """Round half-up to whole dollars, matching JavaScript's Math.round.

    Python's built-in round() is banker's rounding (half-even), which diverges by $1 on
    exact .5 ties (2102.5 rounds *down* to 2102) — so a figure computed here and the same
    figure recomputed client-side in a page disagree, and a "reset to the original numbers"
    control cannot restore what the server said. Half-up keeps both engines identical.
    """
    return math.floor(x + 0.5)


class SpecialAssessment(BaseModel):
    """A public-improvement or community-development district charge, in the form the
    district itself publishes it: a percentage of value, or a flat dollar bill per year.

    Declaring both is refused rather than reconciled. A district bills one way or the
    other, and a block claiming both is a config mistake whose only honest outcome is a
    loud failure at load time — the alternative is a monthly number that is quietly wrong
    for as long as nobody checks it.

    Declaring neither is the explicit "there is no district here", which is the ordinary
    case and the default.
    """

    pct: float | None = None  # %/yr of value, e.g. 0.35
    flat_annual: float | None = None  # $/yr per lot, e.g. 3271
    citation: str = ""  # where this number came from, shown in the report appendix

    @model_validator(mode="after")
    def _one_form_only(self) -> "SpecialAssessment":
        if self.pct is not None and self.flat_annual is not None:
            raise ValueError(
                "a special assessment is a percentage of value OR a flat annual dollar "
                f"amount, never both (got pct={self.pct}, flat_annual={self.flat_annual}). "
                "Districts bill one way; check the service-and-assessment plan and delete "
                "the other field."
            )
        return self

    @property
    def declared(self) -> bool:
        """True when this block actually names a charge."""
        return self.pct is not None or self.flat_annual is not None

    def annual(self, price: float) -> tuple[float, str]:
        """(dollars per year, basis) for a home at this price.

        Basis is "flat" (a fixed bill, the same at any price), "percent" (scales with
        value), or "none". It travels with the number so a page can say which it is.
        """
        if self.flat_annual is not None:
            return float(self.flat_annual), "flat"
        if self.pct is not None:
            return price * self.pct / 100.0, "percent"
        return 0.0, "none"


class FinanceAssumptions(BaseModel):
    """The mortgage, tax, insurance and district assumptions a monthly figure rests on.

    Defaults are deliberately market-neutral placeholders; the numbers that matter are
    verified per market and cited (`tax_rate_citation`, `SpecialAssessment.citation`) so
    the report can show its work. The original ran for weeks on a guessed 2.9% before
    anyone checked the adopted rates, and the correction moved every monthly number by
    hundreds of dollars.
    """

    mortgage_rate: float = 6.5  # annual %, fixed
    down_pct: float = 20.0
    loan_term_years: int = 30
    insurance_annual_per_1000: float = 5.0  # $/yr per $1,000 of value
    default_tax_rate: float = 2.0  # ad-valorem %/yr when the home's own rate is unknown
    tax_rate_citation: str = ""
    hoa_default_monthly: float = 0.0
    special_assessment: SpecialAssessment = SpecialAssessment()


@dataclass(frozen=True)
class MonthlyCost:
    """One home's monthly carry, itemised, in whole dollars.

    The assessment is its own line rather than a rate folded into the tax, which is the
    entire point: a flat district bill is a dollar fact, and the moment it is expressed as
    a percentage it becomes wrong for every home at a different price.
    """

    principal_interest: int
    tax: int  # ad-valorem property tax only
    insurance: int
    dues: int  # homeowners association
    assessment: int  # special improvement district
    total: int
    tax_rate_used: float  # the ad-valorem rate applied, %/yr
    tax_basis: str  # "home" (its own rate) | "default" (the market fallback)
    assessment_annual: int
    assessment_basis: str  # "flat" | "percent" | "none"
    all_in_tax_rate: float  # (tax + assessment) as %/yr of THIS price — derived, not assumed


def monthly_cost(
    price: float | None,
    tax_rate: float | None,
    hoa_monthly: float | None,
    fin: FinanceAssumptions,
    assessment: SpecialAssessment | None = None,
) -> MonthlyCost | None:
    """Principal and interest, property tax, insurance, dues, and the district assessment.

    `assessment` overrides the finance block's district for this one home — the early-phase
    lot paying $928 where its neighbour on a newer lot pays $3,271. Absent, the block's own
    assessment applies. Returns None without a usable price, because there is no honest
    monthly figure for a home whose asking price the feed never gave us.
    """
    if not price:
        return None

    loan = price * (1 - fin.down_pct / 100.0)
    n = fin.loan_term_years * 12
    r = fin.mortgage_rate / 100.0 / 12.0
    pi = loan * r * (1 + r) ** n / ((1 + r) ** n - 1) if r > 0 else loan / n

    # A home's own rate wins when the feed actually supplied one; otherwise the market
    # default stands. Which of the two was used is recorded, never inferred by the reader.
    rate, basis = (tax_rate, "home") if tax_rate and tax_rate > 0 else (
        fin.default_tax_rate,
        "default",
    )
    tax = price * (rate / 100.0) / 12.0

    district = assessment if assessment is not None else fin.special_assessment
    assessment_annual, assessment_basis = district.annual(price)

    insurance = price / 1000.0 * fin.insurance_annual_per_1000 / 12.0
    dues = hoa_monthly if hoa_monthly is not None else fin.hoa_default_monthly
    total = pi + tax + insurance + dues + assessment_annual / 12.0

    return MonthlyCost(
        principal_interest=_round(pi),
        tax=_round(tax),
        insurance=_round(insurance),
        dues=_round(dues),
        assessment=_round(assessment_annual / 12.0),
        total=_round(total),
        tax_rate_used=round(rate, 6),
        tax_basis=basis,
        assessment_annual=_round(assessment_annual),
        assessment_basis=assessment_basis,
        all_in_tax_rate=round(rate + assessment_annual / price * 100.0, 4),
    )


@dataclass(frozen=True)
class RentMetrics:
    """The investment lens: what the home yields, and whether it feeds or eats you.

    `monthly_rent` is the feed's rent estimate — a *reference* figure, displayed and
    labelled. It never enters a deal score (docs/EXPERT-PLAN.md, "Valuation: two tracks,
    no Zestimate"); it answers a different question entirely, which is what a landlord
    would collect, not what a buyer should pay.
    """

    monthly_rent: int
    gross_yield: float  # annual rent / price, %
    cap_rate: float | None  # net operating income / price, %
    cash_flow: int | None  # monthly rent minus the full carry


def rent_metrics(
    price: float | None, rent_estimate: float | None, cost: MonthlyCost | None
) -> RentMetrics | None:
    """Gross yield, a simple capitalisation rate, and monthly cash flow against the carry.

    The cash-flow line is where the special assessment earns its place in this module: a
    district bill routinely flips an apparently positive rental negative, and a tool that
    ranked on list price would never see it.
    """
    if not (price and rent_estimate):
        return None

    annual_rent = rent_estimate * 12.0
    gross_yield = annual_rent / price * 100.0
    cap_rate = cash_flow = None
    if cost is not None:
        # Net operating income excludes financing: tax, insurance, dues, the assessment,
        # and a reserve for vacancy and maintenance — but never the mortgage.
        opex = (
            cost.tax + cost.insurance + cost.dues + cost.assessment
        ) * 12.0 + annual_rent * _VACANCY_MAINT_FRAC
        cap_rate = (annual_rent - opex) / price * 100.0
        cash_flow = rent_estimate - cost.total

    return RentMetrics(
        monthly_rent=_round(rent_estimate),
        gross_yield=round(gross_yield, 2),
        cap_rate=round(cap_rate, 2) if cap_rate is not None else None,
        cash_flow=_round(cash_flow) if cash_flow is not None else None,
    )
