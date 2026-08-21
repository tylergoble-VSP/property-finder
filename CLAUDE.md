# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A housing-market watcher. Once a day it sweeps listings around a point on a map, stores every
observation, compares today against every previous day, and renders self-contained HTML pages
that are published as static sites. Nothing server-side ever runs the Python.

Two documents are load-bearing and should be read before any non-trivial change:

- **`docs/EXPERT-PLAN.md`** — the methodology. What counts as "a deal" and why. When the code
  and this document disagree, the document is usually right.
- **`docs/REBUILD.md`** — the post-mortem of the predecessor tool (`property-watch`) that this
  repo is a ground-up rebuild of. Its numbered post-mortem items are cited by name throughout
  the source; when a module docstring says "post-mortem item 3", that's this file.
- **`docs/PORTING-THE-REPORTS.md`** — numbered lessons (1–16) from porting the report/publish
  work over from the wrong repo. Also cited by number throughout the source.

Module docstrings in this codebase are unusually long and carry real design rationale — the
*why*, and the specific production bug that shaped the code. Read the docstring before editing
a module; most "obvious simplifications" here are things that already shipped and broke.

## Commands

```bash
uv venv --python 3.12 && source .venv/bin/activate
uv pip install -e ".[stats,dev]"

.venv/bin/python -m pytest -q                        # whole suite, offline, ~12s (580 tests)
.venv/bin/python -m pytest tests/test_deals.py -q    # one file
.venv/bin/python -m pytest tests/test_deals.py::test_name -q
.venv/bin/python -m pytest -q -k "public or manifest"

propertyfinder init                  # create/update the SQLite schema (idempotent)
propertyfinder watches               # list configured watches
propertyfinder sweep [--watch NAME] [--budget N]     # SPENDS REAL API QUOTA
propertyfinder report [--watch NAME] [--kind map|table|newcon] [--public]
propertyfinder map [--watch NAME] [--public]
propertyfinder predictions           # model calibration so far
propertyfinder enrich [--watch NAME] [--limit N]     # SPENDS REAL API QUOTA
propertyfinder daily [--no-sweep] [--deploy]         # the scheduled job's whole work

.venv/bin/python scripts/build_site.py               # reports/ -> site/, per site-manifest.yaml
bash scripts/deploy.sh                               # build site/, vercel deploy, verify from outside
bash scripts/publish_ledger.sh                       # the annex's agent ledger (own Vercel project)
bash scripts/publish_crockett.sh                     # the Crockett shortlist (own Vercel project)
.venv/bin/python scripts/verify_page.py reports/x.html   # render in headless Chrome and check
.venv/bin/python scripts/verify_deploy.py https://…      # fetch a live deploy as a visitor
```

The annex sub-project has its own suite and console script, run from its own directory:

```bash
cd annex/agent-finder && ../../.venv/bin/python -m pytest -q      # 29 tests
agentfinder init | sweep | resolve | report
```

There is no linter or formatter configured. `pytest` is the only gate.

Prefer `.venv/bin/python …` over relying on an activated venv — the shell scripts do, and a
moved project folder leaves a `.venv` that exists and cannot run (see `scripts/daily.sh`).

## Money and quota — the constraint that shapes everything

Sweeps and enrichment cost real money per call against a shared monthly allowance
(`QUOTA_CAP_SEARCHAPI_MONTHLY`, default 1000). Consequences that are structural, not stylistic:

- **Never make a real network call from a test, a script you run to "check something", or an
  exploratory command.** The whole suite runs offline against golden JSON in `tests/fixtures/`
  served through `httpx.MockTransport` (`tests/conftest.py`).
- `propertyfinder.budget.CallBudget` is passed to whatever might spend. The adapter asks it
  before every request and raises `BudgetExceeded` rather than send. Enforcement is an object,
  never a sentence in a doc.
- The real `httpx.Client` is constructed in exactly one place per project — `cli.py`'s `main`
  (and `agentfinder/cli.py`) — and injected downward. Do not construct one anywhere else.
- `report`, `map`, `predictions`, and every `build_*_payload` are pure reads and spend nothing.
  Rebuilding a page after a wording change is free; refreshing data is a separate deliberate act.

## Architecture

### The pipeline

```
adapters/zillow.py  →  sweep.py  →  store.py  →  *data.py (payload)  →  pagebuild.render  →  reports/*.html
  (only door to        (radius +     (2 tables,     (pure dict, no        (splices dict         → build_site.py → site/
   the internet)        dedupe)       history)       markup)               into a template)        → vercel
```

**One door to the internet.** `propertyfinder/adapters/` is the only package that makes HTTP
calls or knows the provider's vocabulary. A 200 whose body fails pydantic validation raises
`SchemaDrift` and persists nothing. Everything downstream consumes the frozen `Listing`
dataclass and never sees raw JSON.

**Two tables are the product.** `properties` (identity, one row per home ever seen) and
`snapshots` (observation, one row per home per watch per sweep) — `propertyfinder/domain/models.py`.
Every claim the tool makes is one of three query shapes over these: latest-per-home,
previous-per-home, or all-in-order. Identity fields are **backfilled, never overwritten by
absence** (`store._REFRESHABLE`): a later "unknown" must not erase an earlier fact.
`predictions` is the third table — claims the tool made, recorded before the answer was known.

**Payload/template/builder split is law, with an enforcer.** `*data.py` modules
(`reportdata`, `mapdata`, `newconreport`, `digest`, annex `reportdata`) are pure: session in,
JSON-able dict out, not one angle bracket. All markup lives in `propertyfinder/templates/*.html`.
`pagebuild.render` splices exactly one `/*__PAYLOAD__*/{}` token — zero or two raises. Never
build HTML in Python here; that is the single biggest thing the rebuild exists to undo.

- `{{VENDOR:name}}` inlines a pinned file from `templates/vendor/` (Leaflet 1.9.4). Pages must
  stay one self-contained file that opens from a filesystem with nothing fetched at load time.
- Templates may not state a number the database owns. `tests/test_template_prose.py` fails the
  build if static template text contains a month name, an "N days/sweeps" phrase, or an ISO
  date. Windows, counts and dates go in the payload and get interpolated inside `<script>`.

**Degrade, do not die.** Every page rests on things that may be absent: no sold companion, too
few sales to fit a model, one sweep on record, no builder price list, `stats` extra not
installed. None is an error. Each becomes `fitted: false`, `null`, or an empty list, plus a
*note* printed on the terminal line saying what the page had to do without. `stats` imports are
deliberately deferred (`mapdata`, `cli._fit_for_predictions`) so a core-only install still runs.

**One report pipeline, no arbitration.** `cli._kind_for` answers one question — does this watch
have a sold companion to value against — and that picks `map` vs `table`, printing why either
way. `--kind` overrides the choice; nothing overrides the honesty. Do not add a second report
builder (post-mortem item 7).

### Valuation, in two tracks

- **Resale** — `baseline.py` (sold comps) → `stats.py` (OLS of log price on log size/beds/
  baths/type; `z` = standardised residual) → `deals.py` (0–100 score plus a **ledger** whose
  entries sum to the score exactly, including any clamp). `predictions.py` freezes each
  expectation and marks it when the home later shows up sold.
- **New construction** — `newcon.py`. Builder plan-sheet rows (address contains `"Plan,"`) are
  an ask curve, not homes: excluded from every resale statistic and from the map's `listings`,
  and used as the yardstick for spec homes instead, sqft-indexed (±20% band, falling back to
  community-wide with `n_in_band: 0` and LOW confidence).
- **The published site estimate is banned from valuation.** It may be displayed and labelled;
  it may never enter a score. Asking prices re-anchor to it, so scoring against it measures how
  closely the seller read the same web page. Tests hold `deals.py` to this.

### Config: two separate worlds

- `Settings` (pydantic-settings, from `.env`) — secrets and machine-specific paths only.
- `watch-config.yaml` → `WatchConfig` — the markets a user edits and version-controls.

`WatchConfig.finance_for(watch)` **merges** a watch's finance block over the global one using
`model_fields_set` (not "fields differing from defaults"). Replacing instead of merging silently
mispriced a market for weeks. Nested blocks (e.g. `special_assessment`) are one fact and are
replaced whole.

A `SpecialAssessment` carries *either* `pct` *or* `flat_annual`, never both — the Walsh PID
apportions fixed dollars per lot, and forcing that through a percentage field mispriced early-
phase homes by ~$200/month (post-mortem item 3).

Watch queries must be `"City, ST ZIP"`. A bare ZIP is rejected at load time by a validator: the
provider resolved `76008` (Texas) to Minerva, Ohio.

**The radius is authoritative** (`geo.py`): whichever query surfaced a listing, outside the
circle is discarded, and **a listing with no coordinates is outside** — never guessed in.
`segments.py` narrows a circle to a named subdivision (plan-sheet community, street allowlist in
`data/<key>-streets.yaml`, address token), and runs *after* geometry.

**`criteria.py` is applied when the report is built, never when the market is swept.** A sweep
costs money and cannot be undone; a report is free. Screening at render time means one purchase
answers every brief that market is ever asked, including "what did the filter throw away" —
`Screening.dropped` counts each exclusion by reason, and a missing number *fails* the test
(`sqft_unknown` is a fact about the feed, not about the market).

`dataquality.py` encodes the feed's known lies (half-baths round up, sqft is the base of a
range, one home under two zpids, there is no builder field). Nothing mutates silently:
`apply_corrections` returns a corrected copy and the `DataQuality` record travels with the home
into the page, so listed and verified values both survive.

`newconreport.py` has one deliberate seam: every top-level payload key is **derived** and
recomputed on every build, except exactly `curated`, read from `data/walsh-newcon-curated.yaml`
and never written by a build. `tests/test_newconreport.py` asserts that sentence.

Times are always UTC `"%Y-%m-%dT%H:%M:%SZ"` strings (`timeutil.py`) — fixed-width so string
order *is* chronological order, which is what the window queries rank on. Local time is never
stored. `daily` uses one `now` for the whole run so pages, predictions and digest agree.

### Migrations

`propertyfinder/migrations/mNNN_*.py` exposing `apply(conn)`, discovered by scanning the
package. Never edit an applied migration — write the next number. Every step must be safe to
run twice (SQLite commits DDL implicitly, so a failed step leaves work behind; only the *stamp*
is transactional). Write SQL against a `Connection`, not today's mapped classes. `run_migrations`
is idempotent and every command calls it, so no user ever has to know their DB version.

### Publishing

`scripts/build_site.py` copies **only** paths named in `site-manifest.yaml`, and accepts exactly
one shape: `reports/*.html` under the repo root. That narrowness is the guarantee — `.env` and
`propertyfinder.db` are unpublishable by construction (post-mortem item 8). No globs. Do not
widen it for convenience.

Four declared deploy targets, each with a manifest of the same shape, each listed in
`docs/vercel.md`, each checkable by `scripts/verify_deploy.py`.
`tests/test_verify_deploy.py::DECLARED_MANIFESTS` fails if a manifest exists that the table in
`docs/vercel.md` does not mention. Adding a target means adding all three.

`visibility: private` sits behind Basic Auth in `site/middleware.js` (generated from
`templates/site-middleware.js`) and **fails closed** — unset `SITE_PASSWORD` means 401, not open.
`visibility: public` is direct-URL-only, `noindex`, never linked from the index, and must be
produced by `report --public` / `map --public`, which substitutes the model's own market-neutral
`FinanceAssumptions`. Public renders write a `-public` filename and private renders never do, so
the two cannot be confused by a typo or a rebuild. `tests/test_public_pages.py` walks every
`public` manifest entry and fails if its embedded payload holds anything beyond market
assumptions (lesson 9). Never hand-edit a config to make a public page.

### Verification that lives outside the suite

The suite is offline and browser-free by design, so it proves everything decided at build time
and cannot prove that a page's own script ran.

- `scripts/verify_page.py` renders in headless Chrome and checks for JS errors (read off
  stderr), placeholders leaking into visible text (`${`, `undefined`, `NaN`, `[[token]]`),
  element counts against the payload, and overflow at **1280×720** (a projector's reality).
  Headless Chrome ignores `--force-prefers-color-scheme`; use `--blink-settings=preferredColorScheme`
  and re-measure the enum with `--probe-themes` rather than trusting any note about it.
- `scripts/verify_deploy.py` fetches the **production alias** as a visitor: public paths return
  200 with real bytes, private paths are refused, `og:image` is absolute in the served response.
  Vercel SSO-gates the per-deployment URL and leaves the alias public, and `vercel deploy`'s
  success line distinguishes neither (lesson 12).

### Scheduling

Point a scheduler at `scripts/daily.sh`, never at the console script. It asserts its own
preconditions (directory enterable, interpreter present *and able to `import propertyfinder.cli`*,
config present) and exits 78 with one greppable line — because the plist invoked
`.venv/bin/propertyfinder` directly, the folder moved, and launchd swallowed exit 127 every
morning for a fortnight. `heartbeat.py` writes `reports/.last-daily` **whatever happened**
("it ran and failed" and "it never ran" are different problems); the digest reads it aloud and
`deploy.sh` warns when it is stale (lesson 16).

### The annex

`annex/agent-finder/` is a separate installable package that depends on `propertyfinder` and
reuses its seams (`Listing`, `CallBudget`, `SchemaDrift`, `geo`, `store`'s window-query idiom,
the migration runner numbered from m100, `pagebuild.render` with its own `templates_dir`).
**The dependency arrow points one way**: core must never import the annex. Post-mortem item 2
is a lending annex that grew into a second application inside the first — if annex work starts
wanting a change in core, that is the signal to stop and ask.

## Working in this repo

- Commits are meant to be read: one small tested step, with a message that says what it is
  *for*. Match the existing style — lowercase subject, a colon-prefixed area, prose.
- Add a CLI command when a person needs one, not when a module appears. The predecessor
  sprawled to fourteen, several of them one-offs that outlived their question.
- A number the tool cannot honestly produce is `None`/`null`, rendered "—". Never invent a
  value to fill a cell, and never pad a median with a guess.
- When a degradation happens, say so on the output line. The terminal note is the feature.
