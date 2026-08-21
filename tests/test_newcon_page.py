"""The new-construction page, rendered: what a browser would find in it, checked without one.

Same division of labour `test_map_page.py` explains. There is no browser here — none offline,
and none needed — so nothing below watches the template's own script turn a payload into
builder cards and score ledgers. What this proves is everything decided at *build* time and
otherwise discoverable only by opening the file: the payload is embedded and reversible, both
vendored assets are in the bytes, no token survived, and the states the page has to survive —
a market mid-flight, a market swept once, a watch with nothing in it — each produce one whole
self-contained document.

The layer this deliberately does not cover — does the script actually run, does anything
overflow at 1280×720, did a placeholder leak into visible text — is
`scripts/verify_page.py`'s, which drives a real headless browser and is not a member of this
suite.
"""
import json
import re

from conftest import make_listing
from test_mapdata import WALSH_FINANCE, _config, _record
from test_newconreport import GENERATED, PRICE_LIST, T1, T2, WATCH, _spec

from propertyfinder import pagebuild
from propertyfinder.newconreport import build_payload
from propertyfinder.pagebuild import PAYLOAD_TOKEN, render

TEMPLATE = "newcon.html"


def _page(sessions, cfg=None):
    cfg = cfg or _config(WATCH, finance=WALSH_FINANCE)
    with sessions() as s:
        payload = build_payload(s, WATCH, cfg, GENERATED)
    return render(TEMPLATE, payload), payload


def _market(sessions):
    _record(
        sessions,
        [
            *PRICE_LIST,
            _spec("s1", "2404 Grand Gable Way, Fort Worth, TX 76008", 700_000, 3000, dom=140),
            _spec("s2", "2413 Red Hen Ln, Fort Worth, TX 76008", 620_000, 2900),
            make_listing("r1", address="2212 Dunstan Dr, Aledo, TX 76008", price=780_000, sqft=3600),
        ],
        ts=T1,
    )
    _record(
        sessions,
        [
            *PRICE_LIST,
            _spec("s1", "2404 Grand Gable Way, Fort Worth, TX 76008", 640_000, 3000, dom=161),
            _spec("s2", "2413 Red Hen Ln, Fort Worth, TX 76008", 620_000, 2900),
            make_listing("r1", address="2212 Dunstan Dr, Aledo, TX 76008", price=780_000, sqft=3600),
        ],
        ts=T2,
    )


# -- the vendored library ------------------------------------------------------------------


def test_the_page_carries_leaflet_in_its_own_bytes(sessions):
    _market(sessions)
    page, _ = _page(sessions)

    assert "Leaflet 1.9.4, a JS library for interactive maps" in page  # the js banner
    assert ".leaflet-container" in page  # ...and the stylesheet
    assert "{{VENDOR:" not in page
    assert "unpkg.com" not in page and "cdn.jsdelivr" not in page


def test_the_page_fetches_nothing_to_render_itself(sessions):
    """The claim `property-watch`'s own architecture doc made falsely: no CDN.

    A reader's browser fetches basemap tiles and nothing else. Looks for the four constructs
    that fetch rather than for "https", which legitimately appears in the tile URL, in
    attribution, in every builder link, and inside Leaflet's own stylesheet.
    """
    _market(sessions)
    page, _ = _page(sessions)

    assert "<script src" not in page and "<script  src" not in page
    assert 'rel="stylesheet"' not in page and "rel=stylesheet" not in page
    assert "@import" not in page
    assert "url(http" not in page and "url('http" not in page and 'url("http' not in page


# -- one whole document --------------------------------------------------------------------


def test_the_page_is_one_whole_document_with_the_payload_recoverable(sessions):
    _market(sessions)
    page, payload = _page(sessions)

    assert page.count("<html") == 1 and page.rstrip().endswith("</html>")
    assert PAYLOAD_TOKEN not in page
    start = page.index('id="nc-payload"')
    blob = page[page.index(">", start) + 1 : page.index("</script>", start)]
    assert json.loads(blob) == payload


def test_the_title_is_in_the_head_where_a_browser_reads_it(sessions):
    """An artifact host injects a head; a static host serves your bytes and nothing else.

    The talk deck shipped to Vercel with its `<title>` sitting in the body, where a browser
    ignores it (docs/PORTING-THE-REPORTS.md, lesson 11).
    """
    page, _ = _page(sessions)
    head = page[page.index("<head") : page.index("</head>")]

    assert "<title>" in head
    assert "<title>" not in page[page.index("</head>") :]


def test_every_social_url_is_absolute(sessions):
    """A relative og:url passes every local check and produces no preview at all."""
    page, _ = _page(sessions)

    urls = re.findall(r'<meta property="og:(?:url|image)" content="([^"]+)"', page)
    assert urls, "the page claims no canonical URL at all"
    for url in urls:
        assert url.startswith("https://"), url


def test_an_address_cannot_close_the_payload_tag_early(sessions):
    _record(
        sessions,
        [_spec("s1", "1 Main St</script><script>alert(1)", 500_000, 3000)],
        ts=T1,
    )
    page, _ = _page(sessions)

    assert "</script><script>alert" not in page


# -- the states the page has to survive ----------------------------------------------------


def test_a_market_mid_flight_carries_its_verdicts_ledgers_and_curated_research(sessions):
    _market(sessions)
    page, payload = _page(sessions)

    assert "2404 Grand Gable Way, Fort Worth, TX 76008" in page
    # the ledger's own words, which are what makes a score explainable rather than asserted
    assert "Starting point" in page and "Against the builder's ask" in page
    # curated research reached the payload, and therefore the page
    assert "Service and Assessment Plan" in page
    assert "Athletic Club" in page
    assert {r["verdict"] for r in payload["specs"]} & {"GREAT", "GOOD", "FAIR", "OVERPRICED"}
    assert payload["market"]["n_cuts"] == 1


def test_a_market_swept_once_says_history_begins_today(sessions):
    """No cut ledger is not an error and not a second template. The words are in the file."""
    _record(sessions, [*PRICE_LIST, _spec("s1", "1 Oak Trail Dr", 600_000, 3000)], ts=T1)
    page, payload = _page(sessions)

    assert payload["market"]["window"]["n_sweeps"] == 1
    assert '"months_supply":null' in page
    assert "History begins today." in page  # ready for the script to choose it
    assert 'id="nc-map"' in page  # the map is still there


def test_a_watch_with_nothing_in_it_still_opens(sessions):
    page, payload = _page(sessions)

    assert '"n_specs":0' in page
    assert page.count("<html") == 1
    assert "Athletic Club" in page  # the research does not depend on the market


# -- what the split is for ------------------------------------------------------------------


def test_the_template_file_contains_no_python():
    source = (pagebuild.TEMPLATES_DIR / TEMPLATE).read_text()
    for marker in ("import ", "def ", "session.execute", "from propertyfinder"):
        assert marker not in source, f"{marker!r} found in {TEMPLATE}"


def test_the_template_asks_for_exactly_one_payload_and_says_where_curated_data_ends():
    """One token, because two would silently duplicate a payload and none would ship an empty
    page. The original spliced three — payload, builders, plans — which is precisely how its
    curated research ended up in files a refresh could overwrite (lesson 5)."""
    source = (pagebuild.TEMPLATES_DIR / TEMPLATE).read_text()

    assert source.count(PAYLOAD_TOKEN) == 1
    assert "__BUILDERS__" not in source and "__PLANS__" not in source


def test_theme_is_defined_in_all_three_states(sessions):
    """The default "system" setting stamps nothing on the root, and that is most readers.

    A colour whose only definition sits inside a [data-theme] block never applies there
    (lesson 13). So: the complete palette on bare :root, redefined under the media query but
    guarded against an explicit light choice, and redefined again for an explicit dark one.
    """
    source = (pagebuild.TEMPLATES_DIR / TEMPLATE).read_text()

    assert re.search(r"\n  :root \{", source), "no unstamped palette"
    assert '@media (prefers-color-scheme: dark)' in source
    assert ':root:not([data-theme="light"])' in source
    assert ':root[data-theme="dark"]' in source
    # And the tile layer resolves through the same three states, and re-resolves on a change.
    assert 'getAttribute("data-theme")' in source
    assert 'addEventListener("change"' in source
