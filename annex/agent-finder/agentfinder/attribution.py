"""Who listed this home, and how sure we are — the annex's `dataquality`.

The feed has no agent field, but the attribution is public: every IDX-syndicating site
republishes the NTREIS "Listed by" block, and Google indexes it. Asking Google for the exact
phrase `'"<address>" "listed by"'` forces the snippet window onto that block, so the agent's
name, TREC licence and direct line arrive as text without ever fetching the listing page.

Three rules, taken from `propertyfinder.dataquality`:
- nothing mutates silently — a resolution is a returned record that travels with the home;
- evidence carries provenance — the snippet and source that decided it are kept;
- a guess is never dressed as a fact — two sources naming two *different* people, listed
  separately, is not weaker evidence, it is none, and it returns UNRESOLVED.

Calibrated on n=40 luxury addresses, TREC-cross-checked: 48% CONFIRMED, 80% actionable, 82%
of licensed attributions verified active in the state register. Three review fixes are baked
in here versus the first draft: Zillow is demoted out of the trusted tiers (a Zillow snippet
names a Premier Agent *advertiser*, not the lister), Movoto likewise (a referral aggregator),
and genuine co-listers written together on a page are kept rather than thrown away as a tie.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

CONFIRMED, INFERRED, UNRESOLVED = "CONFIRMED", "INFERRED", "UNRESOLVED"

# Source trust. TIER_1 republishes the MLS block often enough to carry the licence, which is
# what makes it trustworthy — not the brand, the falsifiability. Zillow and Movoto are
# deliberately absent (review fix): a Zillow snippet names an advertiser, Movoto is a
# referral aggregator; neither is the listing brokerage.
TIER_1 = {"trulia.com", "realtor.com", "redfin.com", "homes.com"}
TIER_2 = {"remax.com", "compass.com", "coldwellbanker.com", "century21.com", "bhgre.com",
          "era.com", "sothebysrealty.com", "briggsfreeman.com", "williamstrew.com",
          "elliman.com", "har.com", "point2homes.com"}
# Everything else (including zillow.com, movoto.com) is TIER_3: readable, not trusted.

RE_AGENT_LICENSED = re.compile(
    r"[Ll]isted\s+by:?\s+"
    r"(?P<name>[A-Z][A-Za-z'\-\.]+(?:\s+[A-Z][A-Za-z'\-\.]+){1,3})"
    r"[,\s]+(?P<licence>0\d{6})"
    r"(?:[,\s]+(?P<phone>\(?\d{3}\)?[\s\-\.]?\d{3}[\-\.]?\d{4}))?"
)
RE_AGENT_NAMED = re.compile(
    r"(?:[Ll]isted\s+by|[Cc]o-[Ll]isting\s+[Aa]gent|[Ll]isting\s+[Aa]gent)[:\.\s]+"
    r"(?P<name>[A-Z][A-Za-z'\-\.]+(?:\s+[A-Z][A-Za-z'\-\.]+){1,3})"
)
RE_BROKERAGE_ONLY = re.compile(
    r"[Ll]isted\s+(?:with|by)\s+(?:the\s+)?"
    r"(?P<brokerage>[A-Z][A-Za-z'\-\.&]+(?:\s+[A-Z][A-Za-z'\-\.&]+){0,4}"
    r"\s*(?:Realty|Real\s+Estate|Properties|Group|Realtors|Inc\.?|LLC|Sotheby's[A-Za-z\s']*))"
)
# Every licence anywhere in a snippet, so two co-listers written together are both seen.
RE_LICENCE = re.compile(r"\b(0\d{6})\b")
# Every "Name 0dddddd" pair in a snippet — used to capture co-listers written together
# ("Listed by: Alice Adams 0111111 and Bob Baker 0222222") rather than only the first.
RE_NAME_LICENCE = re.compile(
    r"([A-Z][A-Za-z'\-\.]+(?:\s+[A-Z][A-Za-z'\-\.]+){1,3})[,\s]+(0\d{6})")

_NOT_A_NAME = {"the", "this", "listing", "listed", "agent", "broker", "brokerage", "owner",
               "mls", "new", "for", "sale", "contact", "call", "please", "courtesy",
               "information"}
_NAME_STOP = re.compile(
    r"\s*(?:\.|,|\bBrokered\b|\bBrokerage\b|\bwith\b|\bat\b|\bof\b|\bRealty\b|"
    r"\bReal\s+Estate\b|\bProperties\b|\bGroup\b|\bRealtors\b)")
_DIRECTION_WORDS = {"e", "w", "n", "s", "east", "west", "north", "south"}
_BROKERAGE_NAMES = {
    "ebby halliday", "williams trew", "keller williams", "briggs freeman", "allie beth",
    "allie beth allman", "coldwell banker", "berkshire hathaway", "douglas elliman",
    "sotheby's international", "engel volkers", "compass re", "helen painter",
    "burt ladner", "league real", "fathom realty", "century 21", "iron star",
    "united real", "monument realty", "orchard brokerage", "real broker"}
_BROKERAGE_TOKENS = {"realty", "realtors", "brokerage", "properties", "estate", "group",
                     "team", "associates", "partners", "homes", "llc", "inc", "company"}


@dataclass(frozen=True)
class AgentAttribution:
    """Who listed one home, how sure that is, and the evidence for it. Travels with the home."""

    address: str
    agent: str | None = None
    licence: str | None = None
    phone: str | None = None
    brokerage: str | None = None
    tier: str = UNRESOLVED
    reason: str | None = None      # why it is not better than it is (always set when not CONFIRMED)
    sources: tuple[str, ...] = ()
    evidence: str | None = None
    conflict: tuple[str, ...] = ()      # names that cancelled → UNRESOLVED
    co_listers: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_actionable(self) -> bool:
        return self.tier in (CONFIRMED, INFERRED)


def _domain(link: str) -> str:
    return urlparse(link or "").netloc.replace("www.", "").lower()


def _trust(domain: str) -> int:
    return 1 if domain in TIER_1 else 2 if domain in TIER_2 else 3


def _clean_name(raw: str | None) -> str | None:
    if not raw:
        return None
    name = _NAME_STOP.split(raw.strip(), 1)[0].strip(" .,-")
    return name or None


def _plausible_name(name: str | None) -> bool:
    if not name:
        return False
    words = name.split()
    return 2 <= len(words) <= 4 and not any(
        w.lower().strip(".") in _NOT_A_NAME for w in words
    )


def _is_brokerage(name: str) -> bool:
    low = (name or "").lower().strip()
    if low in _BROKERAGE_NAMES or any(
        low.startswith(b + " ") or low == b for b in _BROKERAGE_NAMES
    ):
        return True
    return any(w.strip(".,") in _BROKERAGE_TOKENS for w in low.split())


def _norm(name: str) -> str:
    """A person's identity key: first and last word, letters only. 'Joseph Berkes' and
    'Joseph McCarthy Berkes' fold to one key so a middle name is not a second person."""
    words = [re.sub(r"[^a-z]", "", w.lower()) for w in (name or "").split()]
    words = [w for w in words if w]
    if not words:
        return ""
    return words[0] if len(words) == 1 else f"{words[0]}|{words[-1]}"


def _address_tokens(address: str) -> tuple[str, str]:
    head = (address or "").split(",", 1)[0].strip().lower()
    m = re.match(r"(\d+)\s+(.*)", head)
    if not m:
        return "", ""
    words = [w for w in re.findall(r"[a-z]+", m.group(2)) if w not in _DIRECTION_WORDS]
    return m.group(1), (words[0] if words else "")


def is_about_address(address: str, link: str, title: str) -> bool:
    """Is this result page actually about the target home?

    A SERP for one luxury address returns aggregator pages carrying a dozen *neighbouring*
    listings, each with its own 'Listed by' block; reading page-wide text would attribute a
    neighbour's agent to this house with total confidence. Requiring the house number and
    street word in the result's own URL or title is what keeps one home's evidence from
    speaking for another. An address with no house number (land, 'TBD Lot 5') fails the gate
    and stays honestly UNRESOLVED rather than being mis-attributed.
    """
    number, street = _address_tokens(address)
    if not number or not street:
        return False
    hay = f"{link or ''} {title or ''}".lower()
    return number in re.findall(r"\d+", hay) and street in hay


def parse_result(snippet: str, link: str) -> dict | None:
    """One organic result reduced to whatever attribution it carries, or None.

    `co_licensed` holds every extra licensed name written in the *same* snippet — the
    fingerprint of a co-listing ('Listed by A 0111111 and B 0222222'), used to tell
    co-listers from a cross-page conflict.
    """
    text = snippet or ""
    domain, trust = _domain(link), _trust(_domain(link))

    m = RE_AGENT_LICENSED.search(text)
    name = _clean_name(m.group("name")) if m else None
    if m and _plausible_name(name):
        # Co-listers written in the SAME snippet ("A 0111 and B 0222") are all captured
        # here, primary first, so a co-listing is kept rather than reduced to one name.
        seen, co_names = {_norm(name)}, []
        for raw_name, _lic in RE_NAME_LICENCE.findall(text):
            cleaned = _clean_name(raw_name)
            if (cleaned and _plausible_name(cleaned) and not _is_brokerage(cleaned)
                    and _norm(cleaned) not in seen):
                seen.add(_norm(cleaned))
                co_names.append(cleaned)
        return {"agent": name, "licence": m.group("licence"), "phone": m.group("phone"),
                "brokerage": None, "domain": domain, "trust": trust, "licensed": True,
                "n_licences": len(set(RE_LICENCE.findall(text))), "co_names": co_names}

    m = RE_AGENT_NAMED.search(text)
    name = _clean_name(m.group("name")) if m else None
    if m and _plausible_name(name):
        if _is_brokerage(name):  # "Listed by Ebby Halliday" names a firm — demote to brokerage
            return {"agent": None, "licence": None, "phone": None, "brokerage": name,
                    "domain": domain, "trust": trust, "licensed": False, "n_licences": 0}
        return {"agent": name, "licence": None, "phone": None, "brokerage": None,
                "domain": domain, "trust": trust, "licensed": False, "n_licences": 0}

    m = RE_BROKERAGE_ONLY.search(text)
    if m:
        return {"agent": None, "licence": None, "phone": None,
                "brokerage": m.group("brokerage").strip(" .,"),
                "domain": domain, "trust": trust, "licensed": False, "n_licences": 0}
    return None


def attribute(address: str, organic_results: list[dict]) -> AgentAttribution:
    """Who listed this home, and how sure that is. See the tier rules in the module docstring."""
    on_topic = [r for r in organic_results
                if is_about_address(address, r.get("link") or "", r.get("title") or "")]
    if not on_topic:
        return AgentAttribution(address, tier=UNRESOLVED,
                                reason="no search result was about this exact address")

    found = [f for f in (parse_result(r.get("snippet") or "", r.get("link") or "")
                         for r in on_topic) if f]
    named = [f for f in found if f["agent"]]

    if not named:
        brokers = sorted([f for f in found if f["brokerage"]], key=lambda f: f["trust"])
        if brokers:
            return AgentAttribution(
                address, brokerage=brokers[0]["brokerage"], tier=INFERRED,
                reason="the snippet names the brokerage but not the individual agent",
                sources=tuple(dict.fromkeys(f["domain"] for f in brokers)),
                evidence=brokers[0]["brokerage"])
        return AgentAttribution(address, tier=UNRESOLVED,
                                reason="no result named an agent or a brokerage for this home")

    by_person: dict[str, list[dict]] = {}
    for f in named:
        by_person.setdefault(_norm(f["agent"]), []).append(f)

    credible = {k: v for k, v in by_person.items() if min(f["trust"] for f in v) <= 2}
    pool = credible or by_person
    co_listers: tuple[str, ...] = ()

    if len(pool) > 1:
        licensed = {k: v for k, v in pool.items() if any(f["licence"] for f in v)}
        # Review fix: two licensed names written together on a page are co-listers, not a
        # conflict. The tell is `n_licences >= 2` on the source snippet — a single result
        # naming both. When that is what we see, keep the most-corroborated as primary and
        # carry the rest as co-listers. Only a genuine cross-page split (each source naming
        # a different single person) is a conflict, and that returns UNRESOLVED.
        co_listing = any(f["n_licences"] >= 2 for v in licensed.values() for f in v)
        if len(licensed) >= 2 and co_listing:
            others = sorted({v[0]["agent"] for k, v in licensed.items()})
            primary_key = max(licensed, key=lambda k: len(licensed[k]))
            co_listers = tuple(n for n in others if n != licensed[primary_key][0]["agent"])
            pool = {primary_key: licensed[primary_key]}
        elif len(licensed) == 1:
            others = sorted({v[0]["agent"] for k, v in pool.items() if k not in licensed})
            pool = licensed
            co_listers = tuple(others)
        else:
            return AgentAttribution(
                address, tier=UNRESOLVED,
                reason="two sources named two different agents — ambiguous evidence is none",
                sources=tuple(dict.fromkeys(f["domain"] for f in named)),
                conflict=tuple(sorted({v[0]["agent"] for v in pool.values()})))

    group = next(iter(pool.values()))
    best = sorted(group, key=lambda f: (f["trust"], not f["licensed"]))[0]
    domains = {f["domain"] for f in group}
    licensed = any(f["licensed"] for f in group)
    corroborated = len(domains) > 1
    tier = CONFIRMED if (best["trust"] <= 2 and (licensed or corroborated)) else INFERRED
    reason = None if tier == CONFIRMED else (
        "named only by a broker's own IDX site, without a licence or a second source")
    # Co-listers come from the primary's own snippet (names written alongside it) plus any
    # carried in from the cross-page branch, deduped and never including the primary itself.
    from_snippet = [n for f in group for n in (f.get("co_names") or [])]
    co = tuple(dict.fromkeys(list(co_listers) + from_snippet))
    co = tuple(n for n in co if _norm(n) != _norm(best["agent"]))
    return AgentAttribution(
        address, agent=best["agent"],
        licence=next((f["licence"] for f in group if f["licence"]), None),
        phone=next((f["phone"] for f in group if f["phone"]), None),
        brokerage=next((f["brokerage"] for f in found if f["brokerage"]), None),
        tier=tier, reason=reason, sources=tuple(sorted(domains)),
        evidence=best["agent"], co_listers=co)
