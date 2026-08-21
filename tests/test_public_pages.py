"""A page reachable without a password carries market assumptions and nothing personal.

The original tool's deal map embedded a finance block holding the owner's HELOC balance, their
current home's value, their monthly payment and their rental income. Fine on a laptop,
disqualifying on a URL. The public copy was produced by rebuilding through a neutral finance
configuration and then auditing the deployed page (docs/PORTING-THE-REPORTS.md, lesson 9).

This repository is already most of the way there by construction: `costmodel.FinanceAssumptions`
carries only market assumptions — a rate, a deposit, a term, an insurance factor, a tax rate with
its citation, a dues default, a district assessment — and the lending annex that would hold a
borrower profile is excluded from core by design. So most of what follows passes trivially today.

That is exactly what makes it a tripwire rather than a remediation. It exists for the day someone
wires the lending annex one import too close, or adds a field to a watch because it was convenient,
and it fails then rather than after a link has been shared. `test_a_doctored_payload_is_caught`
is the part that proves the tripwire is armed, since a test that only ever passes proves nothing.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest
from conftest import make_listing
from test_mapdata import WALSH_FINANCE, _config, _record
from test_newconreport import PRICE_LIST, T1, WATCH, _spec

from propertyfinder.config import FinanceAssumptions
from propertyfinder.costmodel import SpecialAssessment
from propertyfinder.newconreport import build_payload
from propertyfinder.pagebuild import render

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import build_site  # noqa: E402

# What a finance block on a public page is allowed to say: the market's assumptions, and the
# citation for the one of them that moved every monthly figure when it was corrected. Derived
# from the model rather than typed out, so adding a field to `FinanceAssumptions` is a decision
# this test participates in rather than one it silently ratifies.
ALLOWED = set(FinanceAssumptions.model_fields) | {"special_assessment"}
ALLOWED_ASSESSMENT = set(SpecialAssessment.model_fields)

# Key fragments that name a *person's* position rather than a market's. Not an exhaustive list
# of everything private — no such list exists — but the exact shapes the original leaked, plus
# the neighbouring ones a lending annex would introduce.
PERSONAL = (
    "heloc", "borrower", "credit", "equity", "payoff", "balance",
    "current_home", "rental_income", "monthly_payment", "savings", "salary",
    "net_worth", "household_income", "down_payment_available",
)


def _payload_of(page: str) -> dict:
    match = re.search(r'<script id="[\w-]+" type="application/json">(.*?)</script>', page, re.S)
    assert match, "a published page with no embedded payload cannot be audited at all"
    return json.loads(match.group(1).replace("<\\/", "</"))


def _personal_keys(node, path: str = "") -> list[str]:
    """Every key anywhere in a payload whose name describes a person's own position."""
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            here = f"{path}.{key}"
            if any(word in str(key).lower() for word in PERSONAL):
                found.append(here)
            found.extend(_personal_keys(value, here))
    elif isinstance(node, list):
        for i, value in enumerate(node):
            found.extend(_personal_keys(value, f"{path}[{i}]"))
    return found


def _audit(payload: dict) -> list[str]:
    """Everything wrong with this payload for publication. Empty is the passing answer."""
    problems = _personal_keys(payload)

    finance = payload.get("finance")
    if finance is None:
        return problems  # a page with no monthly column leaks no assumptions either
    for key in finance:
        if key not in ALLOWED:
            problems.append(f"finance.{key} is not a market assumption")
    for key in finance.get("special_assessment") or {}:
        if key not in ALLOWED_ASSESSMENT:
            problems.append(f"finance.special_assessment.{key} is not a district's own field")
    return problems


# -- the render a public page is actually built from -------------------------------------


def test_a_public_render_carries_the_models_own_neutral_assumptions(sessions):
    """`--public` is a flag, not a hand-edited config file, and this is what it produces.

    The watch's own block names a verified tax rate and a $3,271 improvement-district bill. A
    public render carries neither: it carries the model's defaults, which describe a market and
    not a household.
    """
    _record(sessions, [*PRICE_LIST, _spec("s1", "1 Oak Trail Dr", 600_000, 3000)], ts=T1)
    cfg = _config(WATCH, finance=WALSH_FINANCE)

    with sessions() as s:
        private = build_payload(s, WATCH, cfg, "2026-08-21T12:00:00Z")
        public = build_payload(s, WATCH, cfg, "2026-08-21T12:00:00Z", FinanceAssumptions())

    assert private["finance"]["special_assessment"]["flat_annual"] == 3271.0
    assert public["finance"]["special_assessment"]["flat_annual"] is None
    assert public["finance"]["tax_rate_citation"] == ""
    assert _audit(public) == []


def test_a_public_render_is_auditable_from_the_built_page_alone(sessions):
    """Because that is how the audit actually happens: on bytes someone can fetch."""
    _record(sessions, [*PRICE_LIST, _spec("s1", "1 Oak Trail Dr", 600_000, 3000)], ts=T1)
    with sessions() as s:
        payload = build_payload(
            s, WATCH, _config(WATCH, finance=WALSH_FINANCE), "2026-08-21T12:00:00Z",
            FinanceAssumptions(),
        )

    page = render("newcon.html", payload)
    recovered = _payload_of(page)

    assert _audit(recovered) == []
    # The watch's own verified rate and its district bill are what a public render drops. The
    # district's *published* schedule stays — it is curated research about a place, printed in
    # the assessment plan the section cites, and dropping it would make the page less honest
    # rather than more private. What must not survive is either figure standing as an
    # assumption every monthly number on the page was computed from.
    assert recovered["finance"]["default_tax_rate"] == FinanceAssumptions().default_tax_rate
    assert recovered["finance"]["special_assessment"]["flat_annual"] is None
    assert all(row["carry"]["assessment"] == 0 for row in recovered["specs"])


# -- the tripwire is armed ----------------------------------------------------------------


@pytest.mark.parametrize(
    "doctored, expect",
    [
        ({"finance": {"heloc_balance": 184_000}}, "heloc"),
        ({"finance": {"mortgage_rate": 6.5, "current_home_value": 720_000}}, "current_home"),
        ({"finance": {"mortgage_rate": 6.5, "monthly_payment": 3_140}}, "monthly_payment"),
        ({"specs": [{"zpid": "1", "rental_income": 3_200}]}, "rental_income"),
        ({"finance": {"mortgage_rate": 6.5, "lender_notes": "pre-approved"}}, "lender_notes"),
    ],
)
def test_a_doctored_payload_is_caught(doctored, expect):
    """One field at a time, each the shape the original actually published."""
    problems = _audit(doctored)

    assert problems, f"{doctored} passed an audit it should have failed"
    assert any(expect in problem for problem in problems), problems


def test_a_market_assumption_is_not_mistaken_for_a_personal_one():
    """The tripwire has to be quiet about the block it exists to permit, or it gets disabled."""
    assert _audit({"finance": FinanceAssumptions().model_dump()}) == []
    assert _audit({"finance": WALSH_FINANCE.model_dump()}) == []


# -- and every page the manifest actually publishes ---------------------------------------


def test_every_built_public_page_passes_the_audit():
    """Walks the committed manifest and audits whatever has actually been built.

    Skipped rather than failed when a page is absent, because `reports/` is not in git and a
    fresh clone has built nothing yet — a suite that failed on a clean checkout would be
    switched off, and a switched-off tripwire is no tripwire. On a machine that has run the
    pipeline, this is the real check on the real bytes.
    """
    entries = [
        e
        for e in build_site.load_manifest(build_site.MANIFEST_PATH, build_site.REPORTS_DIR)
        if e.visibility == "public"
    ]
    assert entries, "the manifest publishes nothing without a password"

    built = [e for e in entries if e.source.is_file()]
    if not built:
        pytest.skip("no public page has been built on this machine yet")

    for entry in built:
        problems = _audit(_payload_of(entry.source.read_text()))
        assert problems == [], f"{entry.dest}: {problems}"
