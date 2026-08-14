"""Subdivision membership — narrow a watch to a named neighbourhood, not just a circle.

A radius cannot isolate a subdivision: a circle wide enough to cover all of Walsh also
scoops up neighbouring Aledo communities, and Walsh's streets carry the name "Walsh" on
almost none of them — only Walsh Ave and the builder plan sheets say it outright.
Membership is decided by three signals, cheapest and most reliable first, none of them
costing a network call at sweep time:

  1. **Plan-sheet community** — new-construction rows name the community in the address
     ("Camborne Plan, Walsh Cottage"). Authoritative for builder inventory.
  2. **Street allowlist** — the enumerated streets of the subdivision. The workhorse for
     resale and spec homes, whose addresses never say the community's name.
  3. **Address token** — a literal alias appearing in the address ("... Walsh Ave ...").
     A backstop for whatever the allowlist has not caught up with yet.

The `zillow_property` detail engine's own `subdivision_name` field would be the obvious
fourth signal, but it is absent on roughly four pulls in five and on all new
construction — too sparse to be the live filter. `subdivision_name_matches` exists to
reconcile it against the allowlist offline, on the rare pull where it does show up.

The allowlist itself is data, not code: `propertyfinder/data/<key>-streets.yaml`. See
that file's header for how to triage a listing the filter drops — `sweep.collect_in_radius`
logs the count whenever this filter removes an in-radius listing, and that count is the
maintenance signal that a street is missing.

Geometry runs first and membership second (`sweep.collect_in_radius`, Stage 9) — a
same-named street in a different town is excluded by the radius before this module is
ever asked about it, so this module only has to answer "is this address a member",
never "is this address nearby".
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

DATA_DIR = Path(__file__).parent / "data"


@dataclass(frozen=True)
class Subdivision:
    """One neighbourhood's membership rules, loaded whole from its YAML file."""

    key: str
    streets: frozenset[str]
    plan_prefixes: tuple[str, ...] = ()
    subdivision_name_patterns: tuple[str, ...] = ()

    def matches_plan(self, address: str) -> bool:
        m = re.search(r"Plan,\s*(.+)$", address or "")
        if not m:
            return False
        community = m.group(1).strip().lower()
        return any(community.startswith(p) for p in self.plan_prefixes)


@lru_cache(maxsize=None)
def get_subdivision(key: str) -> Subdivision:
    """The named subdivision's rules, read from `propertyfinder/data/<key>-streets.yaml`.

    Cached: the file is small and never changes mid-process, and a sweep asks this
    question once per listing.
    """
    path = DATA_DIR / f"{(key or '').strip().lower()}-streets.yaml"
    if not path.exists():
        raise KeyError(f"unknown subdivision {key!r}; expected a file at {path}")
    raw = yaml.safe_load(path.read_text())
    return Subdivision(
        key=raw["key"],
        streets=frozenset(raw.get("streets") or ()),
        plan_prefixes=tuple(raw.get("plan_prefixes") or ()),
        subdivision_name_patterns=tuple(raw.get("subdivision_name_patterns") or ()),
    )


def _address_of(listing_or_row) -> str:
    """Read `address` off whichever shape arrives — a `Listing`, or a plain store row.

    Both `collect_in_radius` (a `Listing` fresh off the adapter) and anything reading
    history back out of `store.latest_snapshot_rows` (a plain dict) need to ask this same
    question, and neither shape carries a separate `street` field in this tool — the
    address is the only place a street name lives.
    """
    if isinstance(listing_or_row, dict):
        return listing_or_row.get("address") or ""
    return getattr(listing_or_row, "address", None) or ""


def _street_key(address: str) -> str | None:
    """Normalized street name: the segment before the first comma, house number and
    surrounding whitespace stripped, lowercased."""
    raw = (address or "").split(",", 1)[0].strip()
    if not raw:
        return None
    raw = re.sub(r"^\d+\s+", "", raw)  # drop a leading house number
    raw = re.sub(r"\s+", " ", raw).strip().lower()
    return raw or None


def in_subdivision(listing_or_row, key: str) -> bool:
    """Is this home a member of subdivision `key`? Plan community, then street
    allowlist, then address token — cheapest and most reliable signal first, no network
    call. Raises `KeyError` for a subdivision with no matching data file.
    """
    sub = get_subdivision(key)
    addr = _address_of(listing_or_row)
    if sub.matches_plan(addr):
        return True
    street = _street_key(addr)
    if street and street in sub.streets:
        return True
    # Backstop: the subdivision's own name appearing literally in the address — catches a
    # "Walsh Ave" row and any "...Walsh..." Zillow tacks on — but a plan row for a
    # *different* community was already ruled out above, so this cannot readmit one.
    if "Plan," not in addr and re.search(rf"\b{re.escape(sub.key)}\b", addr, re.IGNORECASE):
        return True
    return False


def subdivision_name_matches(subdivision_name: str | None, key: str) -> bool:
    """Map the detail engine's own `subdivision_name` back to a key — offline
    reconciliation of the allowlist only, never the live filter (see module docstring)."""
    if not subdivision_name:
        return False
    sub = get_subdivision(key)
    return any(
        re.search(pattern, subdivision_name, re.IGNORECASE)
        for pattern in sub.subdivision_name_patterns
    )
