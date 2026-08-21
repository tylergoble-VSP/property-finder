"""One source, two builds: derive the claude.ai artifact fragment from the canonical document.

    .venv/bin/python scripts/build_artifact_fragment.py site-talk/index.html

An artifact fragment is not a web page, and a web page is not an artifact fragment. The
claude.ai artifact host injects the doctype, `<html>`, `<head>`, `<body>` and a CSS reset around
whatever it is given; a static host serves your bytes and nothing else. The talk deck was
authored as a fragment and then published to Vercel, where it arrived with no reset, no
metadata, and a `<title>` sitting in the body where a browser ignores it entirely
(docs/PORTING-THE-REPORTS.md, lesson 11).

The rule that follows, and the direction of this script, is the whole point: **the full,
self-contained document is canonical** — that is what this repository's pipeline produces
anyway — **and the fragment is derived from it.** Never the reverse. Deriving downward can only
ever remove things the host supplies; deriving upward means remembering to add them, and the
thing forgotten is a `<title>` in the right place or an absolute `og:image`.

What comes off:

  * the doctype and the `<html>`, `<head>` and `<body>` tags, which the host writes
  * the page's own CSS reset, which the host supplies (and which would otherwise fight it)
  * `<meta>` and `<link>` elements — viewport, robots, theme-color, Open Graph, the favicon —
    all of which describe a document at a URL, and a fragment has neither

What stays: every style block that is not the reset, every section, and every script. The
`<title>` is reported rather than dropped silently, because the artifact host takes a title
through its own interface and someone has to type it there.
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

# The reset block, recognised by the comment it carries in the canonical document rather than
# by position. A style block that says what it is can be found; the third `<style>` cannot.
RESET_MARKER = "reset — the artifact host supplies one"

SKELETON = re.compile(
    r"(?is)<!doctype[^>]*>|</?html[^>]*>|</?head[^>]*>|</?body[^>]*>"
)
# Attribute values are matched as quoted units rather than skipped to the first ">": the
# favicon is a `data:image/svg+xml,<svg ...>` URI, and a lazier pattern cuts it in half and
# leaves the second half of an SVG standing in the output as text.
DOCUMENT_ONLY = re.compile(
    r"""(?imsx) ^[ \t]* < (?:meta|link) \b (?: [^>"'] | "[^"]*" | '[^']*' )* > [ \t]* \n?"""
)
STYLE_BLOCK = re.compile(r"(?is)<style>.*?</style>")
TITLE = re.compile(r"(?is)<title>(.*?)</title>")


def derive(document: str) -> tuple[str, str]:
    """(the fragment, the title the host has to be told) from a canonical document."""
    match = TITLE.search(document)
    title = match.group(1).strip() if match else ""

    fragment = TITLE.sub("", document)
    fragment = STYLE_BLOCK.sub(
        lambda m: "" if RESET_MARKER in m.group(0) else m.group(0), fragment
    )
    fragment = DOCUMENT_ONLY.sub("", fragment)
    fragment = SKELETON.sub("", fragment)
    # Collapse the runs of blank lines the removals leave behind, so the fragment reads like a
    # file somebody wrote rather than the residue of one.
    fragment = re.sub(r"\n{3,}", "\n\n", fragment).strip() + "\n"
    return fragment, title


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("source", type=Path, help="the canonical, self-contained document")
    parser.add_argument(
        "-o", "--out", type=Path, help="where to write the fragment (default: <source>.fragment.html)"
    )
    args = parser.parse_args(argv)

    document = args.source.read_text()
    fragment, title = derive(document)
    out = args.out or args.source.with_suffix(".fragment.html")
    out.write_text(fragment)

    print(f"{args.source} -> {out}  ({len(document):,} -> {len(fragment):,} bytes)")
    if title:
        print(f'set the artifact title to: "{title}"')
    for leftover in ("<!doctype", "<html", "<head", "<body", "<title"):
        if leftover in fragment.lower():
            print(f"  warning: {leftover!r} survived into the fragment")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
