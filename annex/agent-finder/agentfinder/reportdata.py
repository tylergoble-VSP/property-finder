"""Database in, one JSON payload out — no HTML anywhere near this file.

Two views over the same facts, cross-linked: an **agents** leaderboard (who to call, in what
order, with their contact and how sure we are of it) and an **opportunities** list (each
luxury listing's photos, why it matters to a designer, and who holds it). The page is built
from this dict plus a template by `pagebuild.render`, exactly as every core page is.

Honesty travels into the payload: the `coverage` block states the resolution rate and shows
unresolved counts openly; every attribution carries its tier and, when short of CONFIRMED, a
plain-English reason; every derived figure that rests on a partial view says so.
"""
from __future__ import annotations

import json

from sqlalchemy.orm import Session

from propertyfinder.store import latest_snapshot_rows

from agentfinder.attribution import CONFIRMED, INFERRED, UNRESOLVED
from agentfinder.config import LuxuryConfig
from agentfinder.designfit import score_fit
from agentfinder.specialization import concentration, rank_agents
from agentfinder.store import current_attribution, extras_map


def _photos(row: dict, extras: dict | None) -> list[str]:
    """Photo URLs for a listing — from the free search `images` array, else the thumbnail.

    Hotlinked to Zillow rather than embedded: listing photos are the photographer's/broker's
    copyright, and downloading-and-archiving them (even privately) is the reproduction a
    rights-holder objects to. Linking keeps the page honest about whose images they are, at
    the cost of the page needing the network to show them — a deliberate trade (plan §5)."""
    imgs = []
    if extras and extras.get("images_json"):
        try:
            imgs = [u for u in json.loads(extras["images_json"]) if u][:5]
        except (ValueError, TypeError):
            imgs = []
    if not imgs and row.get("image_url"):
        imgs = [row["image_url"]]
    return imgs


def build_payload(session: Session, cfg: LuxuryConfig, generated_ts: str) -> dict:
    snapshots = latest_snapshot_rows(session, cfg.name)
    current = current_attribution(session, cfg.name)
    extras = extras_map(session, cfg.name)
    total_volume = sum(r.get("price") or 0 for r in snapshots)

    ranked = rank_agents(current, snapshots)
    conc = concentration(ranked, total_volume)

    # Contact leaderboard: head of repeat specialists first, then the tail.
    agents = [{
        "name": a.name, "brokerage": a.brokerage, "licence": a.licence, "phone": a.phone,
        "trec_status": a.trec_status, "tier": a.tier, "specialist": a.specialist,
        "n_listings": a.n_listings, "volume": a.volume, "median_price": a.median_price,
        "max_price": a.max_price, "zpids": list(a.zpids), "basis": a.basis,
    } for a in ranked]

    opportunities = []
    for r in snapshots:
        attr = current.get(r["zpid"], {"tier": UNRESOLVED, "reason": "not yet resolved"})
        fit = score_fit(r, extras.get(r["zpid"]), cfg.price_floor)
        opportunities.append({
            "zpid": r["zpid"], "address": r.get("address"), "price": r.get("price"),
            "beds": r.get("beds"), "baths": r.get("baths"), "sqft": r.get("sqft"),
            "days_on_market": r.get("days_on_zillow"), "link": r.get("link"),
            "distance_miles": r.get("distance_miles"),
            "photos": _photos(r, extras.get(r["zpid"])),
            "designfit": {
                "score": fit.score, "verdict": fit.verdict,
                "ledger": [{"label": l.label, "points": l.points} for l in fit.ledger],
            },
            "agent": {
                "tier": attr.get("tier"), "name": attr.get("agent_name"),
                "brokerage": attr.get("brokerage"), "licence": attr.get("licence"),
                "phone": attr.get("phone"), "trec_status": attr.get("trec_status"),
                "reason": attr.get("reason"),
                "co_listers": json.loads(attr["co_listers"]) if attr.get("co_listers") else [],
            },
        })
    opportunities.sort(key=lambda o: -o["designfit"]["score"])

    n = len(snapshots)
    resolved_agent = sum(1 for a in current.values()
                         if a["tier"] in (CONFIRMED, INFERRED) and a.get("agent_key"))
    brokerage_only = sum(1 for a in current.values()
                         if a["tier"] == INFERRED and not a.get("agent_key"))
    unresolved = n - resolved_agent - brokerage_only

    return {
        "generated_ts": generated_ts,
        "market": {"name": cfg.name, "center_address": cfg.center_address,
                   "radius_miles": cfg.radius_miles, "price_floor": cfg.price_floor},
        "for": "Lindsey Goble — luxury interior design, business development",
        "counts": {"luxury_listings": n, "unique_agents": conc["unique_agents"],
                   "total_volume": total_volume},
        "concentration": conc,
        "coverage": {
            "resolved_to_agent": resolved_agent, "brokerage_only": brokerage_only,
            "unresolved": unresolved,
            "resolution_rate": (resolved_agent / n) if n else 0.0,
            "notes": [
                "Attribution is recovered from Google's index of the MLS 'Listed by' block "
                "and verified against the Texas licence register — calibrated at ~48% "
                "CONFIRMED / 80% actionable, not a guess where it is unsure.",
                "Photos are linked from Zillow, not embedded — they are the listing's "
                "copyright. The page needs a connection to show them.",
                "Counts describe only listings inside the "
                f"{cfg.radius_miles:g}-mile circle around {cfg.center_address}.",
            ],
        },
        "agents": agents,
        "opportunities": opportunities,
    }
