"""The map page, rendered: what a browser would find in it, checked without one.

There is no browser here — none offline, and none needed — so nothing below watches the
template's own script turn a payload into markers and cards. What it proves is everything
that is decided at *build* time and would otherwise only be discovered by opening the file:
the payload is embedded and reversible, both vendored assets are in the bytes, no token
survived, and the two states the page has to survive — scored and unscored — both produce
one whole self-contained document.

The rest of the page's behaviour is proved where behaviour lives: `test_mapdata` holds the
numbers, and the template holds the pixels.
"""
import json
from pathlib import Path

import pytest
from conftest import make_listing
from test_mapdata import (
    GENERATED,
    SOLD,
    WALSH_FINANCE,
    WATCH,
    _config,
    _record,
    _sold_market,
)

from propertyfinder import pagebuild
from propertyfinder.mapdata import build_map_payload
from propertyfinder.pagebuild import PAYLOAD_TOKEN, render

TEMPLATE = "map.html"


def _page(sessions, cfg=None):
    cfg = cfg or _config(WATCH, SOLD, finance=WALSH_FINANCE)
    with sessions() as s:
        payload = build_map_payload(s, WATCH, cfg, GENERATED)
    return render(TEMPLATE, payload), payload


# -- the vendored library ------------------------------------------------------------------


def test_the_page_carries_leaflet_in_its_own_bytes(sessions):
    """The reason this is vendored rather than linked: a report archived today has to open
    in five years, on a train, without asking anyone's network for a script."""
    _record(sessions, [make_listing("111", price=500_000)])
    page, _ = _page(sessions, _config(WATCH))

    assert "Leaflet 1.9.4, a JS library for interactive maps" in page  # the js banner
    assert ".leaflet-container" in page  # ...and the stylesheet
    assert "{{VENDOR:" not in page
    assert "unpkg.com" not in page and "cdn.jsdelivr" not in page


def test_the_page_fetches_nothing_to_render_itself(sessions):
    """URLs a reader may click are fine; URLs the page loads to become itself are not.

    So this looks for the four constructs that fetch — an external script, an external
    stylesheet, a CSS import, a remote asset URL — rather than for the string "https",
    which appears in the tile layer (a click-free network call by design, at view time),
    in attribution, in a listing link, and in a Chromium bug reference inside Leaflet's
    own stylesheet.
    """
    _record(sessions, [make_listing("111", price=500_000)])
    page, _ = _page(sessions, _config(WATCH))

    assert "<script src" not in page and "<script  src" not in page
    assert 'rel="stylesheet"' not in page and "rel=stylesheet" not in page
    assert "@import" not in page
    assert "url(http" not in page and "url('http" not in page and 'url("http' not in page


def test_a_template_asking_for_a_vendored_file_that_is_not_there_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(pagebuild, "TEMPLATES_DIR", tmp_path)
    (tmp_path / "broken.html").write_text("{{VENDOR:nope.js}}" + PAYLOAD_TOKEN)

    with pytest.raises(ValueError, match="nope.js"):
        render("broken.html", {})


def test_a_vendored_file_that_would_close_its_own_element_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(pagebuild, "TEMPLATES_DIR", tmp_path)
    monkeypatch.setattr(pagebuild, "VENDOR_DIR", tmp_path)
    (tmp_path / "evil.js").write_text("var x = 1; </script>")
    (tmp_path / "page.html").write_text("{{VENDOR:evil.js}}" + PAYLOAD_TOKEN)

    with pytest.raises(ValueError, match="truncate the page"):
        render("page.html", {})


def test_the_vendored_assets_are_the_pinned_leaflet_and_nothing_else():
    names = sorted(p.name for p in pagebuild.VENDOR_DIR.iterdir() if p.suffix in (".js", ".css"))
    assert names == ["leaflet-1.9.4.css", "leaflet-1.9.4.js"]
    assert (pagebuild.VENDOR_DIR / "README.md").exists(), "a vendored file needs its provenance"


# -- the page, scored ------------------------------------------------------------------------


def test_a_scored_page_carries_its_verdicts_ledgers_and_comps(sessions):
    _record(sessions, _sold_market(), watch_name=SOLD.name, status="sold")
    _record(
        sessions,
        [
            make_listing("cheap", address="2 Cheap St", price=430_000, sqft=2600),
            make_listing("dear", address="3 Dear St", price=980_000, sqft=3400),
        ],
    )
    page, payload = _page(sessions)

    assert '"fitted":true' in page
    assert PAYLOAD_TOKEN not in page
    assert "2 Cheap St" in page and "3 Dear St" in page
    # the ledger's own words, which are what makes a score explainable rather than asserted
    assert "Starting point" in page and "Statistical value" in page
    verdicts = {row["deal"]["verdict"] for row in payload["listings"]}
    assert verdicts & {"GREAT", "GOOD"} and "OVERPRICED" in verdicts


def test_the_page_is_one_whole_document_with_the_payload_recoverable(sessions):
    _record(sessions, _sold_market(), watch_name=SOLD.name, status="sold")
    _record(sessions, [make_listing("111", price=500_000, sqft=3000)])
    page, payload = _page(sessions)

    assert page.count("<html") == 1 and page.rstrip().endswith("</html>")
    start = page.index('id="pf-payload"')
    blob = page[page.index(">", start) + 1 : page.index("</script>", start)]
    assert json.loads(blob) == payload


def test_an_address_cannot_close_the_payload_tag_early(sessions):
    _record(
        sessions,
        [make_listing("111", address="1 Main St</script><script>alert(1)", price=500_000)],
    )
    page, _ = _page(sessions, _config(WATCH))

    assert "</script><script>alert" not in page


# -- the page, unscored ------------------------------------------------------------------------


def test_a_degraded_page_still_has_its_map_and_its_table_and_says_why_it_has_no_scores(sessions):
    """The rule this stage exists for: no model is not an error and not a second template.
    The same page renders, the same map, the same homes — and the score sections stay shut
    with a sentence saying what is missing."""
    _record(sessions, [make_listing("111", address="111 Tolleson Dr", price=500_000)])
    page, payload = _page(sessions, _config(WATCH, finance=WALSH_FINANCE))

    assert '"fitted":false' in page
    assert '"deal":null' in page
    assert "no sold companion watch is configured" in page  # the reason travels into the page
    assert 'id="pf-map"' in page  # the map is still there
    assert "111 Tolleson Dr" in page  # ...and so is the home
    assert '"curve":[]' in page and '"solds":[]' in page
    # The two model-dependent sections ship closed and are opened by the page's own script
    # only when there is something to put in them.
    assert 'id="pf-chart-section" hidden' in page
    assert 'id="pf-newcon-section" hidden' in page


def test_a_page_with_no_sweep_at_all_still_opens(sessions):
    page, _ = _page(sessions, _config(WATCH))

    assert '"active":0' in page
    assert '"history_began":false' in page
    assert page.count("<html") == 1


# -- what the split is for ------------------------------------------------------------------


def test_the_template_file_contains_no_python():
    source = (pagebuild.TEMPLATES_DIR / TEMPLATE).read_text()
    for marker in ("import ", "def ", "session.execute", "from propertyfinder"):
        assert marker not in source, f"{marker!r} found in {TEMPLATE}"


def test_the_builder_module_still_contains_no_html():
    """`render` grew a second kind of token this stage; it did not grow a tag."""
    source = Path(pagebuild.__file__).read_text()
    for marker in ("<html", "<div", "<table"):
        assert marker not in source, f"{marker!r} found in pagebuild.py"


def test_the_movement_strip_degrades_to_history_begins_today(sessions):
    """Both shapes of the strip, proved the way test_report_page proves them: through the
    embedded JSON a browser would read, since the sentence itself is the script's job."""
    _record(sessions, [make_listing("111", price=500_000), make_listing("222", price=700_000)])
    single, _ = _page(sessions, _config(WATCH))
    assert '"history_began":false' in single
    assert "History begins today." in single  # the words are in the template, ready

    _record(sessions, [make_listing("111", price=465_000)], ts="2026-07-11T10:00:00Z")
    second, payload = _page(sessions, _config(WATCH))
    assert '"history_began":true' in second
    assert json.dumps(payload["movement"]["cuts"][0]["delta"]) in second
    assert [g["zpid"] for g in payload["movement"]["gone"]] == ["222"]
