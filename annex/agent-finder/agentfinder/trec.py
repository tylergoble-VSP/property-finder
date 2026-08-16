"""Verify a parsed licence against the Texas Real Estate Commission's public register.

Free and authoritative: `data.texas.gov/resource/s7ft-44qi.json`, daily refresh, no key.
SB 510 (2023) stripped phone/email/address from the file, so TREC is not a *contact* source
— but it is the falsifiable check that turns a parsed name into a verified one. A snippet
licence `0549218` joins as `starts_with(license_number, '549218')` (leading zeros stripped).

The client is injected, like everywhere else, so the test suite never touches the network.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import httpx

RESOURCE = "https://data.texas.gov/resource/s7ft-44qi.json"


@dataclass(frozen=True)
class TrecCheck:
    """What the register said about one licence."""

    found: bool
    status: str | None = None          # e.g. "Active", "Expired"
    record_name: str | None = None     # the name TREC has on file
    brokerage: str | None = None       # the authoritative sponsoring broker, when present
    name_match: bool = False           # does the record name match the parsed name?

    @property
    def verified(self) -> bool:
        """Active licence whose name matches the parse — the ground-truth proxy."""
        return self.found and (self.status or "").lower().startswith(("active", "current")) \
            and self.name_match


def _name_key(name: str) -> tuple[str, str]:
    words = [re.sub(r"[^a-z]", "", w.lower()) for w in (name or "").split()]
    words = [w for w in words if w]
    return (words[0], words[-1]) if words else ("", "")


def verify(client: httpx.Client, licence: str | None, parsed_name: str | None) -> TrecCheck:
    if not licence:
        return TrecCheck(found=False)
    num = licence.lstrip("0")
    resp = client.get(RESOURCE, params={"$where": f"starts_with(license_number, '{num}')",
                                        "$limit": 5})
    rows = resp.json() if resp.status_code == 200 else []
    if not rows:
        return TrecCheck(found=False)
    row = rows[0]
    name = (row.get("license_full_name") or row.get("full_name")
            or row.get("licensee_name") or "")
    status = row.get("license_status") or row.get("status")
    brokerage = row.get("related_license_full_name") or row.get("broker_name")
    first, last = _name_key(parsed_name or "")
    rk = re.sub(r"[^a-z ]", " ", (name or "").lower())
    matched = bool(first) and first in rk and last in rk
    return TrecCheck(found=True, status=status, record_name=name or None,
                     brokerage=brokerage or None, name_match=matched)
