"""Build a publishable `site/` folder from an explicit manifest — nothing else moves.

Every earlier version of "publish the reports" grew its own discovery rule: glob for
dated archives, glob for one-home analyses, glob for purchase dossiers — three globs
guessing at three shapes of thing, until the deploy folder held whatever those globs
happened to find (docs/REBUILD.md, post-mortem item 8: "three deploy targets accreted —
hence the manifest"). This script has no globs. `site-manifest.yaml` names every file
that gets published and whether it needs a password, and the copier touches exactly those
files and nothing else: a secrets file or the SQLite database is unpublishable by
construction, because neither could ever be spelled as a `reports/*.html` path, which is
the one shape of thing this script will accept.

    .venv/bin/python scripts/build_site.py       # writes site/ from site-manifest.yaml

`site/index.html` links every *private* page; a *public* one stays reachable only by a
direct URL, unlisted — the same carve-out the original's one-home analyses used, kept here
as the shape of the idea rather than the feature itself (this rebuild ships nothing that
actually needs to be public yet). The index is built the same way every other page in this
project is: a template file (`propertyfinder/templates/site-index.html`) plus a JSON
payload plus `pagebuild.render` — no HTML lives in this file. The one exception is
`site/middleware.js`, whose template is real JavaScript rather than HTML and so gets its
one token spliced by hand, in `_middleware_js` below, rather than through `pagebuild.render`
(which only knows the `{}`-shaped payload token every *page* template carries).
"""
from __future__ import annotations

import json
import re
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from propertyfinder.pagebuild import render  # noqa: E402

MANIFEST_PATH = ROOT / "site-manifest.yaml"
REPORTS_DIR = ROOT / "reports"
SITE_DIR = ROOT / "site"

VISIBILITIES = {"private", "public"}

# A dated archive (`<name>-YYYY-MM-DD.html`) never changes once written, so it is the one
# thing in `site/` safe to cache forever; everything else is a "latest" name that a later
# rebuild will overwrite, and must always be revalidated.
_DATED = re.compile(r"-\d{4}-\d{2}-\d{2}\.html$")

# The token `templates/site-middleware.js` carries. Not the `pagebuild.PAYLOAD_TOKEN`
# shape (`{}`) because this file's one variable is a list of paths, not a page's payload.
_PUBLIC_PATHS_TOKEN = "/*__PUBLIC_PATHS__*/[]"


class ManifestError(ValueError):
    """The manifest asked for something the copier refuses to do."""


@dataclass(frozen=True)
class PageEntry:
    """One validated line of the manifest: where the file lives, and how visible it is."""

    source: Path  # resolved, absolute, and proven to sit inside reports_dir
    dest: str  # the filename it gets inside site/
    visibility: str  # "private" or "public"


def load_manifest(manifest_path: Path, reports_dir: Path) -> list[PageEntry]:
    """Read and validate every entry before any file is touched.

    Validating the whole list up front, rather than failing on the third file after the
    first two are already copied, is what keeps a bad manifest from leaving `site/`
    half-built — a partially-published site is a worse failure mode than none at all,
    since nothing about the directory's existence says it is incomplete.
    """
    raw = yaml.safe_load(manifest_path.read_text()) or []
    if not isinstance(raw, list):
        raise ManifestError(f"{manifest_path} must be a YAML list of page entries")

    reports_dir = reports_dir.resolve()
    entries: list[PageEntry] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ManifestError(f"entry {i} in {manifest_path} must be a mapping, got {item!r}")
        missing = {"source", "dest", "visibility"} - item.keys()
        if missing:
            raise ManifestError(f"entry {i} in {manifest_path} is missing {sorted(missing)}")
        if item["visibility"] not in VISIBILITIES:
            raise ManifestError(
                f"entry {i} ({item['dest']!r}) names visibility {item['visibility']!r}, "
                f"must be one of {sorted(VISIBILITIES)}"
            )

        source = (ROOT / item["source"]).resolve()
        if not _is_within(source, reports_dir):
            raise ManifestError(
                f"entry {i} names {item['source']!r}, which is outside {reports_dir} — "
                "the site copier only ever touches reports/, so anything else is refused "
                "outright rather than silently skipped"
            )
        entries.append(
            PageEntry(source=source, dest=item["dest"], visibility=item["visibility"])
        )
    return entries


def _is_within(path: Path, directory: Path) -> bool:
    try:
        path.relative_to(directory)
        return True
    except ValueError:
        return False


def build(
    manifest_path: Path = MANIFEST_PATH,
    reports_dir: Path = REPORTS_DIR,
    site_dir: Path = SITE_DIR,
) -> list[PageEntry]:
    """Copy every manifest entry into `site_dir`, then write the pages that describe what
    just got copied: the index, `vercel.json`, and `middleware.js`. Returns the entries."""
    entries = load_manifest(manifest_path, reports_dir)

    missing = [e for e in entries if not e.source.is_file()]
    if missing:
        names = ", ".join(str(e.source) for e in missing)
        raise ManifestError(
            f"the manifest names {names}, which {'does' if len(missing) == 1 else 'do'} "
            "not exist on disk — run `report` / `map` / `daily` first, or drop the stale "
            "entry from the manifest"
        )

    _reset(site_dir)
    for entry in entries:
        shutil.copy2(entry.source, site_dir / entry.dest)

    (site_dir / "index.html").write_text(_index_html(entries))
    (site_dir / "vercel.json").write_text(_vercel_json(entries))
    (site_dir / "middleware.js").write_text(_middleware_js(entries))

    total = sum(f.stat().st_size for f in site_dir.rglob("*") if f.is_file())
    print(f"built {site_dir} — {len(entries)} page(s), {total / 1_048_576:.1f} MB")
    return entries


def _reset(site_dir: Path) -> None:
    """Empty `site_dir` before a fresh build, preserving `.vercel` — the project link a
    deploy needs in order to keep landing on the same Vercel project across rebuilds."""
    if site_dir.exists():
        for item in site_dir.iterdir():
            if item.name == ".vercel":
                continue
            shutil.rmtree(item) if item.is_dir() else item.unlink()
    else:
        site_dir.mkdir(parents=True)


def _index_html(entries: list[PageEntry]) -> str:
    """The landing page, built the same way every other page here is: a template, a JSON
    payload, `pagebuild.render`. It links every *private* entry; a public one is reachable
    by its URL but never listed, so "on the index" and "requires the password" stay the
    same fact."""
    payload = {
        "generated_ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "pages": [
            {"dest": e.dest, "title": _title(e.dest)}
            for e in entries
            if e.visibility == "private"
        ],
    }
    return render("site-index.html", payload)


def _title(dest: str) -> str:
    stem = dest.removesuffix(".html")
    return " ".join(part.capitalize() for part in stem.split("-"))


def _clean_url(dest: str) -> str:
    return dest.removesuffix(".html")


def _vercel_json(entries: list[PageEntry]) -> str:
    """Clean URLs, do-not-index on public pages, immutable caching on dated archives, and
    a revalidate-always default for everything else — the last of which is what makes a
    rebuilt "latest" page actually replace what a browser or a CDN edge is holding."""
    headers = [
        {
            "source": f"/{_clean_url(e.dest)}",
            "headers": [{"key": "X-Robots-Tag", "value": "noindex"}],
        }
        for e in entries
        if e.visibility == "public"
    ] + [
        {
            "source": f"/{_clean_url(e.dest)}",
            "headers": [
                {"key": "Cache-Control", "value": "public, max-age=31536000, immutable"}
            ],
        }
        for e in entries
        if _DATED.search(e.dest)
    ] + [
        {
            "source": "/(.*)",
            "headers": [
                {"key": "Cache-Control", "value": "public, max-age=0, must-revalidate"}
            ],
        }
    ]
    doc = {"cleanUrls": True, "trailingSlash": False, "headers": headers}
    return json.dumps(doc, indent=2) + "\n"


def _middleware_js(entries: list[PageEntry]) -> str:
    """The auth gate, with the manifest's public paths spliced into its one token.

    Splicing rather than importing the manifest at request time: Vercel's edge runtime
    reads whatever `site/middleware.js` says, once, at deploy — it has no access to
    `site-manifest.yaml`, and should not need any, since everything it has to know is
    already baked into this one line by the time it is uploaded.
    """
    template = (ROOT / "propertyfinder" / "templates" / "site-middleware.js").read_text()
    public_paths = [_clean_url(e.dest) for e in entries if e.visibility == "public"]
    count = template.count(_PUBLIC_PATHS_TOKEN)
    if count != 1:
        raise ValueError(
            f"site-middleware.js must contain {_PUBLIC_PATHS_TOKEN!r} exactly once "
            f"(found {count}) — nothing was rendered"
        )
    encoded = "/*__PUBLIC_PATHS__*/" + json.dumps(public_paths)
    return template.replace(_PUBLIC_PATHS_TOKEN, encoded)


if __name__ == "__main__":
    try:
        build()
    except ManifestError as exc:
        raise SystemExit(str(exc)) from exc
