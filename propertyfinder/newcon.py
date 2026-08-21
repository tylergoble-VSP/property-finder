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

What a spec home scores on is, deliberately, only what the builder has done: priced this
house under its own list for that size, failed to sell it for two months, cut its price.
Fifty points to start, four per percentage point of undercut to a cap of twenty-five, and
fixed points for staleness and for an observed cut — priors from the methodology plan
rather than fitted weights, because a market with no closed sales has nothing to fit on.
No published estimate and no rent figure enters the arithmetic. The original tool's spec
score carried a rent-yield bonus, and a yield computed from an estimated rent against an
asking price is two guesses in a trench coat.

Two things the feed cannot see, and this module therefore refuses to price: builder
incentives (rate buydowns and closing-cost credits, worth a few percent, and never
published in a listing) and the improvement-district assessment that `costmodel.py`
carries. Both belong in the page's prose next to the score, as the reasons a number this
module produces is a reason to make a phone call rather than a conclusion.
"""
from __future__ import annotations

import logging
import re
import statistics
from dataclasses import dataclass
from typing import TYPE_CHECKING, NamedTuple, Sequence

from sqlalchemy.orm import Session

from propertyfinder.store import latest_snapshot_rows, price_change_map

if TYPE_CHECKING:  # a type name only; see the note above score_specs about the direction
    from propertyfinder.dataquality import DataQuality

log = logging.getLogger(__name__)

# A plan comp is drawn from plans within this fraction of the home's size, either way.
SQFT_BAND = 0.20

# Below this many plans the band is not a comp, it is an anecdote. Three is the smallest
# number with a median that is not simply one plan wearing a statistic's name.
MIN_BAND_PLANS = 3

# The feed's own phrasing for new construction, matched case-insensitively because it is
# scraped display text and its capitalisation is not a promise.
NEWCON_STATUS = "new construction"

# The score's coefficients. These are priors from the methodology plan, not fitted
# weights — Track B has no closed sales to fit on, which is the whole reason it exists —
# and they are written here as named constants so that the day a spec home does close,
# recalibrating means editing six numbers rather than reading a function.
BASE_SCORE = 50.0
DISCOUNT_POINTS_PER_PCT = 4.0  # a percentage point under the builder's own ask
DISCOUNT_CAP = 25.0  # no undercut wins the score outright, however deep
STALE_DAYS = 60  # a spec sitting this long is costing the builder interest
STALE_POINTS = 8.0
DEEP_CUT_PCT = 3.0
DEEP_CUT_POINTS = 12.0
ANY_CUT_POINTS = 8.0

# Verdict bands, deliberately identical to `deals.verdict_for` so a resale and a spec home
# read on one scale in one report. They are restated rather than imported because that
# module pulls in the scientific stack, and Track B — which fits nothing — must stay
# runnable by someone who installed the core package alone.
GREAT, GOOD, FAIR, OVERPRICED = "GREAT", "GOOD", "FAIR", "OVERPRICED"

# "GRANTLEY Plan, Walsh Ranch 70'" -> name "GRANTLEY", community "Walsh Ranch 70'".
_PLAN_RE = re.compile(r"^(?P<name>.+?)\s*Plan,\s*(?P<community>.+)$")

# Where a plan row names no community. Kept as a bucket rather than dropped: the plan is
# still a real ask at a real size, and losing it would thin the curve it belongs to.
UNNAMED_COMMUNITY = "(unnamed community)"


def _address(row: dict) -> str:
    return row.get("address") or ""


def on_the_market_today(rows: Sequence[dict]) -> list[dict]:
    """The rows from the most recent sweep only — what is actually for sale right now.

    `store.latest_snapshot_rows` answers a different and equally correct question: the
    newest sighting of every home this watch has ever seen. On any database older than one
    sweep that set includes homes which have since been delisted, and a page dated today
    that counts them is simply wrong. The first refreshed buyer report in the original tool
    was built on 180 homes when 145 were live: it counted 35 dead listings, and every
    median, every count and the fitted ask curve were polluted by them
    (docs/PORTING-THE-REPORTS.md, lesson 2).

    So the filter lives here, in the module, applied by every function whose output
    describes the present — never in a caller, because a caller is a place the rule has to
    be re-learned. A baseline or a model fitted *for* a current-market page is fitted on
    this set, not on the raw query.
    """
    sweep_ts = max((r["snapshot_ts"] for r in rows), default=None)
    return [r for r in rows if r["snapshot_ts"] == sweep_ts] if sweep_ts else []


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

    The curve is fitted on the *current* sweep only (`on_the_market_today`). A plan the
    builder has withdrawn is a price it is no longer asking, and leaving it on the curve
    would let a discontinued plan set the yardstick every standing spec home is scored
    against.
    """
    rows = on_the_market_today(latest_snapshot_rows(session, watch_name))
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


# -- scoring a spec home against the price list -----------------------------------------


@dataclass(frozen=True)
class ScoreLine:
    """One line of the arithmetic: what it is called, what it is worth, and why."""

    label: str
    points: float
    detail: str


@dataclass(frozen=True)
class ScoreCard:
    """Everything this tool is willing to say about one spec home's asking price."""

    zpid: str
    address: str | None
    price: float | None
    sqft: float | None
    baths: float | None  # the corrected count where one was verified — see `quality`
    score: float
    verdict: str
    confidence: str  # HIGH | MED | LOW
    discount_pct: float | None  # under (+) or over (−) the builder's ask for this size
    comp: AskComp
    ledger: tuple[ScoreLine, ...] = ()
    quality: "DataQuality | None" = None

    def ledger_total(self) -> float:
        return round(sum(line.points for line in self.ledger), 4)


def verdict_for(score: float) -> str:
    if score >= 75:
        return GREAT
    if score >= 60:
        return GOOD
    if score >= 45:
        return FAIR
    return OVERPRICED


def _confidence(row: dict, comp: AskComp) -> str:
    """How much weight this score will bear.

    It is a statement about the comp, not about the home. A band of five or more plans of
    comparable size is the best this track offers; a fallback to the whole price list, or
    a home whose size the feed never gave, is LOW however confident the arithmetic looks.
    """
    if not row.get("sqft") or comp.ppsf is None:
        return "LOW"
    if comp.n_in_band >= 5:
        return "HIGH"
    if comp.n_in_band >= MIN_BAND_PLANS:
        return "MED"
    return "LOW"


def score_spec(
    row: dict,
    baseline: PlanBaseline,
    cut_pct: float = 0.0,
    quality: "DataQuality | None" = None,
) -> ScoreCard:
    """Score one spec home against the builder's own ask, and show the arithmetic.

    Three signals, and all three are things the builder did rather than things a model
    believes: it priced this home under its own list for that size, it has failed to sell
    it for two months, and it has already cut. Nothing here consults a published estimate
    or a rent figure — the original tool's spec score carried a rent-yield bonus, and it
    had no business in a number a family reads as "is this house priced well".

    `row` should already be corrected (`dataquality.apply_corrections`); `quality` is
    attached so the page can show what was corrected and why.
    """
    ask = ppsf(row)
    comp = baseline.comparable_ppsf(row.get("sqft"))
    ledger = [ScoreLine("Starting point", BASE_SCORE, "every spec home starts here")]

    discount_pct: float | None = None
    if ask and comp.ppsf:
        discount_pct = (comp.ppsf - ask) / comp.ppsf * 100
        raw = DISCOUNT_POINTS_PER_PCT * discount_pct
        points = max(-DISCOUNT_CAP, min(DISCOUNT_CAP, raw))
        against = (
            f"{comp.n_in_band} plans of comparable size"
            if comp.basis == "band"
            else "the whole community price list, no plan being close in size"
        )
        detail = (
            f"asking ${ask:,.0f} per foot against ${comp.ppsf:,.0f} — "
            f"{discount_pct:+.1f}% versus {against}"
        )
        if points != raw:
            detail += f" (capped at {DISCOUNT_CAP:+.0f} points)"
        ledger.append(ScoreLine("Against the builder's ask", round(points, 1), detail))
    else:
        ledger.append(
            ScoreLine(
                "Against the builder's ask",
                0.0,
                "not scored: this home has no price per foot, or the community has no "
                "price list to compare it against",
            )
        )

    dom = row.get("days_on_zillow")
    if dom is not None and dom > STALE_DAYS:
        ledger.append(
            ScoreLine(
                "Sitting unsold",
                STALE_POINTS,
                f"{dom} days on the market — a finished house the builder is paying "
                "interest on every month it stands empty",
            )
        )

    if cut_pct >= DEEP_CUT_PCT:
        ledger.append(
            ScoreLine(
                "Price cut",
                DEEP_CUT_POINTS,
                f"down {cut_pct:.0f}% from the first ask we recorded — builders cut late "
                "and reluctantly",
            )
        )
    elif cut_pct > 0:
        ledger.append(
            ScoreLine("Price cut", ANY_CUT_POINTS, f"down {cut_pct:.1f}% from its first ask")
        )

    score = round(sum(line.points for line in ledger), 1)
    return ScoreCard(
        zpid=str(row.get("zpid") or ""),
        address=row.get("address"),
        price=row.get("price"),
        sqft=row.get("sqft"),
        baths=row.get("baths"),
        score=score,
        verdict=verdict_for(score),
        confidence=_confidence(row, comp),
        discount_pct=discount_pct,
        comp=comp,
        ledger=tuple(ledger),
        quality=quality,
    )


def score_specs(
    session: Session,
    watch_name: str,
    baseline: PlanBaseline,
    corrections=None,
    plans_by_builder=None,
) -> list[ScoreCard]:
    """Every buyable spec home under a watch, corrected, scored, best first.

    Data quality runs first and over the *whole* watch rather than over the spec homes
    alone, because two of its detections need a home's neighbours: a duplicate needs the
    twin it duplicates, which may be a resale row, and a suspect bath count needs the
    sibling plan that proves the feed can print a half. What comes back is scored on the
    corrected record — a verified bath count is what the card reports — and a home the feed
    listed twice is dropped from the ranking entirely, because a leaderboard that shows
    one house in two places is wrong in the way readers notice.

    Only homes seen in the most recent sweep are scored. Data quality still runs over the
    whole of history — that is deliberate, and it is the reason the filter is applied to the
    loop rather than to the query: a duplicate needs the twin it duplicates, and the twin
    may last have been seen a sweep ago.

    The import sits inside the function on purpose. `dataquality` speaks this module's
    plan-sheet vocabulary and imports it at module scope; scoring is the one place the
    arrow turns around, and it turns around here rather than at import time.
    """
    from propertyfinder.dataquality import apply_corrections, assess

    rows = latest_snapshot_rows(session, watch_name)
    quality = assess(rows, corrections=corrections, plans_by_builder=plans_by_builder)
    cuts = price_change_map(session, watch_name)

    cards: list[ScoreCard] = []
    duplicates = 0
    for row in on_the_market_today(rows):
        if not is_spec(row):
            continue
        found = quality.get(str(row.get("zpid") or ""))
        if found is not None and found.is_duplicate:
            duplicates += 1
            continue
        cut_pct = (cuts.get(str(row.get("zpid") or "")) or {}).get("cut_pct") or 0.0
        corrected = apply_corrections(row, found)
        cards.append(score_spec(corrected, baseline, cut_pct, quality=found))

    if duplicates:
        log.info("%d spec home(s) excluded from %s: duplicate listings", duplicates, watch_name)
    cards.sort(key=lambda card: (-card.score, card.zpid))
    return cards
