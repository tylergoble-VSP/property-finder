"""Splice a payload into a template — the entire "build a page" step, and no more.

Everything about *how* a report looks lives in `templates/*.html`, a real file editable
with syntax highlighting, its own style and script blocks, and zero Python inside it.
Everything about *what* a report says lives in a plain dict (`reportdata.build_payload`).
This module's only job is gluing the two together: read the template, replace its one
payload token with JSON, hand back the finished page. It assembles no markup — building
HTML here, one string at a time, would be the exact mistake docs/REBUILD.md calls out in
the original tool, just moved one file over.

A page may also need a library it did not write. `{{VENDOR:name}}` inlines a file from
`templates/vendor/`, checked into this repository at a pinned version (see that folder's
own README), so a built report stays what every page here is: one file that opens from a
filesystem with nothing fetched at load time. The map page needs Leaflet, and a Leaflet
loaded from a content-delivery network would mean a report that goes blank the day the
network moves — or the day it is read on a train.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

TEMPLATES_DIR = Path(__file__).parent / "templates"
VENDOR_DIR = TEMPLATES_DIR / "vendor"

# The one seam between a template and its data. It lives inside a JavaScript comment so
# the template is also valid, renderable (empty) HTML before this module ever touches it.
PAYLOAD_TOKEN = "/*__PAYLOAD__*/{}"

# `{{VENDOR:leaflet-1.9.4.js}}`. The name may not contain a separator, so the pattern
# itself is what confines an include to the vendor folder — no path can be spelled that
# escapes it.
VENDOR_TOKEN = re.compile(r"\{\{VENDOR:([A-Za-z0-9._-]+)\}\}")

# The two sequences that would end the element a vendored asset is being inlined into.
# Neither appears in anything vendored here; a future asset containing one would truncate
# a page mid-file, so it is refused at build time rather than discovered in a browser.
_CLOSERS = ("</script", "</style")


def render(
    template_name: str,
    payload: dict,
    *,
    templates_dir: Path | None = None,
    vendor_dir: Path | None = None,
) -> str:
    """The named template, with its payload token replaced by `payload` as JSON.

    Raises `ValueError` if the token is missing, or appears more than once — a template
    that lost it would silently ship a page with no data, and one with two copies would
    silently duplicate it. Either way, failing loudly here beats a page that looks fine
    and is wrong.

    Vendored assets are inlined first and the payload last, so the payload's own text is
    the one thing on the page nothing afterwards reads: an address that happened to spell
    an include token could otherwise pull a file into the JSON.

    `templates_dir`/`vendor_dir` default to this package's own folders (resolved at call
    time, so monkeypatching the module globals still works), and exist so a dependent
    package (the agent-finder annex) can render its own templates through this same splicing
    — reusing the `</`-escaping and the exactly-one-token check rather than copying a
    security-relevant function.
    """
    templates_dir = templates_dir or TEMPLATES_DIR
    vendor_dir = vendor_dir or VENDOR_DIR
    template = inline_vendor(
        (templates_dir / template_name).read_text(), template_name, vendor_dir
    )
    count = template.count(PAYLOAD_TOKEN)
    if count != 1:
        raise ValueError(
            f"{template_name!r} must contain the token {PAYLOAD_TOKEN!r} exactly once "
            f"(found {count}) — nothing was rendered"
        )

    encoded = json.dumps(payload, separators=(",", ":"))
    # A payload string containing "</script" would otherwise close the embedding tag
    # early and truncate the page mid-JSON. Escaping the slash leaves the JSON's meaning
    # untouched and the closing sequence impossible to spell.
    encoded = encoded.replace("</", "<\\/")
    return template.replace(PAYLOAD_TOKEN, encoded)


def inline_vendor(
    template: str,
    template_name: str = "<template>",
    vendor_dir: Path | None = None,
) -> str:
    """Every `{{VENDOR:name}}` replaced by the bytes of `templates/vendor/name`.

    A template with no includes comes back untouched, which is why `report.html` never had
    to hear about any of this. A named file that is not there raises rather than rendering
    a page whose map is a grey rectangle.
    """
    vendor_dir = vendor_dir or VENDOR_DIR

    def _read(match: re.Match) -> str:
        name = match.group(1)
        path = vendor_dir / name
        if not path.is_file():
            raise ValueError(
                f"{template_name!r} asks for the vendored file {name!r}, which is not in "
                f"{vendor_dir} — nothing was rendered"
            )
        asset = path.read_text()
        lowered = asset.lower()
        for closer in _CLOSERS:
            if closer in lowered:
                raise ValueError(
                    f"vendored file {name!r} contains {closer!r}, which would end the "
                    "element it is inlined into and truncate the page — nothing was "
                    "rendered"
                )
        return asset

    return VENDOR_TOKEN.sub(_read, template)
