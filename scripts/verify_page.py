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

  * Headless Chrome defaults to dark and **ignores** --force-prefers-color-scheme entirely —
    passing `=light` still renders dark, so a harness that trusted it would silently check one
    state twice. --blink-settings=preferredColorScheme is the switch that works, but MEASURE
    THE ENUM rather than trusting a note about it: on Chrome 151, `=1` and `=2` both render
    LIGHT, `=0` renders dark, and omitting the flag renders dark. docs/PORTING-THE-REPORTS.md
    lesson 15 records `=1` as dark, which is how this file first shipped rendering light twice
    and reporting two clean theme states. `python3 scripts/verify_page.py --probe-themes`
    re-measures it against whatever Chrome is installed; if the table below stops matching,
    that is the thing to run.
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

# The four states this page's CSS claims to handle, and how to put a browser into each.
#
# `system` means no explicit choice is stamped on the root — which is MOST readers, and the
# state a colour defined only inside a [data-theme] block never reaches. `explicit` means the
# reader chose, which has to win over the system preference in both directions.
#
# Measured against Chrome 151 by `--probe-themes`, not copied from a note: preferredColorScheme
# `=0` renders dark and `=1` renders light. Do not swap these on the strength of documentation.
THEMES = {
    "system-dark": {"flags": ["--blink-settings=preferredColorScheme=0"], "attr": None},
    "system-light": {"flags": ["--blink-settings=preferredColorScheme=1"], "attr": None},
    # The explicit choice is tested against the OPPOSITE system preference, because that is the
    # combination a one-sided palette gets wrong: a toggle has to win, not merely agree.
    "explicit-dark": {"flags": ["--blink-settings=preferredColorScheme=1"], "attr": "dark"},
    "explicit-light": {"flags": ["--blink-settings=preferredColorScheme=0"], "attr": "light"},
}

# What each page is expected to draw. Explicit rather than inferred: a clever general rule
# would have to guess which payload arrays reach the map (the buyer report places plan sheets
# and standing homes but not resales), and a wrong guess in a verifier is worse than no
# verifier. `count` takes the payload and returns the number the DOM should hold.
#
# Keyed on a marker in the page's own markup rather than on the payload script's id, because
# map.html and report.html both call theirs `pf-payload` — keying on the id alone measured the
# table page against the map page's selectors and reported five phantom failures.
PAGE_EXPECTATIONS = [
    ("newcon report", 'id="nc-payload"', [
        ("map markers", ".leaflet-interactive",
         lambda p: _placed(p["specs"]) + _placed(p["plans"])),
        ("plan table rows", "#plans-table tbody tr", lambda p: len(p["plans"])),
        ("ready-now cards", "#spec-cards .spec", lambda p: len(p["specs"])),
        ("builder cards", "#builder-cards .bcard", lambda p: len(p["builders"])),
        ("ask-curve panels", "#curve-facets .facet",
         lambda p: len({r["builder"] for r in p["plans"]})),
    ]),
    # The map page draws one extra interactive path that is not a home: the dashed circle
    # showing the watch's radius. Counting it as a marker would make this check permanently
    # off by one, and a check that is always one out is a check nobody reads.
    ("deal map", 'id="pf-map"', [
        ("map markers", ".leaflet-interactive", lambda p: _placed(p["listings"]) + 1),
        ("deal cards", "#pf-deals .deal", lambda p: len(p["listings"])),
    ]),
    ("listing table", 'id="pf-listings"', [
        ("listing rows", "#pf-listings tbody tr", lambda p: len(p["listings"])),
    ]),
]


def expectations_for(page: str) -> list[tuple]:
    """The count checks that apply to this page, or none where the page is not one we know.

    A page with no entry here is still checked for leaked placeholders, overflow, theme
    response and console errors — it just has no counts to compare. That is the honest answer
    for a hand-authored page like the talk deck, which has no payload behind it at all.
    """
    for _, marker, checks in PAGE_EXPECTATIONS:
        if marker in page:
            return checks
    return []


def _placed(rows: list[dict]) -> int:
    return sum(1 for r in rows if r.get("lat") is not None and r.get("lon") is not None)


# The probe. Pure DOM measurement, no assertions: it reports numbers and the reader decides,
# so that a failure prints what was found rather than only that something was wrong.
PROBE = """
<script>
(function () {
  var choice = THEME_ATTR;
  if (choice) document.documentElement.setAttribute("data-theme", choice);
})();
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
    // A slide is a frame the size of the display, by design. Content taller than it is
    // content the audience never sees, whether the slide scrolls internally or clips — so a
    // slide is measured vertically no matter what its overflow-y says. Twelve of thirty-one
    // slides failed this at 1280x720 while all thirty-one fitted at the 1920x1080 they were
    // authored at.
    document.querySelectorAll(".slide").forEach(function (node) {
      var dx = node.scrollWidth - node.clientWidth;
      var dy = node.scrollHeight - node.clientHeight;
      if (dx > 1 || dy > 1) {
        sections.push({
          id: node.dataset.chap ? "slide[" + node.dataset.chap + "]" : "slide",
          index: Array.prototype.indexOf.call(document.querySelectorAll(".slide"), node) + 1,
          dx: dx, dy: dy
        });
      }
    });
    document.querySelectorAll("section:not(.slide), .tblwrap, table").forEach(function (node) {
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
          index: null, dx: dx, dy: fixed ? dy : 0
        });
      }
    });

    var counts = {};
    SELECTORS.forEach(function (selector) {
      counts[selector] = document.querySelectorAll(selector).length;
    });

    var result = {
      viewport: [window.innerWidth, window.innerHeight],
      system_dark: window.matchMedia("(prefers-color-scheme: dark)").matches,
      data_theme: doc.getAttribute("data-theme"),
      // The colour actually painted. A theme run that produced the same background twice was
      // never two runs, and this is what makes that visible instead of assumed.
      background: getComputedStyle(document.body).backgroundColor,
      page_overflow_x: doc.scrollWidth - window.innerWidth,
      text_length: text.length,
      slides: document.querySelectorAll(".slide").length,
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


def payload_of(page: str) -> tuple[str | None, dict]:
    """(the payload script's id, the payload) — the page's own embedded truth, or (None, {}).

    The same JSON the template's script reads, which is what makes an element count a real
    check rather than a second guess: the number in the DOM is compared against the number
    the page was built from, not against a figure typed into this file.

    A page with no payload is not an error. The talk deck is hand-authored HTML with no data
    behind it, and the text, overflow and console checks are exactly as useful there — it just
    has no counts to compare.
    """
    match = re.search(
        r'<script id="([\w-]+)" type="application/json">(.*?)</script>', page, re.S
    )
    if not match:
        return None, {}
    # `pagebuild.render` escapes "</" so a payload string cannot close the tag early.
    return match.group(1), json.loads(match.group(2).replace("<\\/", "</"))


def run(path: Path, theme: str, viewport: tuple[int, int], timeout: int) -> tuple[dict, list[str]]:
    """Render one page in one theme state and return (the probe's report, console errors)."""
    page = path.read_text()
    selectors = [selector for _, selector, _ in expectations_for(page)]
    state = THEMES[theme]
    probe = PROBE.replace("SELECTORS", json.dumps(selectors)).replace(
        "THEME_ATTR", json.dumps(state["attr"])
    )

    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / path.name
        # Not every page here closes its body. The annex's report template ends at
        # `</script>` and relies on the parser to close the rest, which is valid HTML and
        # would have made this harness silently inject nothing at all.
        copy.write_text(
            page.replace("</body>", probe + "</body>", 1)
            if "</body>" in page
            else page + probe
        )
        result = subprocess.run(
            [
                chrome(),
                "--headless",
                "--disable-gpu",
                "--no-sandbox",
                "--hide-scrollbars",
                f"--window-size={viewport[0]},{viewport[1]}",
                *state["flags"],
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


def probe_themes() -> int:
    """What each flag actually does in the installed Chrome. Measure; never assume.

    Exists because this harness shipped with the enum backwards, copied from a note, and
    therefore rendered light twice while reporting two clean theme states. A one-command
    answer is the difference between a trap written down and a trap avoided.
    """
    page = (
        "<!doctype html><html><head><style>:root{--bg:#ffffff}"
        '@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--bg:#000000}}'
        ':root[data-theme="dark"]{--bg:#000000}body{background:var(--bg)}</style></head><body>'
        '<pre id="pf-verify"></pre><script>document.getElementById("pf-verify").textContent='
        '"__PF_VERIFY" + "_BEGIN__" + JSON.stringify({dark:'
        'window.matchMedia("(prefers-color-scheme: dark)").matches,'
        'bg:getComputedStyle(document.body).backgroundColor}) + "__PF_VERIFY" + "_END__";'
        "</script></body></html>"
    )
    trials = [("(no flag)", [])] + [
        (f"preferredColorScheme={n}", [f"--blink-settings=preferredColorScheme={n}"])
        for n in range(4)
    ] + [("--force-prefers-color-scheme=light", ["--force-prefers-color-scheme=light"])]

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "probe.html"
        path.write_text(page)
        for label, flags in trials:
            result = subprocess.run(
                [chrome(), "--headless", "--disable-gpu", "--no-sandbox",
                 "--virtual-time-budget=3000", *flags, "--dump-dom", path.resolve().as_uri()],
                capture_output=True, text=True, timeout=60,
            )
            block = re.search(r'<pre id="pf-verify">(.*?)</pre>', result.stdout, re.S)
            if not block:
                print(f"{label:38s} -> no answer")
                continue
            body = html.unescape(block.group(1)).split(BEGIN, 1)[1].split(END, 1)[0]
            found = json.loads(body)
            print(f"{label:38s} -> {'DARK' if found['dark'] else 'light':5s}  {found['bg']}")
    print("\nUse a flag that reports DARK for the dark states. Chrome 151: `=0` and no flag.")
    return 0


def theme_failures(path: Path, painted: dict[str, str]) -> list[Failure]:
    """Did the PAGE respond to each state, or only the browser?

    Entering a state proves nothing about the stylesheet. Theme has three states, not two: an
    explicit reader choice stamps data-theme on the root, and the default "system" setting
    stamps nothing, so a colour defined only inside a [data-theme] block never applies to most
    readers — and a media query that is not guarded against an explicit *light* choice ignores
    a reader who asked for light on a dark machine. Both mistakes are invisible unless the
    painted colour is compared across the states, which is what this does.
    """
    failures: list[Failure] = []
    have = set(painted)
    if {"system-dark", "system-light"} <= have:
        if painted["system-dark"] == painted["system-light"]:
            failures.append(Failure(path.name, "system-dark/light",
                f"the page paints {painted['system-dark']} whether the system asks for dark or "
                "light — with no explicit choice stamped, which is most readers, only "
                "prefers-color-scheme separates the two"))
    for explicit, system in (("explicit-dark", "system-dark"), ("explicit-light", "system-light")):
        if {explicit, system} <= have and painted[explicit] != painted[system]:
            failures.append(Failure(path.name, explicit,
                f"an explicit data-theme choice paints {painted[explicit]} where the same theme "
                f"chosen by the system paints {painted[system]} — the reader's toggle does not "
                "win against the opposite system preference"))
    return failures


def check(path: Path, theme: str, viewport: tuple[int, int], timeout: int) -> list[Failure]:
    report, errors = run(path, theme, viewport, timeout)
    page = path.read_text()
    _, payload = payload_of(page)
    failures = [Failure(path.name, theme, f"console: {e}") for e in errors]

    # Did the browser actually enter the state it was asked for? This harness once rendered
    # light in both of its two "theme states" for weeks' worth of runs, because the enum in the
    # note it was written from was backwards. A state that cannot be entered is worse than a
    # state that is not tested, since it reports a pass.
    wants_dark = theme.endswith("dark")
    got_dark = report["data_theme"] == "dark" or (
        report["data_theme"] != "light" and report["system_dark"]
    )
    if wants_dark != got_dark:
        failures.append(
            Failure(path.name, theme,
                    f"the browser never entered this state — asked for "
                    f"{'dark' if wants_dark else 'light'}, got system_dark="
                    f"{report['system_dark']}, data-theme={report['data_theme']!r}, "
                    f"background {report['background']}. Run --probe-themes.")
        )

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
        where = (
            f"slide {section['index']} of {report['slides']} ({section['id']})"
            if section.get("index")
            else f"<{section['id']}>"
        )
        failures.append(
            Failure(path.name, theme,
                    f"{where} overflows by {section['dx']}px across and "
                    f"{section['dy']}px down")
        )

    for label, selector, expected in expectations_for(page):
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
    return failures, report


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
        choices=(*THEMES, "all"),
        default="all",
        help="which theme state to render (default: all four — system dark and light, plus an "
        "explicit choice against the opposite system preference, because a colour defined in "
        "only one state is a bug in the others)",
    )
    parser.add_argument(
        "--probe-themes",
        action="store_true",
        help="report what each theme flag actually does in the installed Chrome, and exit. Run "
        "this rather than trusting any note about the preferredColorScheme enum.",
    )
    parser.add_argument("--timeout", type=int, default=90, help="seconds per render")
    args = parser.parse_args(argv)

    width, height = (int(n) for n in args.viewport.lower().split("x"))
    if args.probe_themes:
        return probe_themes()
    themes = list(THEMES) if args.theme == "all" else [args.theme]

    failures: list[Failure] = []
    for path in args.pages:
        if not path.is_file():
            raise SystemExit(f"{path} does not exist — build it first")
        painted: dict[str, str] = {}
        for theme in themes:
            found, report = check(path, theme, (width, height), args.timeout)
            painted[theme] = report["background"]
            state = f"{len(found)} problem(s)" if found else "ok"
            print(f"{path.name} · {width}×{height} · {theme} · {state}")
            failures.extend(found)
        failures.extend(theme_failures(path, painted))

    if not failures:
        print(f"\nall clear: {len(args.pages)} page(s) × {len(themes)} theme state(s)")
        return 0
    print(f"\n{len(failures)} problem(s):", file=sys.stderr)
    for failure in failures:
        print(f"  {failure}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
