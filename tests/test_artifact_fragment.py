"""One source, two builds — and the derivation only ever runs downhill.

The claude.ai artifact host injects the doctype, `<html>`, `<head>`, `<body>` and a CSS reset
around what it is given; a static host serves your bytes and nothing else. The talk deck was
authored as a fragment and then published at a URL, where it arrived with no reset, no metadata,
and a `<title>` in the body where a browser ignores it (docs/PORTING-THE-REPORTS.md, lesson 11).

So the full document is canonical and the fragment is derived. These tests hold both halves of
that: the derivation removes exactly what the host supplies and nothing else, and the canonical
document really does carry the things a static host needs.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import build_artifact_fragment as fragmentise  # noqa: E402

DECK = Path(__file__).resolve().parent.parent / "site-talk" / "index.html"

DOCUMENT = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>A Talk</title>
<meta property="og:image" content="https://example.test/og.png">
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg'><text>x</text></svg>">
<style>
/* reset — the artifact host supplies one; a standalone page must carry its own */
*{box-sizing:border-box}
</style>
</head>
<body>
<style>.slide { color: red; }</style>
<section class="slide">Slide one</section>
<script>const slides = 1;</script>
</body>
</html>
"""


def test_the_host_supplied_skeleton_comes_off():
    fragment, title = fragmentise.derive(DOCUMENT)

    assert title == "A Talk"
    for injected in ("<!doctype", "<html", "<head", "<body", "<title"):
        assert injected not in fragment.lower(), injected


def test_the_pages_own_reset_comes_off_and_its_real_styles_stay():
    fragment, _ = fragmentise.derive(DOCUMENT)

    assert "box-sizing:border-box" not in fragment  # the host's job
    assert ".slide { color: red; }" in fragment  # the deck's job
    assert "<script>const slides = 1;</script>" in fragment


def test_document_only_metadata_comes_off_whole():
    """The favicon is a `data:image/svg+xml,<svg …>` URI.

    A pattern that skipped to the first ">" would cut it in half and leave the tail of an SVG
    standing in the output as visible text — which is the sort of thing that only shows up
    after it has been pasted somewhere.
    """
    fragment, _ = fragmentise.derive(DOCUMENT)

    assert "og:image" not in fragment
    assert "viewport" not in fragment
    assert "svg" not in fragment.lower()


def test_the_content_survives_intact():
    fragment, _ = fragmentise.derive(DOCUMENT)

    assert "Slide one" in fragment
    assert fragment.count('class="slide"') == DOCUMENT.count('class="slide"')


def test_deriving_the_real_deck_keeps_every_slide():
    fragment, title = fragmentise.derive(DECK.read_text())
    canonical = DECK.read_text()

    assert title == "So Easy A Grunt Could Do It"
    assert fragment.count('class="slide') == canonical.count('class="slide')
    assert len(fragment) < len(canonical)
    for injected in ("<!doctype", "<html", "<head", "<body", "<title"):
        assert injected not in fragment.lower(), injected


# -- the canonical document carries what a static host needs -------------------------------


@pytest.mark.parametrize(
    "needed",
    [
        "<!doctype html>",  # the host would have injected it; a static host will not
        "reset — the artifact host supplies one",  # its own reset, carried on purpose
        'name="viewport"',
    ],
)
def test_the_canonical_deck_is_a_whole_document(needed):
    assert needed in DECK.read_text()


def test_the_deck_title_is_in_the_head_where_a_browser_reads_it():
    source = DECK.read_text()
    head = source[source.index("<head") : source.index("</head>")]

    assert "<title>" in head
    assert "<title>" not in source[source.index("</head>") :]


def test_every_social_url_on_the_deck_is_absolute():
    """A relative og:image passes every local check and then no preview ever renders."""
    urls = re.findall(
        r'<meta (?:property|name)="(?:og:image|og:url|twitter:image)" content="([^"]+)"',
        DECK.read_text(),
    )

    assert urls
    for url in urls:
        assert url.startswith("https://"), url


def test_the_stage_clips_so_the_page_cannot_scroll_sideways():
    """Inactive slides sit at translateX(26px) to slide in, which extended the document past
    the viewport at every width until the stage clipped. Found by rendering, kept by this."""
    source = DECK.read_text()
    stage = next(line for line in source.splitlines() if line.strip().startswith(".stage {"))

    assert "overflow: hidden" in stage


def test_the_height_query_that_fixed_twelve_slides_is_still_there():
    """Twelve of thirty-one slides overflowed at 1280×720; all thirty-one fitted at 1920×1080.

    The fix was a height query tightening vertical rhythm and capping figure heights, rather
    than per-slide surgery. `scripts/verify_page.py` proves it still works by rendering; this
    proves nobody deleted it (docs/PORTING-THE-REPORTS.md, lesson 14).
    """
    source = DECK.read_text()

    assert "@media (max-height: 820px)" in source
    block = source[source.index("@media (max-height: 820px)") :][:2000]
    assert "max-height" in block  # figures are capped, because a figure scales rather than reflows
