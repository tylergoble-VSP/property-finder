"""scripts/build_site.py: an allowlist copier, driven entirely by site-manifest.yaml.

Imported as a bare module (not a package) because `scripts/` is a folder of standalone
scripts, not part of the `propertyfinder` distribution — the same reason `tests/conftest.py`
is reached with `from conftest import ...` rather than a package-qualified import.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import build_site  # noqa: E402


def _write_manifest(path: Path, entries: list[dict]) -> Path:
    path.write_text(yaml.safe_dump(entries, sort_keys=False))
    return path


def _report(reports_dir: Path, name: str, body: str = "<html>hi</html>") -> Path:
    reports_dir.mkdir(parents=True, exist_ok=True)
    page = reports_dir / name
    page.write_text(body)
    return page


# -- the manifest is respected, and only the manifest -----------------------------------


def test_only_manifest_entries_are_copied(tmp_path):
    reports = tmp_path / "reports"
    _report(reports, "walsh-aledo.html", "<html>walsh</html>")
    _report(reports, "not-in-the-manifest.html", "<html>secret-ish</html>")
    manifest = _write_manifest(
        tmp_path / "site-manifest.yaml",
        [{"source": str(reports / "walsh-aledo.html"), "dest": "walsh-aledo.html",
          "visibility": "private"}],
    )
    site = tmp_path / "site"

    entries = build_site.build(manifest_path=manifest, reports_dir=reports, site_dir=site)

    assert len(entries) == 1
    assert (site / "walsh-aledo.html").read_text() == "<html>walsh</html>"
    assert not (site / "not-in-the-manifest.html").exists()
    # only the copied page plus the three generated files — nothing else ever lands here
    assert {p.name for p in site.iterdir()} == {
        "walsh-aledo.html", "index.html", "vercel.json", "middleware.js",
    }


def test_rebuilding_preserves_the_vercel_project_link(tmp_path):
    reports = tmp_path / "reports"
    _report(reports, "walsh-aledo.html")
    manifest = _write_manifest(
        tmp_path / "site-manifest.yaml",
        [{"source": str(reports / "walsh-aledo.html"), "dest": "walsh-aledo.html",
          "visibility": "private"}],
    )
    site = tmp_path / "site"
    site.mkdir()
    (site / ".vercel").mkdir()
    (site / ".vercel" / "project.json").write_text("{}")
    (site / "stale-leftover.html").write_text("old")

    build_site.build(manifest_path=manifest, reports_dir=reports, site_dir=site)

    assert (site / ".vercel" / "project.json").exists()
    assert not (site / "stale-leftover.html").exists()


# -- out-of-tree paths are refused, loudly, before anything is written ------------------


def test_a_source_outside_reports_is_refused(tmp_path):
    reports = tmp_path / "reports"
    reports.mkdir()
    secret = tmp_path / ".env"
    secret.write_text("SEARCHAPI_API_KEY=do-not-publish-me")
    manifest = _write_manifest(
        tmp_path / "site-manifest.yaml",
        [{"source": str(secret), "dest": "oops.html", "visibility": "public"}],
    )
    site = tmp_path / "site"

    with pytest.raises(build_site.ManifestError, match="outside"):
        build_site.build(manifest_path=manifest, reports_dir=reports, site_dir=site)

    assert not site.exists()  # refused before a single file was touched


def test_a_relative_escape_is_also_refused(tmp_path):
    """`../` inside an otherwise reports/-looking path is still an escape."""
    reports = tmp_path / "reports"
    reports.mkdir()
    (tmp_path / "propertyfinder.db").write_text("not a real database, but pretend")
    manifest = _write_manifest(
        tmp_path / "site-manifest.yaml",
        [{"source": str(reports / ".." / "propertyfinder.db"), "dest": "db.html",
          "visibility": "public"}],
    )

    with pytest.raises(build_site.ManifestError, match="outside"):
        build_site.build(manifest_path=manifest, reports_dir=reports, site_dir=tmp_path / "site")


def test_a_manifest_naming_a_file_that_was_never_built_is_refused(tmp_path):
    reports = tmp_path / "reports"
    reports.mkdir()
    manifest = _write_manifest(
        tmp_path / "site-manifest.yaml",
        [{"source": str(reports / "never-swept.html"), "dest": "never-swept.html",
          "visibility": "private"}],
    )

    with pytest.raises(build_site.ManifestError, match="never-swept.html"):
        build_site.build(manifest_path=manifest, reports_dir=reports, site_dir=tmp_path / "site")


def test_an_unknown_visibility_is_refused(tmp_path):
    reports = tmp_path / "reports"
    _report(reports, "walsh-aledo.html")
    manifest = _write_manifest(
        tmp_path / "site-manifest.yaml",
        [{"source": str(reports / "walsh-aledo.html"), "dest": "walsh-aledo.html",
          "visibility": "unlisted"}],
    )

    with pytest.raises(build_site.ManifestError, match="unlisted"):
        build_site.build(manifest_path=manifest, reports_dir=reports, site_dir=tmp_path / "site")


# -- the index links every private page, and only the private ones ---------------------


def test_index_links_private_pages_and_leaves_public_ones_unlisted(tmp_path):
    reports = tmp_path / "reports"
    _report(reports, "walsh-aledo.html")
    _report(reports, "handoff.html")
    manifest = _write_manifest(
        tmp_path / "site-manifest.yaml",
        [
            {"source": str(reports / "walsh-aledo.html"), "dest": "walsh-aledo.html",
             "visibility": "private"},
            {"source": str(reports / "handoff.html"), "dest": "handoff.html",
             "visibility": "public"},
        ],
    )
    site = tmp_path / "site"

    build_site.build(manifest_path=manifest, reports_dir=reports, site_dir=site)

    index = (site / "index.html").read_text()
    assert '"dest":"walsh-aledo.html"' in index.replace(" ", "")
    assert '"dest":"handoff.html"' not in index.replace(" ", "")
    assert "Walsh Aledo" in index  # the derived title, for a person reading the page
    assert 'meta name="robots" content="noindex"' in index  # the index itself stays unlisted


def test_index_says_so_honestly_when_nothing_is_published(tmp_path):
    reports = tmp_path / "reports"
    reports.mkdir()
    manifest = _write_manifest(tmp_path / "site-manifest.yaml", [])
    site = tmp_path / "site"

    entries = build_site.build(manifest_path=manifest, reports_dir=reports, site_dir=site)

    assert entries == []
    assert (site / "index.html").exists()
    assert (site / "vercel.json").exists()
    assert (site / "middleware.js").exists()


# -- vercel.json: clean URLs, noindex on public, immutable cache on dated archives ------


def test_vercel_json_headers_match_each_entrys_visibility_and_shape(tmp_path):
    reports = tmp_path / "reports"
    _report(reports, "walsh-aledo.html")
    _report(reports, "walsh-aledo-2026-08-14.html")
    _report(reports, "handoff.html")
    manifest = _write_manifest(
        tmp_path / "site-manifest.yaml",
        [
            {"source": str(reports / "walsh-aledo.html"), "dest": "walsh-aledo.html",
             "visibility": "private"},
            {"source": str(reports / "walsh-aledo-2026-08-14.html"),
             "dest": "walsh-aledo-2026-08-14.html", "visibility": "private"},
            {"source": str(reports / "handoff.html"), "dest": "handoff.html",
             "visibility": "public"},
        ],
    )
    site = tmp_path / "site"

    build_site.build(manifest_path=manifest, reports_dir=reports, site_dir=site)
    doc = json.loads((site / "vercel.json").read_text())

    assert doc["cleanUrls"] is True
    assert doc["trailingSlash"] is False

    by_source = {h["source"]: h["headers"] for h in doc["headers"]}
    assert {"key": "X-Robots-Tag", "value": "noindex"} in by_source["/handoff"]
    assert "/walsh-aledo" not in by_source or \
        {"key": "X-Robots-Tag", "value": "noindex"} not in by_source.get("/walsh-aledo", [])
    assert {"key": "Cache-Control", "value": "public, max-age=31536000, immutable"} in \
        by_source["/walsh-aledo-2026-08-14"]
    assert any(h["source"] == "/(.*)" for h in doc["headers"])  # the catch-all revalidate rule


# -- middleware.js: the manifest's public list, spliced in, nothing else ----------------


def test_middleware_carries_exactly_the_public_paths(tmp_path):
    reports = tmp_path / "reports"
    _report(reports, "walsh-aledo.html")
    _report(reports, "handoff.html")
    manifest = _write_manifest(
        tmp_path / "site-manifest.yaml",
        [
            {"source": str(reports / "walsh-aledo.html"), "dest": "walsh-aledo.html",
             "visibility": "private"},
            {"source": str(reports / "handoff.html"), "dest": "handoff.html",
             "visibility": "public"},
        ],
    )
    site = tmp_path / "site"

    build_site.build(manifest_path=manifest, reports_dir=reports, site_dir=site)
    js = (site / "middleware.js").read_text()

    assert '"/*__PUBLIC_PATHS__*/"' not in js  # the token itself never survives the splice
    assert '["handoff"]' in js.replace(" ", "")
    assert "walsh-aledo" not in js.split("PUBLIC_PATHS")[1].split(";")[0]


def test_an_empty_public_list_still_produces_valid_middleware(tmp_path):
    reports = tmp_path / "reports"
    _report(reports, "walsh-aledo.html")
    manifest = _write_manifest(
        tmp_path / "site-manifest.yaml",
        [{"source": str(reports / "walsh-aledo.html"), "dest": "walsh-aledo.html",
          "visibility": "private"}],
    )
    site = tmp_path / "site"

    build_site.build(manifest_path=manifest, reports_dir=reports, site_dir=site)
    js = (site / "middleware.js").read_text()

    assert "const PUBLIC_PATHS = /*__PUBLIC_PATHS__*/[];" in js


# -- the repository's own manifest --------------------------------------------------------


def test_the_committed_manifest_parses_and_names_only_reports_paths():
    entries = build_site.load_manifest(build_site.MANIFEST_PATH, build_site.REPORTS_DIR)
    assert entries
    for entry in entries:
        assert entry.source.is_relative_to(build_site.REPORTS_DIR.resolve())
        assert entry.visibility in build_site.VISIBILITIES


# -- the committed manifest, now that it has a public page in it -------------------------
#
# Until this stage every entry was private and the `public` branch of the copier was proved
# only against fixtures. The public deal map is the first real page to travel it, so these
# check the committed manifest itself rather than a temporary one.


def test_the_committed_manifest_names_a_page_for_each_report_the_tool_builds():
    """A manifest entry naming a file nothing builds is a deploy that fails at the last step."""
    entries = build_site.load_manifest(build_site.MANIFEST_PATH, build_site.REPORTS_DIR)
    names = {entry.source.name for entry in entries}

    assert "walsh-aledo-newcon.html" in names  # report --kind newcon
    assert "walsh-aledo-map-public.html" in names  # map --public


def test_every_public_entry_names_a_public_render():
    """The filename rule that makes a private page unpublishable by accident.

    `report --public` and `map --public` write a `-public` name and a private render never
    writes one, so an entry marked public can only ever point at a page rendered with
    market-neutral assumptions. A typo cannot cross the line, because the two halves do not
    share a filename (docs/PORTING-THE-REPORTS.md, lesson 9).
    """
    entries = build_site.load_manifest(build_site.MANIFEST_PATH, build_site.REPORTS_DIR)
    public = [e for e in entries if e.visibility == "public"]

    assert public, "the public code path has no real page exercising it"
    for entry in public:
        assert entry.source.stem.endswith("-public"), entry.source.name
    for entry in entries:
        if entry.visibility == "private":
            assert not entry.source.stem.endswith("-public"), entry.source.name
