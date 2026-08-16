"""The luxury market to sweep: a centre, a radius, a price floor, and the place strings.

Separate from core's `watch-config.yaml` on purpose — this is the annex's own concern and
core never learns about it (docs/REBUILD.md item 2). The `"City, ST ZIP"` rule is inherited
in spirit from core: a bare ZIP mis-resolves (76008 → Minerva, Ohio), so a place string must
anchor the name.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator

_BARE_ZIP = re.compile(r"^\s*\d{5}(-\d{4})?\s*$")


class LuxuryConfig(BaseModel):
    """One luxury market: where its centre is, how far out to look, and how rich is rich."""

    name: str = "walsh-luxury-25mi"
    center_address: str
    lat: float
    lon: float
    radius_miles: float = Field(default=25.0, gt=0)
    # The luxury floor. Absolute dollars, not a percentile — the same figure is the 83rd
    # percentile in luxury-heavy ZIPs but ~96th region-wide, so a percentile is unstable.
    price_floor: float = Field(default=1_500_000, gt=0)
    max_pages: int = Field(default=3, ge=1)
    # The place strings that blanket the ring. The radius stays authoritative, so an
    # over-broad string self-trims; a string is only ever a way to reach inventory.
    queries: list[str] = Field(min_length=1)

    @field_validator("queries")
    @classmethod
    def _no_bare_zips(cls, qs: list[str]) -> list[str]:
        for q in qs:
            if _BARE_ZIP.match(q):
                raise ValueError(
                    f"query {q!r} is a bare ZIP. The provider mis-resolves bare ZIPs "
                    f"(observed: 76008 → Minerva, Ohio). Anchor it: 'Aledo, TX 76008'."
                )
        return qs


def load_luxury_config(path: str | Path = "agent-config.yaml") -> LuxuryConfig:
    return LuxuryConfig.model_validate(yaml.safe_load(Path(path).read_text()))
