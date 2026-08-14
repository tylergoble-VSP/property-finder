"""Track B — new construction, where the comp is a price list rather than a sale.

Resale valuation (`stats.py`, `deals.py`) asks what homes like this one *sold* for. That
question has no answer in a brand-new subdivision: nothing has sold yet, and what has is
the builder selling to itself. But a builder is a rational repeat seller who publishes its
own prices, and the feed carries that price list in plain sight — rows whose address is a
plan name rather than a street address ("GRANTLEY Plan, Walsh Ranch 70'"). Sixty-eight of
them described the seed community in a single sweep.

So new-construction inventory splits in two, and the split is the whole of this module:

  - **plan sheets** — the ask curve. Not homes, not buyable, and poison in any comp set:
    a plan sheet is an offer, and averaging offers with sales measures how confidently a
    builder is asking. Excluded from every resale statistic in this codebase, and here
    they become the yardstick instead.
  - **spec homes** — a real address, built or building, with a price. These are the
    scoring targets, and what they are scored against is the builder's own ask for a home
    of that size.

The comparison is sqft-indexed rather than community-wide, because a builder's price per
foot falls as its plans grow — the same elasticity `stats.py` fits on resales, visible in
the price list itself. Comparing a 2,000-foot spec to the community's median plan price
per foot would flatter every small home and condemn every large one. So `comparable_ppsf`
takes the plans within ±20% of the home's size, and when that band is too thin to mean
anything it says so: it falls back to the whole community and returns `n_in_band = 0` and
`basis = "community"`, which the score turns into LOW confidence rather than a silent
approximation.

Two things the feed cannot see, and this module therefore refuses to price: builder
incentives (rate buydowns and closing-cost credits, worth a few percent, and never
published in a listing) and the improvement-district assessment that `costmodel.py`
carries. Both belong in the page's prose next to the score, as the reasons a number this
module produces is a reason to make a phone call rather than a conclusion.
"""
from __future__ import annotations

import re
import statistics
from dataclasses import dataclass
from typing import NamedTuple

from sqlalchemy.orm import Session

from propertyfinder.store import latest_snapshot_rows

# A plan comp is drawn from plans within this fraction of the home's size, either way.
SQFT_BAND = 0.20

# Below this many plans the band is not a comp, it is an anecdote. Three is the smallest
# number with a median that is not simply one plan wearing a statistic's name.
MIN_BAND_PLANS = 3

# The feed's own phrasing for new construction, matched case-insensitively because it is
# scraped display text and its capitalisation is not a promise.
NEWCON_STATUS = "new construction"

# "GRANTLEY Plan, Walsh Ranch 70'" -> name "GRANTLEY", community "Walsh Ranch 70'".
_PLAN_RE = re.compile(r"^(?P<name>.+?)\s*Plan,\s*(?P<community>.+)$")

# Where a plan row names no community. Kept as a bucket rather than dropped: the plan is
# still a real ask at a real size, and losing it would thin the curve it belongs to.
UNNAMED_COMMUNITY = "(unnamed community)"


def _address(row: dict) -> str:
    return row.get("address") or ""


def is_plan_sheet(row: dict) -> bool:
    """Is this row the builder's price list rather than a home someone can buy?

    The test is the address, and it is the same one `segments.py` and every comp filter in
    this codebase use: a plan row says "<plan name> Plan, <community>" where a home says
    a street address. It is a scraped-text rule and it is the only signal the feed gives.
    """
    return "Plan," in _address(row)


def plan_name(address: str | None) -> str | None:
    """The plan's own name — "GRANTLEY", "The Kennedy II" — or None if not a plan row."""
    match = _PLAN_RE.match((address or "").strip())
    return match.group("name").strip() or None if match else None


def plan_community(address: str | None) -> str | None:
    """The community a plan is offered in — "Walsh Ranch 70'", "Walsh Cottage"."""
    match = _PLAN_RE.match((address or "").strip())
    return match.group("community").strip() or None if match else None


def is_spec(row: dict) -> bool:
    """A buyable new home: the feed's new-construction status, at a real address."""
    status = (row.get("status_text") or "").strip().lower()
    return status == NEWCON_STATUS and not is_plan_sheet(row)


def ppsf(row: dict) -> float | None:
    """Asking dollars per foot, or None when either half of the fraction is missing."""
    price, sqft = row.get("price"), row.get("sqft")
    return price / sqft if price and sqft else None


class PlanPoint(NamedTuple):
    """One point on the ask curve: a plan, its size, and what the builder asks per foot."""

    plan: str
    sqft: float
    ppsf: float


class AskComp(NamedTuple):
    """What the builder asks per foot for a home this size — and how sure that is.

    `basis` is the label that travels with the number, in the same spirit as the sold
    baseline's disclosure basis: "band" means plans of comparable size set it, "community"
    means the band was too thin and the whole price list stood in, "none" means there was
    no price list at all. `n_in_band` is 0 in both of the latter cases, which is what any
    caller checking a single field should read as "this comp is not size-specific".
    """

    ppsf: float | None
    n_in_band: int
    basis: str  # band | community | none


@dataclass(frozen=True)
class CommunityAsk:
    """One community's price list, summarised: how many plans, how big, how dear."""

    community: str
    n: int
    sqft_p50: float
    ppsf_p50: float


@dataclass(frozen=True)
class PlanBaseline:
    """A watch's new-construction ask curve — the Track B answer to `SoldBaseline`."""

    watch_name: str
    n_plans: int
    communities: tuple[CommunityAsk, ...] = ()
    plans: tuple[PlanPoint, ...] = ()

    def comparable_ppsf(self, sqft: float | None) -> AskComp:
        """The builder's ask per foot for a home of this size.

        Plans within ±20% first; the whole price list when fewer than three of them exist,
        flagged rather than hidden. The fallback is deliberately not a wider band chosen
        until it contains enough plans — a band tuned per query is a comp fitted to the
        answer, and a reader cannot tell one from the honest kind.
        """
        if not self.plans:
            return AskComp(None, 0, "none")
        if sqft:
            band = [p.ppsf for p in self.plans if abs(p.sqft - sqft) <= SQFT_BAND * sqft]
            if len(band) >= MIN_BAND_PLANS:
                return AskComp(statistics.median(band), len(band), "band")
        return AskComp(statistics.median([p.ppsf for p in self.plans]), 0, "community")


def compute_plan_baseline(session: Session, watch_name: str) -> PlanBaseline:
    """Reduce a watch's plan-sheet rows to an ask curve and its per-community summary.

    Plans without both a price and a size are dropped: an ask curve is made of the two
    numbers, and a plan missing either cannot sit on it. Everything that is not a plan
    sheet — resales, spec homes, land — is ignored here by construction, which is what
    keeps the builder's offers and the market's sales on opposite sides of the analysis.
    """
    rows = latest_snapshot_rows(session, watch_name)
    plans = [r for r in rows if is_plan_sheet(r) and r.get("price") and r.get("sqft")]

    by_community: dict[str, list[dict]] = {}
    for row in plans:
        community = plan_community(_address(row)) or UNNAMED_COMMUNITY
        by_community.setdefault(community, []).append(row)

    communities = tuple(
        sorted(
            (
                CommunityAsk(
                    community=name,
                    n=len(rows_in),
                    sqft_p50=statistics.median([r["sqft"] for r in rows_in]),
                    ppsf_p50=statistics.median([ppsf(r) for r in rows_in]),
                )
                for name, rows_in in by_community.items()
            ),
            key=lambda c: (-c.n, c.community),  # biggest price list first, then by name
        )
    )
    return PlanBaseline(
        watch_name=watch_name,
        n_plans=len(plans),
        communities=communities,
        plans=tuple(
            PlanPoint(plan=_address(r), sqft=float(r["sqft"]), ppsf=ppsf(r)) for r in plans
        ),
    )
