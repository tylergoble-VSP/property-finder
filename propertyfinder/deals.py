"""One score per home, and the arithmetic that produced it, shown.

Two independent readings of a price arrive from `stats.py`: a fitted market surface, and
the nearest recent sales of similar size. This module fuses them into a number between 0
and 100, a verdict, a confidence tier, and — the part that matters most — a **ledger**.

The ledger is not decoration. A family deciding on a six-figure purchase is owed the
sentence "this scored 78 because it starts at 50, its price is two standard deviations
below what homes like it fetch here (+30), both methods agree (+8), and it has been
sitting for four months (+12)". Every entry names itself, states its points, and explains
itself in plain English, and the entries sum to the score exactly — including the entry
that records a clamp, because a score silently trimmed to 100 is a ledger that lies.

The other half of the job is refusing to confuse *cheap* with *good*. A home two standard
deviations under the surface is either a bargain or a house with something wrong with it,
and no amount of statistics can tell which from a listing feed. So `condition_flags`
attaches the reasons a low price might be earned — a foreclosure word in the status line,
a home nobody has bought in months, a statistical extreme, land rather than a house — and
an unexplained extreme drops confidence rather than raising the score.

**No published site estimate enters any of this.** Asking prices re-anchor to that number,
so scoring a listing against it largely measures how closely the seller read the same web
page; the original tool built its first signal on it as a placeholder and had to retire it
publicly. The score is built on sales, and the tests here hold this module to that.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from propertyfinder.stats import Comp, Expectation, HedonicModel, knn_comps

# Verdict bands. Coarse on purpose: the underlying surface is a statistical estimate, not
# an appraisal, and a scale finer than these four words would imply a precision it has not.
GREAT, GOOD, FAIR, OVERPRICED = "GREAT", "GOOD", "FAIR", "OVERPRICED"

BASE_SCORE = 50.0
Z_POINTS_PER_SIGMA = 15.0  # each standard deviation below expectation is worth this much
Z_POINTS_CAP = 30.0  # but no run of statistics wins the score outright
AGREEMENT_POINTS = 8.0
OUTLIER_Z = -2.5  # below this, "underpriced" needs a reason before it needs a bid

# Words that mean a low price may have been earned. Matched against the feed's own status
# phrasing, which is the only place any of them ever appears.
_DISTRESS_WORDS = [
    ("foreclos", "Foreclosure"),
    ("pre-foreclosure", "Pre-foreclosure"),
    ("auction", "Auction"),
    ("short sale", "Short sale"),
    ("bank owned", "Bank owned"),
    ("bank-owned", "Bank owned"),
    ("as-is", "Sold as-is"),
    ("as is", "Sold as-is"),
]

_LAND_TYPES = {"LOT", "LAND"}

# Flags that mean the model was asked a question it cannot answer, rather than merely
# noting something a buyer should check. Either one drops the card to LOW confidence: an
# extreme is as likely a condition problem or a bad row as a bargain, and a lot scored by a
# model fitted on houses is a category error however neat the arithmetic looks.
_CONFIDENCE_BREAKING = ("Statistical outlier", "Land,")


@dataclass(frozen=True)
class LedgerEntry:
    """One line of the arithmetic: what it is called, what it is worth, and why."""

    label: str
    points: float
    detail: str


@dataclass(frozen=True)
class DealCard:
    """Everything this tool is willing to say about one home's asking price."""

    zpid: str
    address: str | None
    price: float
    sqft: float
    expectation: Expectation
    comp_ppsf: float | None  # what nearby similar homes actually fetched, per foot
    comp_discount_pct: float | None  # the ask against that, as a percentage
    comps: list[Comp]
    agree: bool  # both methods independently call it underpriced
    score: float
    verdict: str
    confidence: str  # HIGH | MED | LOW
    ledger: list[LedgerEntry] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)

    @property
    def z(self) -> float:
        return self.expectation.z

    @property
    def basis(self) -> str:
        """"disclosed" or "proxy" — which surface this whole card stands on."""
        return self.expectation.basis

    def ledger_total(self) -> float:
        return round(sum(entry.points for entry in self.ledger), 4)


def verdict_for(score: float) -> str:
    if score >= 75:
        return GREAT
    if score >= 60:
        return GOOD
    if score >= 45:
        return FAIR
    return OVERPRICED


def condition_flags(
    row: dict, z: float | None, dom_pctile: float | None, cut_pct: float
) -> list[str]:
    """Reasons a low price might be earned rather than mispriced.

    Every one of these is computed from what a sweep already stored, so a flag costs no
    API call and no delay. None of them lowers the score — they are not penalties, they
    are the questions a buyer should ask before viewing, and an unexplained statistical
    extreme lowers *confidence* instead.
    """
    flags: list[str] = []
    status = (row.get("status_text") or "").lower()

    seen: set[str] = set()
    for word, label in _DISTRESS_WORDS:
        if word in status and label not in seen:
            flags.append(label)
            seen.add(label)

    if z is not None and z <= OUTLIER_Z:
        flags.append("Statistical outlier — verify condition and the listing's own numbers")
    if dom_pctile is not None and dom_pctile >= 0.90:
        flags.append("Stale — longer on the market than 90% of what is for sale here")
    if cut_pct >= 10:
        # The same cut that scores points also raises a question. Both are true: the seller
        # is motivated, and something has kept everyone else from buying.
        flags.append(f"Steep cut — down {cut_pct:.0f}% from its own high")
    if (row.get("home_type") or "") in _LAND_TYPES:
        flags.append("Land, not a house — priced a different way entirely")
    return flags


def deal_card(
    home: dict,
    model: HedonicModel,
    sold_rows: list[dict],
    dom_pctile: float | None = None,
    cut_pct: float = 0.0,
) -> DealCard | None:
    """Score one home. None when the feed never described it well enough to judge."""
    expectation = model.expected(home)
    if expectation is None:
        return None

    comp_ppsf, comps = knn_comps(home, sold_rows, basis=model.basis)
    sqft = float(home["sqft"])
    comp_discount = (
        (comp_ppsf * sqft - expectation.price) / (comp_ppsf * sqft) * 100
        if comp_ppsf
        else None
    )
    agree = bool(
        comp_discount is not None and expectation.discount_pct > 3 and comp_discount > 3
    )

    ledger = _build_ledger(expectation, comp_discount, agree, dom_pctile, cut_pct)
    raw = round(sum(entry.points for entry in ledger), 4)
    score = min(100.0, max(0.0, raw))
    if score != raw:
        # A clamp is an arithmetic event and belongs in the arithmetic. Without this line
        # the ledger would add to one number while the card showed another.
        ledger.append(
            LedgerEntry(
                label="Clamped to the 0–100 range",
                points=round(score - raw, 4),
                detail=f"the components came to {raw:.0f}, which the scale does not hold",
            )
        )

    flags = condition_flags(home, expectation.z, dom_pctile, cut_pct)
    return DealCard(
        zpid=home["zpid"],
        address=home.get("address"),
        price=expectation.price,
        sqft=sqft,
        expectation=expectation,
        comp_ppsf=comp_ppsf,
        comp_discount_pct=comp_discount,
        comps=comps,
        agree=agree,
        score=round(score, 1),
        verdict=verdict_for(score),
        confidence=_confidence(model, comps, flags),
        ledger=ledger,
        flags=flags,
    )


def _build_ledger(
    expectation: Expectation,
    comp_discount: float | None,
    agree: bool,
    dom_pctile: float | None,
    cut_pct: float,
) -> list[LedgerEntry]:
    """The whole score, as named lines. Nothing is added to a card that is not here."""
    ledger = [
        LedgerEntry("Starting point", BASE_SCORE, "every home starts here"),
        LedgerEntry(
            "Statistical value",
            round(
                max(-Z_POINTS_CAP, min(Z_POINTS_CAP, -expectation.z * Z_POINTS_PER_SIGMA)), 1
            ),
            f"asking {expectation.discount_pct:+.0f}% against what homes like this fetch "
            f"here, which is {expectation.z:+.2f} standard deviations from the middle",
        ),
    ]

    if expectation.location_comps and abs(expectation.location_pct) >= 2:
        ledger.append(
            LedgerEntry(
                "Location",
                0.0,
                f"nearby sales run {expectation.location_pct:+.0f}% against the size model, "
                "which is already folded into the expected price above — it moves the "
                "yardstick, not the score",
            )
        )

    if agree:
        ledger.append(
            LedgerEntry(
                "Both methods agree",
                AGREEMENT_POINTS,
                f"the fitted market and the nearest sales ({comp_discount:+.0f}%) "
                "independently call this underpriced",
            )
        )

    if dom_pctile is not None and dom_pctile >= 0.75:
        ledger.append(
            LedgerEntry(
                "Sitting on the market",
                12.0 if dom_pctile >= 0.90 else 8.0,
                f"listed longer than {round(dom_pctile * 100)}% of what is for sale here — "
                "more room to negotiate",
            )
        )

    if cut_pct >= 3:
        ledger.append(
            LedgerEntry(
                "Price cut", 12.0, f"down {cut_pct:.0f}% from its own high — a motivated seller"
            )
        )
    elif cut_pct > 0:
        ledger.append(LedgerEntry("Price cut", 8.0, f"down {cut_pct:.1f}% from its own high"))

    return ledger


def _confidence(model: HedonicModel, comps: list[Comp], flags: list[str]) -> str:
    """How much weight this score will bear.

    Three things lower it and nothing raises it: a thin comp set, a market that discloses
    no sale prices, and a flag that says the model was asked the wrong question. Both
    methods agreeing that a home is half price does not settle the matter — they read the
    same asking price, and neither of them has been inside the house.
    """
    if len(comps) >= 6 and model.r2 >= 0.75:
        confidence = "HIGH"
    elif len(comps) >= 4:
        confidence = "MED"
    else:
        confidence = "LOW"

    if model.basis == "proxy" and confidence == "HIGH":
        # Nothing fitted on estimates rather than sale prices earns the top tier, however
        # tidy the fit looks. The state simply did not tell us what these homes sold for.
        confidence = "MED"
    if any(f.startswith(prefix) for f in flags for prefix in _CONFIDENCE_BREAKING):
        confidence = "LOW"
    return confidence


def dom_percentiles(active_rows: list[dict]) -> dict[str, float]:
    """Per home, the share of the market it has outlasted. Homes the feed gave no
    days-on-market are absent rather than assumed fresh."""
    known = [
        (r["zpid"], float(r["days_on_zillow"]))
        for r in active_rows
        if r.get("days_on_zillow") is not None
    ]
    if not known:
        return {}
    values = [d for _, d in known]
    return {
        zpid: sum(1 for v in values if v < days) / len(values) for zpid, days in known
    }


def build_deal_cards(
    active_rows: list[dict],
    sold_rows: list[dict],
    model: HedonicModel | None = None,
    cuts: dict | None = None,
) -> list[DealCard]:
    """Every home this tool can honestly score, best first.

    An empty list is a real answer: a market with too few sales to fit produces no cards
    at all rather than cards built on something weaker, and the page that renders them says
    so instead of showing a ranking nobody should act on.
    """
    model = model or HedonicModel.fit(sold_rows)
    if model is None:
        return []

    pctiles = dom_percentiles(active_rows)
    cuts = cuts or {}
    cards = [
        card
        for card in (
            deal_card(
                row,
                model,
                sold_rows,
                dom_pctile=pctiles.get(row["zpid"]),
                cut_pct=max(0.0, cuts.get(row["zpid"], {}).get("cut_pct") or 0.0),
            )
            for row in active_rows
        )
        if card is not None
    ]
    cards.sort(key=lambda c: (-c.score, c.address or "", c.zpid))
    return cards
