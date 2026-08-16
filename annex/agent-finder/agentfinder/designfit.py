"""Why one listing matters to a designer — a transparent ledger, built like `deals.py`.

Base score plus named adjustments, each a sentence Lindsey can read, and the entries sum to
the score exactly. The signals are the designer's actual openings: an agent who already pays
for presentation (Showcase, a 3-D tour) is a warm design relationship; an unfurnished
new-construction spec needs everything; a stalled or cut luxury listing is a restage
conversation. Every signal is free from the sweep, and — core's honesty rule — a missing
free field is *unknown*, never False: it simply scores nothing rather than counting against.
"""
from __future__ import annotations

from dataclasses import dataclass, field

STRONG, WORTH_A_CALL, WEAK = "STRONG", "WORTH_A_CALL", "WEAK"
BASE = 50.0


@dataclass(frozen=True)
class FitLine:
    label: str
    points: float


@dataclass(frozen=True)
class DesignFit:
    zpid: str
    score: float
    verdict: str
    ledger: tuple[FitLine, ...] = field(default_factory=tuple)

    def total(self) -> float:
        return round(sum(l.points for l in self.ledger), 2)


def verdict_for(score: float) -> str:
    return STRONG if score >= 70 else WORTH_A_CALL if score >= 58 else WEAK


def score_fit(row: dict, extras: dict | None, floor: float) -> DesignFit:
    """Score one luxury listing for design-opportunity, with a ledger that explains itself."""
    extras = extras or {}
    ledger = [FitLine("Every luxury listing starts here", BASE)]
    price = row.get("price") or 0

    if price >= 3 * floor:
        ledger.append(FitLine(f"Ultra-luxury ask (${price/1e6:.1f}M) — top of the market", 12))
    elif price >= 2 * floor:
        ledger.append(FitLine(f"High-luxury ask (${price/1e6:.1f}M)", 8))
    elif price >= floor:
        ledger.append(FitLine(f"Luxury ask (${price/1e6:.1f}M)", 4))

    if extras.get("is_showcase") == 1:
        ledger.append(FitLine("Zillow Showcase listing — this agent already invests in "
                              "presentation, the warmest kind of design relationship", 12))
    if extras.get("has_3d_model") == 1:
        ledger.append(FitLine("Has a 3-D tour — a design-forward, presentation-spending agent", 6))
    if extras.get("builder_name") or (extras.get("new_construction_type") or ""):
        ledger.append(FitLine("New-construction spec — an unfurnished home that needs "
                              "everything", 10))

    dom = row.get("days_on_zillow")
    if dom is not None and dom >= 90:
        ledger.append(FitLine(f"On the market {int(dom)} days — a stalled luxury home is a "
                              "restage-and-reshoot conversation", 8))
    elif dom is not None and dom >= 60:
        ledger.append(FitLine(f"On the market {int(dom)} days — slowing", 4))

    if extras.get("price_reduction"):
        ledger.append(FitLine("Price cut since listing — a motivated seller open to value-adds", 6))

    raw = round(sum(l.points for l in ledger), 2)
    score = min(100.0, raw)
    if score != raw:
        ledger.append(FitLine("Capped at 100", round(score - raw, 2)))
    return DesignFit(zpid=row["zpid"], score=round(score, 1),
                     verdict=verdict_for(score), ledger=tuple(ledger))
