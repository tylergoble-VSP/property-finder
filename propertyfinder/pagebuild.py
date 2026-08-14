"""Splice a payload into a template — the entire "build a page" step, and no more.

Everything about *how* a report looks lives in `templates/*.html`, a real file editable
with syntax highlighting, its own style and script blocks, and zero Python inside it.
Everything about *what* a report says lives in a plain dict (`reportdata.build_payload`).
This module's only job is gluing the two together: read the template, replace its one
payload token with JSON, hand back the finished page. It assembles no markup — building
HTML here, one string at a time, would be the exact mistake docs/REBUILD.md calls out in
the original tool, just moved one file over.
"""
from __future__ import annotations

import json
from pathlib import Path

TEMPLATES_DIR = Path(__file__).parent / "templates"

# The one seam between a template and its data. It lives inside a JavaScript comment so
# the template is also valid, renderable (empty) HTML before this module ever touches it.
PAYLOAD_TOKEN = "/*__PAYLOAD__*/{}"


def render(template_name: str, payload: dict) -> str:
    """The named template, with its payload token replaced by `payload` as JSON.

    Raises `ValueError` if the token is missing, or appears more than once — a template
    that lost it would silently ship a page with no data, and one with two copies would
    silently duplicate it. Either way, failing loudly here beats a page that looks fine
    and is wrong.
    """
    template = (TEMPLATES_DIR / template_name).read_text()
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
