"""The buyer's brief: which of the homes in a watch a report is actually about.

A watch draws a circle on a map. A brief narrows that circle to the homes somebody would
actually buy — "four bedrooms and three thousand feet, in this ZIP, and a house rather
than a bare lot". They are deliberately different objects, and the brief is applied when
the **report is built**, never when the market is swept.

That ordering is the whole design. A sweep costs real money and cannot be undone; a report
is free and can be rebuilt a hundred times. Filtering at sweep time would mean the database
only ever held the homes matching the brief on the day it ran — so tightening the brief
would silently rewrite history, and loosening it would need a fresh purchase to answer a
question the tool had already paid for. Sweeping the whole ZIP and screening at render time
means one purchase answers every brief that market will ever be asked, including "what did
this filter throw away", which is a question the page can only answer while the rows are
still there.

**A number that is missing fails the test.** A home the feed never gave a square footage
cannot be shown to have three thousand of them, so it does not appear on a page promising
three thousand. It is counted, under its own reason, in `Screening.dropped` — because "no
home here is that big" and "the feed did not say how big eleven of them are" are different
statements about a market, and a page that renders the first while the second is true is
lying quietly. The reasons are counted rather than merely totalled for the same reason the
sweep logs a mis-resolved place string: the count is the maintenance signal. A brief that
drops forty homes for `sqft_unknown` is not a strict brief, it is a thin feed, and the
person reading the page is the one who needs to know which.

Nothing here reaches a database or a network. Rows in, verdict out — so the whole of it is
testable with dictionaries, and the report modules that call it stay pure reads.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from pydantic import BaseModel, Field, field_validator

__all__ = ["Criteria", "REASONS", "Screening", "screen"]

# The ZIP at the end of a feed address: "712 Lamar Ave, Crockett, TX 75835". Anchored to the
# end because a street name may contain digits ("3168 Fm 2076") and a five-digit road number
# is not a postal code.
_ZIP_TAIL = re.compile(r"\b(\d{5})(?:-\d{4})?\s*$")

# Why a home is not on the page, in the words the page prints. Keys travel in the payload;
# the sentences are here so that both the report and a terminal say the same thing.
REASONS: dict[str, str] = {
    "outside_zip": "in a different ZIP code",
    "zip_unknown": "no ZIP code in the address",
    "too_few_beds": "fewer bedrooms than the brief asks for",
    "beds_unknown": "no bedroom count in the feed",
    "too_small": "smaller than the brief asks for",
    "sqft_unknown": "no square footage in the feed",
    "wrong_home_type": "not a house — land, lots and multi-family",
}


def _get(row, key: str):
    """Read one field off whichever shape arrives — a store row, or an adapter `Listing`.

    The same accommodation `segments._address_of` makes, for the same reason: a brief is
    asked about rows read back out of the database *and* about records fresh off the feed,
    and neither shape should have to be converted to the other to answer it.
    """
    if isinstance(row, dict):
        return row.get(key)
    return getattr(row, key, None)


def _plain(n: float) -> str:
    """A number as a person writes it: 4 rather than 4.0, 3,000 rather than 3000.0."""
    return f"{int(n):,}" if float(n).is_integer() else f"{n:,}"


class Criteria(BaseModel):
    """What a report promises about every home on it. Every field is optional, and a brief
    that states nothing is the ordinary case — a watch reports its whole circle.

    `home_types` is an allowlist of the feed's own words (`SINGLE_FAMILY`, `FARM`,
    `MANUFACTURED`, …), matched case-insensitively. An allowlist rather than a blocklist
    because the feed invents new type words without notice, and the failure mode of an
    unknown word must be "this home is not on the page" rather than "this lot is".
    """

    zip: str | None = None
    min_beds: float | None = Field(default=None, gt=0)
    min_sqft: float | None = Field(default=None, gt=0)
    home_types: list[str] | None = Field(default=None, min_length=1)

    @field_validator("zip")
    @classmethod
    def _five_digits(cls, v: str | None) -> str | None:
        if v is not None and not re.fullmatch(r"\d{5}", v.strip()):
            raise ValueError(f"zip {v!r} must be five digits, e.g. '75835'")
        return None if v is None else v.strip()

    @property
    def declared(self) -> bool:
        """True when this brief actually narrows anything."""
        return any(
            v is not None
            for v in (self.zip, self.min_beds, self.min_sqft, self.home_types)
        )

    def describe(self) -> list[str]:
        """The brief as a reader's phrases, in the order a person would say them."""
        said: list[str] = []
        if self.min_beds is not None:
            said.append(f"{_plain(self.min_beds)}+ bedrooms")
        if self.min_sqft is not None:
            said.append(f"{_plain(self.min_sqft)}+ sq ft")
        if self.home_types is not None:
            said.append("houses only")
        if self.zip is not None:
            said.append(f"ZIP {self.zip}")
        return said

    def test(self, row) -> str | None:
        """None when this home belongs on the page, else the reason key it fails on.

        Checked cheapest and most disqualifying first, and only the *first* failure is
        reported: a bare lot that is also too small is one lot, and counting it twice would
        make the dropped tally disagree with the arithmetic (considered = kept + dropped).
        """
        if self.zip is not None:
            match = _ZIP_TAIL.search(str(_get(row, "address") or ""))
            if match is None:
                return "zip_unknown"
            if match.group(1) != self.zip:
                return "outside_zip"

        if self.home_types is not None:
            home_type = _get(row, "home_type")
            if not home_type:
                return "wrong_home_type"
            if str(home_type).strip().upper() not in {t.upper() for t in self.home_types}:
                return "wrong_home_type"

        if self.min_beds is not None:
            beds = _get(row, "beds")
            if beds is None:
                return "beds_unknown"
            if float(beds) < self.min_beds:
                return "too_few_beds"

        if self.min_sqft is not None:
            sqft = _get(row, "sqft")
            if sqft is None:
                return "sqft_unknown"
            if float(sqft) < self.min_sqft:
                return "too_small"

        return None


@dataclass(frozen=True)
class Screening:
    """What a brief did to a list of homes: what survived, and what did not, and why.

    `considered` is the count going in, so `considered == len(kept) + sum(dropped)` always
    holds and a page can state the shortlist as a fraction of the market rather than as a
    bare number floating free of what it was drawn from.
    """

    kept: list = field(default_factory=list)
    considered: int = 0
    dropped: dict[str, int] = field(default_factory=dict)
    criteria: Criteria = field(default_factory=lambda: Criteria())

    @property
    def n_dropped(self) -> int:
        return sum(self.dropped.values())

    def as_payload(self) -> dict:
        """The block a report embeds: the brief, the arithmetic, and the reasons in English.

        `declared: false` is a real answer rather than an absent key — a page built with no
        brief should say "every home in the circle" outright, not leave a reader inferring
        it from a missing section.
        """
        return {
            "declared": self.criteria.declared,
            "describe": self.criteria.describe(),
            "considered": self.considered,
            "kept": len(self.kept),
            "dropped": self.n_dropped,
            "reasons": [
                {"key": key, "why": REASONS[key], "n": self.dropped[key]}
                for key in sorted(self.dropped, key=lambda k: (-self.dropped[k], k))
            ],
        }


def screen(rows, criteria: Criteria | None) -> Screening:
    """Apply a brief to a list of homes. A brief of None (or an empty one) keeps everything.

    The empty brief returning every row *and* a `considered` count is what lets the calling
    report treat "there is a brief" and "there is not" identically — one code path, one
    payload shape, and no `if` in the template deciding whether a section exists.
    """
    rows = list(rows)
    criteria = criteria or Criteria()
    if not criteria.declared:
        return Screening(kept=rows, considered=len(rows), dropped={}, criteria=criteria)

    kept: list = []
    dropped: dict[str, int] = {}
    for row in rows:
        reason = criteria.test(row)
        if reason is None:
            kept.append(row)
        else:
            dropped[reason] = dropped.get(reason, 0) + 1
    return Screening(kept=kept, considered=len(rows), dropped=dropped, criteria=criteria)
