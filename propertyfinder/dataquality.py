"""The feed's known lies, written down as code.

Every detection in this module exists because it burned the original tool, late, in
research, after the bad number had already skewed a model and reached a page:

  - **Half-baths round up.** Systematically, in one direction. The July 2026 audit read
    all 68 Walsh plan rows against the builders' own plan pages and found 27 wrong, every
    one of them a whole bath where the plan sells a half. The tell is in the aggregate:
    across seventeen hundred stored homes the feed reported exactly two half-baths.
  - **Square footage is the base of a range.** A plan is sold from a floor plan with
    options; the feed publishes the smallest way it can be built and says nothing about
    the rest. One Walsh plan listed 4,121 square feet and runs to 4,896.
  - **One home, two listings.** A spec home mid-construction reappears under a second
    zpid at a differently-spelled address in a differently-named city — "1820 Crested
    Ridge Rd, Aledo" and "1820 Crested Rdg, Fort Worth" — carrying the identical price
    and the identical footage. Left alone it double-counts in every median and can occupy
    two slots on a leaderboard.
  - **There is no builder field.** The single most useful fact about a new-construction
    market is absent from the feed entirely, and the only honest way to recover it is to
    say how it was recovered. The researched half of that recovery lives in
    `data/builder-attribution.yaml` — 68 Walsh plan sheets and 40 standing spec homes, each
    carrying the basis it rests on — because a roster that lives only in a script's memory
    gets lost, and this one already was, once.

Three rules hold this module together.

**Nothing mutates silently.** A detection returns a finding; `assess` collects findings
into one `DataQuality` record per home; the record TRAVELS WITH the home into scoring and
into the page. `apply_corrections` returns a corrected *copy* and never touches the row it
was given, so the listed value and the verified value both survive and a reader can be
shown both. The database keeps what the feed said — an observation is history, and
history is not edited in place.

**Corrections are data, with provenance.** They live in `propertyfinder/data/*.yaml` with
a source and a date per entry, and each carries the value the feed showed *when it was
verified*. If the feed no longer says that, the correction is stale and is not applied:
a correction that no longer describes reality is one to re-verify, not to trust.

**A guess is never dressed as a fact.** Builder attribution returns a confidence tier —
RESEARCHED when a person read the evidence and recorded where, CONFIRMED when the builder
is named in the listing's own text, INFERRED when the home matches a known plan exactly,
UNRESOLVED when none of those, including when two builders match and the evidence therefore
proves nothing.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import Iterable, Mapping, Sequence

import yaml

from propertyfinder.newcon import is_plan_sheet, plan_community, plan_name

DATA_DIR = Path(__file__).parent / "data"

# Flag vocabulary. Strings rather than an enum because these travel into a JSON payload
# and out onto a page, and a name a reader can understand is the point of them.
BATHS_CORRECTED = "baths_corrected"
BATH_CORRECTION_STALE = "bath_correction_stale"
SUSPECT_HALF_BATH_ROUNDUP = "suspect_half_bath_roundup"
SQFT_IS_BASE_OF_RANGE = "sqft_is_base_of_range"
DUPLICATE_LISTING = "duplicate_listing"

# Builder attribution tiers, strongest first. RESEARCHED sits above CONFIRMED because a
# person read the evidence and wrote down where they read it, in a file git keeps; CONFIRMED
# is the same claim made by a scraped string this tool happened to see once. Both are better
# than INFERRED, which is a match rather than a statement, and all three are better than
# UNRESOLVED, which is this module refusing to guess.
RESEARCHED, CONFIRMED, INFERRED, UNRESOLVED = (
    "RESEARCHED",
    "CONFIRMED",
    "INFERRED",
    "UNRESOLVED",
)

# The `basis` values `builder-attribution.yaml` records, and the tier each one earns. A
# researched entry does not automatically outrank a heuristic: an entry whose evidence was
# itself a plan-name match resolves as INFERRED, because that is what it is. See that
# file's own header.
_BASIS_TIERS = {
    "description": RESEARCHED,
    "plan-match": INFERRED,
}

# How alike two street names must read before two rows sharing a price and a footage are
# called one home. "crested ridge" against "crested rdg" scores about 0.92; two genuinely
# different streets do not come close.
STREET_SIMILARITY = 0.82

# The abbreviations the feed alternates between within a single sweep, folded so that two
# spellings of one street compare as one. This is a different question from the one
# `segments._street_key` answers — that one asks whether an address is on an allowlist and
# wants the spelling the allowlist uses; this one asks whether two spellings are the same
# place, which is why it folds and that one must not.
_ABBREVIATIONS = {
    "rd": "road", "st": "street", "dr": "drive", "ln": "lane", "cir": "circle",
    "ct": "court", "ave": "avenue", "trl": "trail", "blvd": "boulevard",
    "pkwy": "parkway", "ter": "terrace", "hwy": "highway",
    "rdg": "ridge", "crk": "creek", "vly": "valley", "mdw": "meadow", "hls": "hills",
    "spgs": "springs", "mnr": "manor", "xing": "crossing", "hllw": "hollow",
}

# Street *types* only — dropped before comparison, because "Crested Rdg" and "Crested
# Ridge Rd" are one street written with and without its type. Name words that merely look
# like types stay: "Ridge", "Creek" and "Bend" are what tells Crested Ridge from Crested
# Creek, and a comparison that discarded them would merge two real streets.
_STREET_TYPES = {
    "road", "street", "drive", "lane", "circle", "court", "avenue", "trail",
    "boulevard", "parkway", "terrace", "highway", "way", "place",
}

# Kept out of the name comparison and checked separately: two arms of one loop street are
# different streets, and their names are otherwise identical.
_DIRECTIONS = {
    "e": "east", "w": "west", "n": "north", "s": "south",
    "ne": "northeast", "nw": "northwest", "se": "southeast", "sw": "southwest",
    "east": "east", "west": "west", "north": "north", "south": "south",
}


@dataclass(frozen=True)
class DataQuality:
    """What is known to be wrong with one home's record, and what to do about it.

    `corrections` maps a field name to a plain dictionary carrying at least the listed
    value and, where a person verified one, the value that is actually true — plus the
    source and date that verified it. It stays a plain dictionary because it is rendered
    on a page and serialised into a payload, and a reader is owed "listed 5, verified 4.5,
    builder plan page, 2026-07-26" rather than a class name.
    """

    zpid: str
    flags: tuple[str, ...] = ()
    corrections: dict[str, dict] = field(default_factory=dict)
    builder: str | None = None
    builder_tier: str = UNRESOLVED
    duplicate_of: str | None = None

    @property
    def is_duplicate(self) -> bool:
        return self.duplicate_of is not None

    def has(self, flag: str) -> bool:
        return flag in self.flags

    def corrected(self, field_name: str) -> float | None:
        """The verified value for a field, or None when nothing verified it."""
        return (self.corrections.get(field_name) or {}).get("verified")


# -- the correction files ---------------------------------------------------------------


@lru_cache(maxsize=None)
def bath_corrections() -> Mapping[str, dict]:
    """Verified bath counts, keyed by plan address or zpid. See the file's own header."""
    return _load("bath-corrections.yaml", "baths")


@lru_cache(maxsize=None)
def builder_attributions() -> Mapping[str, dict]:
    """The researched builder roster, keyed by plan address or by zpid.

    One flat mapping over both of the file's sections, the same shape `bath_corrections`
    has, because the lookup asks the same question in the same two ways: does anything
    know about this exact home, and failing that, does anything know about its plan sheet.
    The two key spaces cannot collide — a zpid is digits and a plan address is not.
    """
    merged: dict[str, dict] = {}
    for section in ("plans", "specs"):
        merged.update(_load("builder-attribution.yaml", section))
    return MappingProxyType(merged)


@lru_cache(maxsize=None)
def plan_sqft_ranges() -> Mapping[str, dict]:
    """What a plan's footage runs to, keyed by plan address. See the file's own header."""
    return _load("plan-sqft-ranges.yaml", "ranges")


def _load(filename: str, section: str) -> Mapping[str, dict]:
    path = DATA_DIR / filename
    if not path.exists():  # a deployment with no corrections yet is not an error
        return MappingProxyType({})
    raw = yaml.safe_load(path.read_text()) or {}
    # Read-only: these are cached module-wide, and a caller that mutated one would be
    # editing every later caller's facts.
    return MappingProxyType({str(k): dict(v) for k, v in (raw.get(section) or {}).items()})


# -- (a) the half-bath round-up ---------------------------------------------------------


def bath_correction(row: dict, corrections: Mapping[str, dict]) -> tuple[dict | None, bool]:
    """The verified bath count for this home, and whether the correction has gone stale.

    Looked up by zpid first — a correction aimed at one home beats one aimed at its plan —
    then by the address the feed writes. Staleness is the guard: if the feed no longer
    reports the value that was verified against, something changed on one side or the
    other and applying an old correction would be inventing a number.
    """
    for key in (str(row.get("zpid") or ""), (row.get("address") or "")):
        entry = corrections.get(key) if key else None
        if not entry:
            continue
        listed, baths = entry.get("listed"), row.get("baths")
        stale = listed is not None and baths is not None and float(baths) != float(listed)
        return dict(entry), stale
    return None, False


def half_bath_suspects(rows: Iterable[dict]) -> set[str]:
    """Plan rows whose whole-number bath count the feed probably rounded up.

    The heuristic is a proof by the feed's own hand: within one community's price list, a
    single plan carrying a half-bath shows that the feed *can* express one there. Every
    whole-number sibling in that same price list is then suspect — not wrong, suspect,
    which is a flag and an invitation to verify, never a correction.
    """
    by_community: dict[str, list[dict]] = {}
    for row in rows:
        if is_plan_sheet(row) and row.get("baths") is not None:
            by_community.setdefault(plan_community(row.get("address")) or "", []).append(row)

    suspects: set[str] = set()
    for plans in by_community.values():
        if not any(float(p["baths"]) % 1 == 0.5 for p in plans):
            continue
        suspects.update(
            str(p["zpid"]) for p in plans if float(p["baths"]) % 1 == 0 and p.get("zpid")
        )
    return suspects


# -- (c) one home, two listings ---------------------------------------------------------


def _street_parts(address: str | None) -> tuple[str, str | None]:
    """An address reduced to its street name and its direction, if it has one.

    "1820 Crested Rdg, Fort Worth, TX" and "1820 Crested Ridge Rd, Aledo, TX" both come
    back as ("crested ridge", None) — abbreviations expanded, type word dropped, city and
    state left behind, which is the whole trick to recognising one home written twice.
    """
    raw = (address or "").split(",", 1)[0].strip().lower()
    raw = re.sub(r"^\d+\s+", "", raw)
    words = [_ABBREVIATIONS.get(w, w) for w in re.findall(r"[a-z]+", raw)]
    direction = next((_DIRECTIONS[w] for w in words if w in _DIRECTIONS), None)
    name = [w for w in words if w not in _STREET_TYPES and w not in _DIRECTIONS]
    # A street named only by its type ("The Circle") keeps it rather than vanishing.
    return " ".join(name or words), direction


def _house_number(address: str | None) -> str | None:
    match = re.match(r"^\s*(\d+)", (address or "").split(",", 1)[0])
    return match.group(1) if match else None


def same_address(a: str | None, b: str | None) -> bool:
    """Two spellings of one address?

    Both halves have to agree: the same house number AND a street name that reads the same
    once abbreviations are folded. The conjunction is deliberate and was learned the hard
    way — a builder routinely offers two identical spec homes on one street at the same
    price and the same footage (14204 and 14217 Fountainhead Cir, both $999,900 and 3,522
    feet, in the seed data), and a rule that accepted a street-name match alone would
    delete one of two real houses. Conflicting directions are a mismatch for the same
    reason: the east and west arms of a loop street are not one street.
    """
    if _house_number(a) != _house_number(b):
        return False
    name_a, dir_a = _street_parts(a)
    name_b, dir_b = _street_parts(b)
    if not name_a or not name_b:
        return False
    if dir_a and dir_b and dir_a != dir_b:
        return False
    return SequenceMatcher(None, name_a, name_b).ratio() >= STREET_SIMILARITY


def _completeness(row: dict) -> int:
    """How much of a record is filled in — the tiebreak for which twin is the real one."""
    return sum(
        1
        for key in ("address", "beds", "baths", "sqft", "lot_sqft", "lat", "lon",
                    "year_built", "link", "image_url", "days_on_zillow")
        if row.get(key) is not None
    )


def find_duplicates(rows: Iterable[dict]) -> dict[str, str]:
    """Map each duplicate row's zpid to the zpid of the listing it duplicates.

    The signature of the real incident is an identical price AND an identical footage —
    two numbers a builder does not coincidentally repeat at the same address — so the
    grouping is exact on both and only near-matching addresses are ever compared. Sold
    rows are left out: a home appearing once for sale and once sold is history working
    correctly, not a double listing.

    The survivor is the more complete record, and the older one where completeness ties;
    the newer, thinner twin is the one flagged, because it is the one the feed invented.
    """
    groups: dict[tuple, list[dict]] = {}
    for row in rows:
        if row.get("listing_status") == "sold":
            continue
        if row.get("price") is None or row.get("sqft") is None or not row.get("zpid"):
            continue
        groups.setdefault((row["price"], row["sqft"]), []).append(row)

    duplicates: dict[str, str] = {}
    for members in groups.values():
        if len(members) < 2:
            continue
        # Best record first, so every twin that matches it is measured against one keeper.
        ranked = sorted(
            members,
            key=lambda r: (-_completeness(r), r.get("first_seen") or "", str(r["zpid"])),
        )
        claimed: set[str] = set()
        for keeper in ranked:
            if str(keeper["zpid"]) in claimed:
                continue
            for other in ranked:
                zpid = str(other["zpid"])
                if zpid == str(keeper["zpid"]) or zpid in claimed:
                    continue
                if same_address(keeper.get("address"), other.get("address")):
                    duplicates[zpid] = str(keeper["zpid"])
                    claimed.add(zpid)
    return duplicates


# -- (d) who built it -------------------------------------------------------------------


def _free_text(row: dict) -> str:
    """Whatever prose the record carries.

    The search feed carries none — there is no description field in a search result — so
    CONFIRMED attribution is reachable only for a row a caller has joined to a detail
    body. That is the honest position: the strongest tier is available exactly when the
    evidence for it is, and the ordinary path through this function ends in INFERRED or
    UNRESOLVED.
    """
    return " ".join(str(row.get(key) or "") for key in ("description", "status_text")).lower()


def researched_builder(
    row: dict, attributions: Mapping[str, dict]
) -> tuple[str | None, str] | None:
    """What the researched roster says about this row — or None when it says nothing.

    Looked up by zpid first and then by the address the feed writes, the same order and for
    the same reason as `bath_correction`: an entry aimed at one home beats one aimed at its
    plan sheet.

    Three outcomes, and the third is the point of the file existing. A `description`-basis
    entry resolves RESEARCHED; a `plan-match`-basis entry resolves INFERRED, because that
    is honestly all it is; and an entry recording that the evidence points at two builders
    resolves `(None, UNRESOLVED)` — which is not the same as "nothing known", because it
    also stops the heuristics from picking one. `None` (nothing known) is the only return
    that lets them run.

    A stale entry — one whose recorded plan name, community or address the feed no longer
    agrees with — returns `None` rather than its builder. Same doctrine as a stale bath
    correction: an attribution that no longer describes the row it keys is one to
    re-verify, not one to trust.
    """
    for key in (str(row.get("zpid") or ""), (row.get("address") or "")):
        entry = attributions.get(key) if key else None
        if entry is None:
            continue
        if _attribution_is_stale(row, entry):
            return None
        if entry.get("candidates"):
            return None, UNRESOLVED
        builder = entry.get("builder")
        if not builder:
            return None  # a person looked and found nothing; let the heuristics try
        return builder, _BASIS_TIERS.get(entry.get("basis"), INFERRED)
    return None


def _attribution_is_stale(row: dict, entry: Mapping) -> bool:
    """Does the feed still say what this entry was verified against?

    A plan entry is guarded by the plan name and community the address decomposes into; a
    spec entry by the address itself. A guard the entry does not carry cannot fail — an
    entry written before the guard existed is trusted rather than silently dropped.
    """
    address = row.get("address")
    recorded_address = entry.get("address")
    if recorded_address and address and recorded_address != address:
        return True
    for field, actual in (
        ("plan_name", plan_name(address)),
        ("community", plan_community(address)),
    ):
        recorded = entry.get(field)
        if recorded and actual and recorded != actual:
            return True
    return False


def attribute_builder(
    row: dict,
    plans_by_builder: Mapping[str, Sequence[dict]],
    attributions: Mapping[str, dict] | None = None,
) -> tuple[str | None, str]:
    """Who built this home, and how sure that is — (builder, tier).

    RESEARCHED: the roster in `data/builder-attribution.yaml` names the builder on the
              strength of evidence a person read and recorded the source of. Consulted
              first, before any heuristic runs, because it is the only tier whose provenance
              survives in git rather than in a scrape.
    CONFIRMED: the builder names itself in the listing's own text.
    INFERRED: the home matches a known plan sheet exactly, by plan name or by footage — or
              the roster records exactly that match as its own basis.
              An exact footage match is strong in new construction precisely because a
              plan is built to a spec sheet; it is not a resale coincidence.
    UNRESOLVED: no evidence, or evidence pointing at two builders — which is not weaker
              evidence, it is none, and it is returned as none.

    `attributions` defaults to nothing researched rather than to the file on disk, the same
    way `plans_by_builder` defaults to nothing known: this is a pure function of what it is
    handed. `assess` is where the file gets read.
    """
    researched = researched_builder(row, attributions or {})
    if researched is not None:
        return researched

    text = _free_text(row)
    named = {b for b in plans_by_builder if b.strip() and b.lower() in text}
    if len(named) == 1:
        return named.pop(), CONFIRMED
    if len(named) > 1:
        return None, UNRESOLVED

    name = (row.get("plan_name") or plan_name(row.get("address")) or "").strip().lower()
    if name:
        by_name = {
            builder
            for builder, plans in plans_by_builder.items()
            for plan in plans
            if (plan.get("plan_name") or plan_name(plan.get("address")) or "").strip().lower()
            == name
        }
        if len(by_name) == 1:
            return by_name.pop(), INFERRED
        if len(by_name) > 1:
            return None, UNRESOLVED

    sqft = row.get("sqft")
    if sqft:
        by_sqft = {
            builder
            for builder, plans in plans_by_builder.items()
            for plan in plans
            if plan.get("sqft") is not None and float(plan["sqft"]) == float(sqft)
        }
        if len(by_sqft) == 1:
            return by_sqft.pop(), INFERRED

    return None, UNRESOLVED


# -- the whole assessment ---------------------------------------------------------------


def assess(
    rows: Sequence[dict],
    corrections: Mapping[str, dict] | None = None,
    sqft_ranges: Mapping[str, dict] | None = None,
    plans_by_builder: Mapping[str, Sequence[dict]] | None = None,
    attributions: Mapping[str, dict] | None = None,
) -> dict[str, DataQuality]:
    """One `DataQuality` record per home, keyed by zpid.

    Takes the whole set rather than one row at a time because two of the four detections
    are about a home's neighbours: a duplicate needs its twin, and a suspect bath count
    needs the sibling plan that proves the feed can print a half. Everything here is pure
    — rows in, findings out, no database, no network, no mutation.

    `plans_by_builder` defaults to nothing known, and nothing known yields UNRESOLVED for
    every home, which is the correct answer for a market whose builders nobody has mapped.
    `attributions` is the one input that defaults to *disk*: the researched roster is a fact
    about this repository rather than something a caller assembles, and a caller that had to
    remember to pass it is a caller that will one day forget and silently lose the roster.
    Pass `{}` to assess as though nobody had researched anything.
    """
    corrections = bath_corrections() if corrections is None else corrections
    sqft_ranges = plan_sqft_ranges() if sqft_ranges is None else sqft_ranges
    attributions = builder_attributions() if attributions is None else attributions
    plans_by_builder = plans_by_builder or {}

    duplicates = find_duplicates(rows)
    suspects = half_bath_suspects(rows)

    assessed: dict[str, DataQuality] = {}
    for row in rows:
        zpid = str(row.get("zpid") or "")
        if not zpid:
            continue
        flags: list[str] = []
        found: dict[str, dict] = {}

        entry, stale = bath_correction(row, corrections)
        if entry is not None:
            found["baths"] = {**entry, "verified": None if stale else entry.get("verified")}
            flags.append(BATH_CORRECTION_STALE if stale else BATHS_CORRECTED)
        elif zpid in suspects:
            flags.append(SUSPECT_HALF_BATH_ROUNDUP)

        if is_plan_sheet(row):
            flags.append(SQFT_IS_BASE_OF_RANGE)
            span = sqft_ranges.get(row.get("address") or "")
            if span:
                found["sqft"] = {
                    "listed": row.get("sqft"),
                    "sqft_max": span.get("max"),
                    "source": span.get("source"),
                    "verified_on": span.get("verified_on"),
                }

        duplicate_of = duplicates.get(zpid)
        if duplicate_of:
            flags.append(DUPLICATE_LISTING)

        builder, tier = attribute_builder(row, plans_by_builder, attributions)
        assessed[zpid] = DataQuality(
            zpid=zpid,
            flags=tuple(flags),
            corrections=found,
            builder=builder,
            builder_tier=tier,
            duplicate_of=duplicate_of,
        )
    return assessed


def apply_corrections(row: dict, quality: DataQuality | None) -> dict:
    """The row as it should have arrived — as a copy. The original is never touched.

    A verified bath count replaces the listed one, because it is simply the truth about
    the home. A plan's footage range is *added* as `sqft_max` and the listed footage is
    left standing, because the base is a true number about a real build and the range is
    an additional fact, not a contradiction of it.
    """
    corrected = dict(row)
    if quality is None:
        return corrected

    baths = quality.corrected("baths")
    if baths is not None:
        corrected["baths"] = baths

    sqft_max = (quality.corrections.get("sqft") or {}).get("sqft_max")
    if sqft_max is not None:
        corrected["sqft_max"] = sqft_max
    return corrected
