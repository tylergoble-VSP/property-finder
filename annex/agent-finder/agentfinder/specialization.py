"""Rank agents by how much luxury they actually hold — the concentration answer.

Every figure is derived, never stored, and carries `basis="in_radius_only"`: we see only the
listings inside the circle, so these are counts of *this market's share* of an agent's book,
not their whole book. An agent with two here and forty in Dallas reads as they appear here,
and the report says so rather than implying otherwise.

Measured reality (the ranked sweep, n=120 across 6 ZIPs): the top 10 of ~74 agents hold ~43%
of luxury volume, ~11 cover half, ~40 cover 80%. So this ranks a *head* of repeat specialists
to contact first, with a tail — not a hard top-10 cutoff.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field

from agentfinder.attribution import CONFIRMED, INFERRED


@dataclass(frozen=True)
class RankedAgent:
    agent_key: str
    name: str
    brokerage: str | None
    licence: str | None
    phone: str | None
    trec_status: str | None
    tier: str                       # attribution tier of the strongest listing (CONFIRMED/INFERRED)
    n_listings: int
    volume: float                   # sum of list prices held, in the ring
    median_price: float
    max_price: float
    specialist: str                 # SPECIALIST (>=2 luxury) | ACTIVE (1)
    zpids: tuple[str, ...] = field(default_factory=tuple)
    basis: str = "in_radius_only"


def rank_agents(current: dict[str, dict], snapshots: list[dict]) -> list[RankedAgent]:
    """One row per agent who holds >=1 luxury listing, ranked by dollar-volume held."""
    price_by_zpid = {r["zpid"]: r.get("price") for r in snapshots}
    grouped: dict[str, list[dict]] = {}
    for zpid, attr in current.items():
        if attr["tier"] in (CONFIRMED, INFERRED) and attr.get("agent_key"):
            grouped.setdefault(attr["agent_key"], []).append(attr)

    ranked: list[RankedAgent] = []
    for key, attrs in grouped.items():
        zpids = [a["zpid"] for a in attrs]
        prices = [price_by_zpid.get(z) or 0.0 for z in zpids]
        prices = [p for p in prices if p] or [0.0]
        best = sorted(attrs, key=lambda a: 0 if a["tier"] == CONFIRMED else 1)[0]
        ranked.append(RankedAgent(
            agent_key=key, name=best["agent_name"], brokerage=best.get("brokerage"),
            licence=best.get("licence"), phone=best.get("phone"),
            trec_status=best.get("trec_status"), tier=best["tier"],
            n_listings=len(zpids), volume=sum(prices),
            median_price=statistics.median(prices), max_price=max(prices),
            specialist="SPECIALIST" if len(zpids) >= 2 else "ACTIVE",
            zpids=tuple(zpids)))
    ranked.sort(key=lambda a: (-a.volume, -a.n_listings, a.name))
    return ranked


def concentration(ranked: list[RankedAgent], total_volume: float) -> dict:
    """The concentration summary: how few agents hold how much."""
    attributed = sum(a.volume for a in ranked)
    top10 = sum(a.volume for a in ranked[:10])

    def _cover(target_frac: float) -> int:
        cum = 0.0
        for i, a in enumerate(ranked, 1):
            cum += a.volume
            if attributed and cum >= target_frac * attributed:
                return i
        return len(ranked)

    return {
        "unique_agents": len(ranked),
        "total_volume": total_volume,
        "attributed_volume": attributed,
        "attributed_pct": (attributed / total_volume * 100) if total_volume else 0.0,
        "top10_volume": top10,
        "top10_pct_of_total": (top10 / total_volume * 100) if total_volume else 0.0,
        "agents_to_cover_50pct": _cover(0.5),
        "agents_to_cover_80pct": _cover(0.8),
        "repeat_specialists": sum(1 for a in ranked if a.n_listings >= 2),
    }
