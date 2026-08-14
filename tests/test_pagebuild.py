"""`render` glues one template to one payload. That is all it is allowed to do.

The proof has two halves, matching docs/REBUILD.md's "no HTML in Python strings" rule:
the rendered page actually carries the payload and nothing of the token survives, and the
Python module doing the splicing never itself contains a tag — if it ever did, the whole
point of the template/payload split would be quietly gone.
"""
import json
from pathlib import Path

import pytest

from propertyfinder import pagebuild
from propertyfinder.pagebuild import PAYLOAD_TOKEN, render


def test_render_embeds_the_payload_and_removes_the_token():
    payload = {"watch": {"name": "walsh-aledo"}, "listings": [{"zpid": "111"}]}
    page = render("report.html", payload)

    assert PAYLOAD_TOKEN not in page
    assert json.dumps(payload, separators=(",", ":")) in page
    assert page.count("<html") == 1  # still one whole document, not two


def test_a_template_missing_the_token_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(pagebuild, "TEMPLATES_DIR", tmp_path)
    (tmp_path / "broken.html").write_text("<html>no token here</html>")

    with pytest.raises(ValueError, match="exactly once"):
        render("broken.html", {"a": 1})


def test_a_template_with_the_token_twice_also_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(pagebuild, "TEMPLATES_DIR", tmp_path)
    (tmp_path / "doubled.html").write_text(PAYLOAD_TOKEN + " ... " + PAYLOAD_TOKEN)

    with pytest.raises(ValueError, match="found 2"):
        render("doubled.html", {"a": 1})


def test_a_payload_string_cannot_close_the_embedding_tag_early():
    """An address or status string containing "</script" must not be able to end the
    JSON blob before the JSON parser sees the rest of it."""
    payload = {"listings": [{"address": "123 Main St</script><script>alert(1)"}]}
    page = render("report.html", payload)

    assert "</script><script>alert" not in page
    # ...and the escaping is reversible: a browser's JSON.parse sees the original text.
    start = page.index('id="pf-payload"')
    blob = page[page.index(">", start) + 1 : page.index("</script>", start)]
    assert json.loads(blob) == payload


def test_the_builder_module_contains_no_html():
    source = Path(pagebuild.__file__).read_text()
    for marker in ("<html", "<div", "<script", "<table", "<style"):
        assert marker not in source, f"{marker!r} found in pagebuild.py"


def test_the_template_file_contains_no_python():
    source = (pagebuild.TEMPLATES_DIR / "report.html").read_text()
    for marker in ("import ", "def ", "session.execute", "from propertyfinder"):
        assert marker not in source, f"{marker!r} found in report.html"
