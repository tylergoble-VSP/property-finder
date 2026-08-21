"""Verify a built page by RENDERING it, not by reading it. Committed once, so it stops being
rebuilt from scratch every time someone needs it — which is why it was sometimes skipped.

    .venv/bin/python scripts/verify_page.py reports/walsh-aledo-newcon.html
    .venv/bin/python scripts/verify_page.py reports/*.html --viewport 1280x720

The test suite is offline and browser-free on purpose (`tests/test_map_page.py` explains the
division of labour): it proves everything decided at build time, and it cannot prove that the
page's own script ran. This harness covers exactly that layer, and it is a development tool
rather than a suite member — a person, or `daily`, runs it against a freshly built page.

WHAT IT CATCHES, all of it a bug that actually shipped or nearly did:

  * **A JavaScript error.** The loudest and most disguised failure there is: a page with a
    ReferenceError halfway down renders its first four sections perfectly and simply stops,
    and every build-time check still passes. Chrome does not report it on stdout, so the
    console is read off stderr with --enable-logging.
  * **A placeholder in visible text.** One `${...}` sat in static markup rather than inside a
    template literal, and the page would have shown a reader the raw expression. Reading the
    source does not catch it; rendering and grepping the *visible text* does — for `${`,
    `undefined`, `NaN`, and this repository's own `[[token]]` form.
  * **Element counts that disagree with the payload.** Markers on the map, rows in the plan
    table, cards in the ready-now list. A count that is short by one means the script threw
    inside a loop, which is invisible in the bytes.
  * **Overflow at the display the content will actually meet.** Twelve of the companion deck's
    thirty-one slides overflowed at 1280×720 — a projector's reality — while all thirty-one
    fitted at the 1920×1080 they were authored at. So 1280×720 is the default, and the check
    is per section as well as for the page.

TWO TRAPS, WRITTEN DOWN SO NOBODY REDISCOVERS THEM:

  * Headless Chrome defaults to dark and **ignores** --force-prefers-color-scheme. The switch
    that works is --blink-settings=preferredColorScheme=1 (dark) or =2 (light), and both are
    run, because a colour defined in only one theme state is a bug in the other.
  * --screenshot resets scroll position, so isolating a section into its own viewport beats
    trying to scroll to it. Nothing here screenshots for that reason; the probe measures
    geometry in the page and reports numbers instead.

The probe below is injected into a COPY of the page in a temporary folder. The page under test
is never modified, and the copy is what the browser opens.
"""
from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

# Where the probe writes its findings, and how the reader finds them in a dumped DOM. A marker
# pair rather than a bare element id: --dump-dom emits the whole document including a vendored
# copy of Leaflet, and a delimiter that cannot occur in JavaScript is what makes the extraction
# a one-liner instead of a parser.
BEGIN, END = "__PF_VERIFY_BEGIN__", "__PF_VERIFY_END__"

# Strings that must never reach a reader's eye. `[[` is this repository's own curated-prose
# token form, which `fill()` in newcon.html substitutes — an unknown token is deliberately
# left standing rather than blanked, so that it fails here rather than leaving a silent gap.
FORBIDDEN = ("${", "undefined", "NaN", "[[")

CHROME_CANDIDATES = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
)

# preferredColorScheme: 1 is dark, 2 is light. Not a typo, and not the switch you would guess.
THEMES = {"dark": 1, "light": 2}

# What each page is expected to draw, per payload script id. Explicit rather than inferred: a
# clever general rule would have to guess which payload arrays reach the map (the buyer report
# places plan sheets and standing homes but not resales), and a wrong guess in a verifier is
# worse than no verifier. `count` takes the payload and returns the number the DOM should hold.
PAGE_EXPECTATIONS = {
    "nc-payload": [
        ("map markers", ".leaflet-interactive",
         lambda p: _placed(p["specs"]) + _placed(p["plans"])),
        ("plan table rows", "#plans-table tbody tr", lambda p: len(p["plans"])),
        ("ready-now cards", "#spec-cards .spec", lambda p: len(p["specs"])),
        ("builder cards", "#builder-cards .bcard", lambda p: len(p["builders"])),
        ("ask-curve panels", "#curve-facets .facet",
         lambda p: len({r["builder"] for r in p["plans"]})),
    ],
    # The map page draws one extra interactive path that is not a home: the dashed circle
    # showing the watch's radius. Counting it as a marker would make this check permanently
    # off by one, and a check that is always one out is a check nobody reads.
    "pf-payload": [
        ("map markers", ".leaflet-interactive", lambda p: _placed(p["listings"]) + 1),
        ("deal cards", "#pf-deals .deal", lambda p: len(p["listings"])),
    ],
}


def _placed(rows: list[dict]) -> int:
    return sum(1 for r in rows if r.get("lat") is not None and r.get("lon") is not None)


# The probe. Pure DOM measurement, no assertions: it reports numbers and the reader decides,
# so that a failure prints what was found rather than only that something was wrong.
PROBE = """
<script>
window.addEventListener("load", function () {
  setTimeout(function () {
    var doc = document.documentElement;
    var text = document.body.innerText || "";
    var found = {};
    ["${", "undefined", "NaN", "[["].forEach(function (needle) {
      var at = text.indexOf(needle);
      if (at >= 0) found[needle] = text.slice(Math.max(0, at - 80), at + 60);
    });

    var sections = [];
    document.querySelectorAll("section, .slide, .tblwrap, table").forEach(function (node) {
      // A container that declares its own horizontal scrolling is doing its job, not
      // overflowing: a wide table inside overflow-x:auto is the correct answer to a wide
      // table. What is never correct is the page itself scrolling sideways.
      var style = window.getComputedStyle(node);
      var scrolls = style.overflowX === "auto" || style.overflowX === "scroll";
      var dx = scrolls ? 0 : node.scrollWidth - node.clientWidth;
      var dy = node.scrollHeight - node.clientHeight;
      var fixed = style.overflowY === "hidden";
      if (dx > 1 || (fixed && dy > 1)) {
        sections.push({
          id: node.id || node.className || node.tagName.toLowerCase(),
          dx: dx, dy: fixed ? dy : 0
        });
      }
    });

    var counts = {};
    SELECTORS.forEach(function (selector) {
      counts[selector] = document.querySelectorAll(selector).length;
    });

    var result = {
      viewport: [window.innerWidth, window.innerHeight],
      dark: window.matchMedia("(prefers-color-scheme: dark)").matches,
      page_overflow_x: doc.scrollWidth - window.innerWidth,
      text_length: text.length,
      forbidden: found,
      sections: sections,
      counts: counts
    };
    var box = document.createElement("pre");
    box.id = "pf-verify";
    // The markers are assembled from halves so that the literal never appears whole in this
    // script's own source — --dump-dom emits the script too, and a reader looking for the
    // marker would otherwise find the injected source before the result it delimits.
    box.textContent = "__PF_VERIFY" + "_BEGIN__" + JSON.stringify(result) +
                      "__PF_VERIFY" + "_END__";
    document.body.appendChild(box);
  }, 400);
});
</script>
"""


@dataclass
class Failure:
    page: str
    theme: str
    what: str

    def __str__(self) -> str:
        return f"{self.page} [{self.theme}]: {self.what}"


def chrome() -> str:
    for candidate in CHROME_CANDIDATES:
        if Path(candidate).is_file():
            return candidate
    raise SystemExit(
        "no headless Chrome found. This harness needs one, and deliberately is not a member "
        f"of the test suite for that reason. Looked in:\n  " + "\n  ".join(CHROME_CANDIDATES)
    )


def payload_of(page: str) -> tuple[str, dict]:
    """(the payload script's id, the payload) — the page's own embedded truth.

    The same JSON the template's script reads, which is what makes an element count a real
    check rather than a second guess: the number in the DOM is compared against the number
    the page was built from, not against a figure typed into this file.
    """
    match = re.search(
        r'<script id="([\w-]+)" type="application/json">(.*?)</script>', page, re.S
    )
    if not match:
        raise SystemExit("this page embeds no JSON payload — nothing to check counts against")
    # `pagebuild.render` escapes "</" so a payload string cannot close the tag early.
    return match.group(1), json.loads(match.group(2).replace("<\\/", "</"))


def run(path: Path, theme: str, viewport: tuple[int, int], timeout: int) -> tuple[dict, list[str]]:
    """Render one page in one theme state and return (the probe's report, console errors)."""
    page = path.read_text()
    payload_id, _ = payload_of(page)
    selectors = [selector for _, selector, _ in PAGE_EXPECTATIONS.get(payload_id, [])]
    probe = PROBE.replace("SELECTORS", json.dumps(selectors))

    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / path.name
        copy.write_text(page.replace("</body>", probe + "</body>", 1))
        result = subprocess.run(
            [
                chrome(),
                "--headless",
                "--disable-gpu",
                "--no-sandbox",
                "--hide-scrollbars",
                f"--window-size={viewport[0]},{viewport[1]}",
                f"--blink-settings=preferredColorScheme={THEMES[theme]}",
                "--virtual-time-budget=6000",
                "--enable-logging=stderr",
                "--log-level=0",
                "--dump-dom",
                copy.resolve().as_uri(),
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    # Chrome writes page console output to stderr, and an uncaught exception appears there and
    # nowhere else — not on stdout, and (for a script that throws at top level) not reliably
    # through window.onerror either. This line is the whole reason the harness catches the one
    # failure that silently truncates a page.
    errors = [
        line.split("CONSOLE:", 1)[-1].strip()
        for line in result.stderr.splitlines()
        if "CONSOLE:" in line and ("Uncaught" in line or "Error" in line)
    ]

    # Read the probe's own element rather than searching the whole dump for the marker: the
    # dump includes every script on the page, this one included.
    block = re.search(r'<pre id="pf-verify">(.*?)</pre>', result.stdout, re.S)
    if not block:
        raise SystemExit(
            f"{path}: the probe never reported. The page's own script most likely threw "
            "before the load handler ran.\n"
            + ("console:\n  " + "\n  ".join(errors) if errors else "no console output either")
        )
    body = html.unescape(block.group(1)).split(BEGIN, 1)[1].split(END, 1)[0]
    return json.loads(body), errors


def check(path: Path, theme: str, viewport: tuple[int, int], timeout: int) -> list[Failure]:
    report, errors = run(path, theme, viewport, timeout)
    payload_id, payload = payload_of(path.read_text())
    failures = [Failure(path.name, theme, f"console: {e}") for e in errors]

    for needle, context in report["forbidden"].items():
        failures.append(
            Failure(path.name, theme, f"{needle!r} in visible text — …{context.strip()}…")
        )

    if report["page_overflow_x"] > 1:
        failures.append(
            Failure(path.name, theme,
                    f"the page scrolls sideways by {report['page_overflow_x']}px at "
                    f"{viewport[0]}×{viewport[1]}")
        )
    for section in report["sections"]:
        failures.append(
            Failure(path.name, theme,
                    f"<{section['id']}> overflows by {section['dx']}px across and "
                    f"{section['dy']}px down")
        )

    for label, selector, expected in PAGE_EXPECTATIONS.get(payload_id, []):
        want = expected(payload)
        got = report["counts"].get(selector, 0)
        if got != want:
            failures.append(
                Failure(path.name, theme,
                        f"{label}: the DOM holds {got}, the payload says {want}")
            )

    if report["text_length"] < 500:
        failures.append(
            Failure(path.name, theme,
                    f"only {report['text_length']} characters of visible text — the page "
                    "rendered, but almost nothing is on it")
        )
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Render a built page in a real browser and check what came out.",
        epilog="Not a test-suite member: the suite stays offline and browser-free.",
    )
    parser.add_argument("pages", nargs="+", type=Path, help="built HTML file(s) to verify")
    parser.add_argument(
        "--viewport",
        default="1280x720",
        help="the display to check at (default 1280x720 — a projector, not a laptop)",
    )
    parser.add_argument(
        "--theme",
        choices=(*THEMES, "both"),
        default="both",
        help="which theme state to render (default: both, because a colour defined in only "
        "one of them is a bug in the other)",
    )
    parser.add_argument("--timeout", type=int, default=90, help="seconds per render")
    args = parser.parse_args(argv)

    width, height = (int(n) for n in args.viewport.lower().split("x"))
    themes = list(THEMES) if args.theme == "both" else [args.theme]

    failures: list[Failure] = []
    for path in args.pages:
        if not path.is_file():
            raise SystemExit(f"{path} does not exist — build it first")
        for theme in themes:
            found = check(path, theme, (width, height), args.timeout)
            state = f"{len(found)} problem(s)" if found else "ok"
            print(f"{path.name} · {width}×{height} · {theme} · {state}")
            failures.extend(found)

    if not failures:
        print(f"\nall clear: {len(args.pages)} page(s) × {len(themes)} theme(s)")
        return 0
    print(f"\n{len(failures)} problem(s):", file=sys.stderr)
    for failure in failures:
        print(f"  {failure}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
