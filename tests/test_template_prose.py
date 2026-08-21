"""No template states a number the database owns. Enforced, because a rule is not a hope.

The original report's template said "Across 26 days", "and one increase", "since 11 July" and
"five sweeps". Every one of them was wrong within a fortnight, because a sentence in a template
does not know the database moved (docs/PORTING-THE-REPORTS.md, lesson 6). The rule that follows
is that windows, counts and dates live in the payload and the template's sentences interpolate
them — and the rule earns its keep only if something fails when it is broken.

So: over every template in the package, the *static* text — what is left after script blocks,
style blocks, comments and tags are removed — may not contain a month name, an "N days" or
"N sweeps" phrase, or an ISO date. Inside a `<script>` block all three are fine and expected:
that is where a payload value becomes a sentence.

The whitelist is deliberate rather than convenient. `WHITELIST` holds phrases that read like a
duration but are glossary prose ("days on market" as a column heading), and each entry is a
judgement someone made on purpose. Starting strict and whitelisting on purpose is the only
version of this test that stays useful; starting loose produces a regex that passes everything.
"""
import re

import pytest

from propertyfinder import pagebuild

MONTHS = (
    "January|February|March|April|May|June|July|August|September|October|November|December"
)

# Three shapes of drift, each one an actual sentence from the original's template.
PATTERNS = {
    "a month name": re.compile(rf"\b({MONTHS})\b"),
    "a bare duration": re.compile(r"\b\d+\s+(?:days?|sweeps?|weeks?|months?)\b", re.I),
    "an ISO date": re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),
}

# Phrases that match a pattern and are not drift. Nothing is here yet, which is the healthy
# state; an addition should come with a sentence saying why the phrase cannot go stale.
WHITELIST: tuple[str, ...] = ()

TEMPLATES = sorted(p.name for p in pagebuild.TEMPLATES_DIR.glob("*.html"))


def _static_text(source: str) -> str:
    """What a reader sees before any script runs — tags, scripts, styles and comments gone.

    Comments are stripped because a template's own commentary legitimately talks about dates
    (this repository's do, at length) and a reader never sees it.
    """
    stripped = re.sub(
        r"(?is)<script.*?</script>|<style.*?</style>|<!--.*?-->", " ", source
    )
    text = re.sub(r"(?s)<[^>]+>", " ", stripped)
    for phrase in WHITELIST:
        text = text.replace(phrase, " ")
    return text


def test_there_are_templates_to_check():
    """A glob that quietly matched nothing would make every test below vacuously pass."""
    assert len(TEMPLATES) >= 3


@pytest.mark.parametrize("name", TEMPLATES)
@pytest.mark.parametrize("label", sorted(PATTERNS))
def test_no_template_hardcodes_a_date_or_a_duration(name, label):
    text = _static_text((pagebuild.TEMPLATES_DIR / name).read_text())

    found = PATTERNS[label].search(text)
    assert found is None, (
        f"{name} states {label} in static text — {text[max(0, found.start() - 60):found.end() + 30].strip()!r}. "
        "Put the number in the payload and interpolate it, or whitelist the phrase here on purpose."
    )


def test_the_test_catches_the_sentence_that_taught_it(tmp_path, monkeypatch):
    """The original's own words, seeded into a template, must fail.

    Without this the test above proves only that today's templates happen to pass, which is
    also what the original's templates did on the day each sentence was written.
    """
    monkeypatch.setattr(pagebuild, "TEMPLATES_DIR", tmp_path)
    for drifted in (
        "<p>Across 26 days, prices fell.</p>",
        "<p>Cut since 11 July, over five sweeps.</p>",
        "<p>Swept 2026-08-21.</p>",
    ):
        text = _static_text(drifted)
        assert any(p.search(text) for p in PATTERNS.values()), drifted


def test_the_same_sentence_inside_a_script_block_is_fine():
    """Because that is where a payload value legitimately becomes a sentence."""
    text = _static_text(
        '<script>host.textContent = "Across " + w.window_days + " days, since July.";</script>'
    )

    assert not any(p.search(text) for p in PATTERNS.values())
