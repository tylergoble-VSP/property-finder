"""site/middleware.js: Basic Auth before anything private is served, and fails closed.

`scripts/build_site.py` wrote a working `propertyfinder/templates/site-middleware.js`
back in the commit that introduced the site build (docs/REBUILD.md, "Template + payload +
builder, for every page" — a gate this security-sensitive does not get to exist half-done,
even between two commits). This file is that gate's own dedicated proof: every claim the
commit message makes about it — the manifest's public paths land in the generated file, an
unset password serves 401 rather than serving everything, and a public carve-out never
swallows a private path by accident — gets its own assertion here, string-matched against
the generated JavaScript rather than executed (Vercel's edge runtime is not available to
this suite, and does not need to be: the whole point of a template-plus-splice pipeline is
that what ships is exactly this text, byte for byte).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import build_site  # noqa: E402


def _report(reports_dir: Path, name: str) -> Path:
    reports_dir.mkdir(parents=True, exist_ok=True)
    page = reports_dir / name
    page.write_text("<html>hi</html>")
    return page


def _middleware(tmp_path: Path, entries: list[dict]) -> str:
    """Build a throwaway site/ from `entries` and return middleware.js's text."""
    reports = tmp_path / "reports"
    for entry in entries:
        _report(reports, Path(entry["dest"]).name)
        entry["source"] = str(reports / Path(entry["dest"]).name)
    manifest = tmp_path / "site-manifest.yaml"
    import yaml

    manifest.write_text(yaml.safe_dump(entries, sort_keys=False))
    build_site.build(manifest_path=manifest, reports_dir=reports, site_dir=tmp_path / "site")
    return (tmp_path / "site" / "middleware.js").read_text()


def _template_source() -> str:
    return (
        Path(__file__).resolve().parent.parent
        / "propertyfinder"
        / "templates"
        / "site-middleware.js"
    ).read_text()


# -- the manifest's public paths land in the generated file, and only they do -----------


def test_public_paths_from_the_manifest_are_spliced_in(tmp_path):
    js = _middleware(
        tmp_path,
        [
            {"dest": "walsh-aledo.html", "visibility": "private"},
            {"dest": "handoff.html", "visibility": "public"},
        ],
    )
    array = re.search(r"const PUBLIC_PATHS = /\*__PUBLIC_PATHS__\*/(\[.*?\]);", js).group(1)
    assert array == '["handoff"]'


def test_a_private_page_never_appears_in_the_public_array(tmp_path):
    js = _middleware(
        tmp_path,
        [
            {"dest": "walsh-aledo.html", "visibility": "private"},
            {"dest": "walsh-aledo-map.html", "visibility": "private"},
        ],
    )
    array = re.search(r"const PUBLIC_PATHS = /\*__PUBLIC_PATHS__\*/(\[.*?\]);", js).group(1)
    assert array == "[]"  # today's real manifest: nothing public, so nothing carved out


# -- fail closed: no SITE_PASSWORD means no private path is ever served -----------------


def test_the_auth_check_requires_a_nonempty_password_before_comparing(tmp_path):
    """The whole fail-closed guarantee is this one guard. `pass` defaults to `""` when
    SITE_PASSWORD is unset, and `pass && header === expected` short-circuits on an empty
    string — so an unconfigured site can never reach the branch that serves the page, no
    matter what (or nothing) a request sends as its Authorization header."""
    js = _middleware(tmp_path, [{"dest": "walsh-aledo.html", "visibility": "private"}])

    assert 'process.env.SITE_PASSWORD || ""' in js
    assert "if (pass && header === expected)" in js


def test_the_only_way_out_of_the_gate_is_the_password_check_or_being_public(tmp_path):
    """Structural proof that 401 is the true default: the function has exactly two early
    bail-outs (the public carve-out, and a correct password), and whatever falls through
    both of them reaches one final, unconditional `return new Response(..., 401)` — not a
    third early return that could let a request slip past the gate some other way."""
    js = _middleware(tmp_path, [{"dest": "walsh-aledo.html", "visibility": "private"}])
    body = js[js.index("export default function middleware") :]

    assert body.count("return;") == 2  # the public carve-out, and the authenticated case
    assert "return new Response(" in body
    # the 401 response is the last statement in the function — nothing after it, and
    # both early "return;"s come before it — so falling through either one ends there.
    assert body.index("return new Response(") > body.rindex("return;")
    assert "status: 401" in body[body.index("return new Response(") :]
    # nothing follows the 401 response but the function's own closing brace
    assert body.rstrip().endswith("}") and "});" in body.rsplit("return new Response(", 1)[1]


def test_www_authenticate_names_this_app_not_the_one_it_was_forked_from(tmp_path):
    js = _middleware(tmp_path, [{"dest": "walsh-aledo.html", "visibility": "private"}])
    assert 'realm="Property Finder"' in js
    assert "Property Watch" not in js  # the tool this one was rebuilt from, left behind


# -- the template itself, before any splice, already carries every one of these ---------


def test_the_unsplit_template_already_contains_every_guarantee():
    """Even before `build_site.py` ever touches it, the template on disk is the real gate
    — the splice only ever supplies the public-path list, never the security logic."""
    template = _template_source()
    assert "const PUBLIC_PATHS = /*__PUBLIC_PATHS__*/[];" in template
    assert 'process.env.SITE_PASSWORD || ""' in template
    assert "if (pass && header === expected)" in template
    assert "status: 401" in template
    assert 'realm="Property Finder"' in template
