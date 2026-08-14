# Rebuilding Property Watch from scratch

*A narrative construction plan. Written 2026-08-14, after four weeks and eighteen sweeps of
running the real thing. This document assumes the reader is a person driving an agentic
coding assistant (Claude Code or similar): you describe each commit, the assistant builds
it, you judge the result. Every commit below is sized so that one round of that loop
produces it — roughly 45 commits from empty folder to deployed site, each one runnable,
each one telling the next chapter of the story.*

---

## Part I — The post-mortem: what the first build taught us

Before rebuilding anything, be honest about the building you have. The current repo is
9,471 lines of Python plus 2,049 lines of tests. Here is how the weight actually
distributes, and what that distribution says:

| Subsystem | Lines | Verdict |
|---|---|---|
| Core pipeline — adapter, sweep, store, geo, config, models | 839 | **The jewel.** Small, boring, correct. |
| Valuation — stats, baseline, predictions, newcon, segments, enrich | 1,196 | Sound method, right size. |
| Money core — costmodel | 212 | Right size, one modelling flaw (below). |
| Reporting — webmap, report, cma, digest, notify, runlog | 3,398 | **Bloated.** Mostly HTML and JavaScript inside Python strings. |
| Lending annex — underwriting, loanproducts, loanpage, packet, amortize | 3,157 | **A second application** that moved in and never paid rent. |
| Command line | 669 | Sprawled to fourteen commands, some one-offs. |

### What earned its place — carry these forward unchanged

1. **The adapter seam.** One module talks to the internet (`adapters/zillow.py`). Raw
   responses are validated by pydantic with `extra="ignore"`, a validation failure raises
   `SchemaDrift` instead of persisting garbage, and everything downstream consumes one
   frozen `Listing` dataclass. This single decision is why 121 tests run in 4.4 seconds
   with zero network and zero quota cost — the tests talk to a fake transport that
   replays recorded responses. It is also why Zillow renaming fields (which it does
   freely — it is scraped data) breaks loudly at the boundary instead of silently
   downstream.

2. **The two-table history model.** `properties` (one row per home ever seen — slowly
   changing identity) and `snapshots` (one observation per home per sweep). Two window
   queries — latest-per-home and previous-per-home — encode the whole product. Eighteen
   sweeps, 5,531 observations, 2.2 megabytes. Never once fought us.

3. **The radius as authoritative filter, and `"City, ST ZIP"` query strings.** A bare
   ZIP mis-resolves (we observed `76008` resolving to Minerva, Ohio). Geometry from a
   fixed centre point, with no-coordinates treated as *outside*, is what keeps every
   comparison honest.

4. **Writing the plan before the code.** The second commit in the repo's history — seven
   minutes after the first — was `EXPERT-PLAN.md`, a prose argument about what "a deal"
   even means. Every time the project wandered, that document pulled it back.

5. **Self-contained HTML as the only output format.** One file, everything embedded,
   opens without a server. This is the entire reason "deploy to the internet" was a
   ten-second copy instead of an infrastructure project, and why the private data never
   left the laptop.

6. **One `daily` command that runs everything.** Because the whole pipeline collapses to
   a single invocation, the computer's built-in scheduler could take over. Automation was
   a ten-line change, not a milestone.

7. **Honesty labels on the data.** Texas discloses no sale prices, so the sold baseline
   runs on a proxy and *says so* (`basis="zestimate_proxy"`). Confidence tags degrade to
   "not scored" rather than guessing. This never embarrassed us; every corner we cut
   silently did.

### What fought us — fix these in the rebuild

1. **HTML inside Python strings.** `webmap.py` is 1,334 lines, 75 of them containing
   embedded `<script>`/`<style>`/`</div>` fragments; `report.py`, `loanpage.py` and
   `packet.py` repeat the pattern. Editing a page means editing a Python string with no
   syntax highlighting, no linting, and merge conflicts everywhere. The fix was proven
   late in the project's own life: the new-construction report was built as **a template
   file plus a JSON payload plus a tiny build script** (`scripts/newcon/`), and it was
   dramatically easier to iterate. The rebuild uses that split for every page from day one.

2. **The lending annex ate the house.** Underwriting, loan products, the borrower packet
   and amortisation total 3,157 lines — a third of the codebase — serving exactly one
   family's financing decision. Worse, it is *coupled*: `borrower` appears in `config.py`
   and `cli.py`, and the command line mentions the annex nineteen times. It also carried
   its own bugs (a stale conforming-loan limit in one file while three others were
   current; a hard-coded veterans-benefit eligibility assumption). The rebuild makes it a
   **separate package that depends on core** — core never imports it, and a user who only
   wants market watching never installs numpy-adjacent lending code.

3. **The special-assessment model couldn't express reality.** `special_assessment_pct`
   is a percentage, but the Walsh Public Improvement District charges a **fixed dollar
   principal per lot** — and at two different tiers depending on improvement area
   ($3,271/year on a new 70-foot lot versus $928 on an early-phase one). We forced a
   fixed-dollar fact through a percentage field and knowingly mispriced early-phase
   resales by ~$200/month. The rebuild's cost model accepts *either* a percentage *or*
   flat dollars per year, per watch and per property.

4. **We built the first deal signal on the Zestimate and had to throw it away.** The
   plan document said the Zestimate was unusable here (list prices re-anchor to it; half
   of resales have none) — and we built `_deal_signal()` on it anyway as a "placeholder"
   that then had to be publicly deprecated. Placeholders become load-bearing. The rebuild
   ships *no* valuation until the sold-comps baseline exists.

5. **Data-quality knowledge stayed tribal.** The listing feed rounds half-baths **up**
   (27 of 68 builder plans were wrong, all in the same direction), reports **base**
   square footage while plans run to a maximum (one plan: 4,121 listed, 4,896 real),
   and occasionally double-lists the same home mid-construction at two addresses. We
   discovered each of these late, in research, after they had already skewed models. The
   rebuild has a `dataquality` module whose whole job is to detect, flag and correct
   these — and the flags travel with each record into every report.

6. **Quota knowledge lived in documentation.** The ~1,000-calls-per-month budget, the
   0.35-second politeness delay, the max-pages tuning — all real constraints, all
   scattered across `CLAUDE.md` and `docs/scheduling.md`. One live sweep during
   development costs real budget; nothing structurally prevented an expensive mistake.
   The rebuild makes the budget an object: the adapter refuses to exceed a per-run call
   ceiling, and `daily` accounts for spend.

7. **Two report pipelines with a fallback dance.** The map report became the standard,
   the old dashboard became the `--classic` fallback, and `cli._standard_report_html`
   arbitrates between them. The rebuild has **one** report pipeline that degrades
   gracefully *internally* (no model? render the page without the model section).

8. **Small frictions worth designing away:** the virtual environment broke when the
   folder moved (hard-coded interpreter paths — document recreation, or use a tool that
   rebuilds it); schema changes relied on "additive only" plus hand migrations (add a
   `schema_version` table and a migration stub on day one); three separate deploy
   targets accreted (unify under one publishing script with an explicit manifest of what
   is public and what is password-protected).

---

## Part II — Ten rules that order the rebuild

These are the post-mortem, compressed into instructions. They dictate the commit order
below.

1. **The seam before the storage; the network last.** The adapter and its fake transport
   exist before the database. The first *live* call to the real service happens at
   commit 15, after everything around it is tested. Recorded fixtures, replayed forever.
2. **A plan document is a commit.** Prose about method precedes code that implements it.
3. **Walking skeleton.** At the end of every stage the app runs end-to-end and does
   something a person could use. No stage ends mid-air.
4. **Template + payload + builder, for every page.** No HTML in Python strings, ever.
5. **Money models must express reality.** Percentage *or* flat-dollar assessments, both
   first-class, from the first commit of the cost model.
6. **No valuation on the Zestimate.** The first deal signal waits for sold comps. Every
   number carries its basis label.
7. **Data quality is a module, not a memory.** Detections ship as code with tests.
8. **The quota is an object.** Budget enforced by the adapter, spent intentionally.
9. **Personal finance stays out of core.** The lending annex is a separate package;
   core never imports it.
10. **Tests never touch the internet.** If a test needs the network, it is not a test —
    it is a live run, and live runs cost money.

---

## Part III — The build, stage by stage

Each commit below is one loop with the assistant: describe it, let it build, run the
proof, commit. Proof lines assume `.venv/bin/python -m pytest -q` as the standing check —
it should pass at **every single commit**.

### Stage 0 — A repo that can hold the work *(commits 1–3)*

The mistake to avoid here is flat dependencies: the original made everyone install the
full scientific stack (numpy, scipy, scikit-learn, statsmodels, pandas) just to sweep a
market. Split them as extras from the start.

- **Commit 1 — `init: package skeleton, pyproject with core/stats/dev extras`.**
  Empty `propertywatch/` package, `pyproject.toml` with three dependency groups:
  `core` (httpx, pydantic, pydantic-settings, sqlalchemy, pyyaml), `stats` (the
  scientific stack), `dev` (pytest). A `README` stub saying what the tool will be in
  three sentences. *Proof: `pip install -e .` succeeds without numpy.*
- **Commit 2 — `docs: the methodology plan, before any code`.** Rewrite
  `EXPERT-PLAN.md` knowing what we now know: sold comps are the anchor; the Zestimate is
  banned from valuation; Texas discloses nothing, so every basis gets a label; builder
  plan-sheets are ask-curves, not homes. This document is the argument the code will
  implement. *Proof: a colleague can read it and say what the tool judges and how.*
- **Commit 3 — `config: settings from .env, watches from YAML, validated`.**
  `Settings` via pydantic-settings (API key, database URL, mail credentials);
  `Watch`/`WatchConfig` from YAML with the `"City, ST ZIP"` rule enforced by a
  validator that *rejects bare ZIP queries* with a message explaining the Minerva, Ohio
  incident. `build_engine()` sets the write-ahead-log and foreign-key pragmas. *Proof:
  a malformed watch fails loudly with a useful error.*

### Stage 1 — The only door to the internet *(commits 4–7)*

Everything in this stage is written and tested **without an API key**.

- **Commit 4 — `adapter: search endpoint with pydantic validation and SchemaDrift`.**
  Models with `extra="ignore"`; an HTTP-200 body that fails validation raises
  `SchemaDrift` and persists nothing. *Proof: a mangled fixture raises; a good one parses.*
- **Commit 5 — `test: fake transport and golden fixtures`.** One real search response
  captured once (by hand, from documentation or a single manual call), stored under
  `tests/fixtures/`, replayed through `httpx.MockTransport`. This transport is the
  test suite's internet for the rest of the project's life. *Proof: adapter tests pass
  in milliseconds, offline.*
- **Commit 6 — `adapter: the Listing seam`.** A frozen dataclass — id, address,
  coordinates, price, beds/baths/sqft, status, the handful of estimate fields — and a
  converter from the validated response. Downstream code imports `Listing` and nothing
  else from the adapter. *Proof: the converter round-trips the fixture.*
- **Commit 7 — `adapter: call counting, politeness delay, budget ceiling`.** The
  request counter and the 0.35-second delay from the original, plus what the original
  lacked: a per-run call budget passed in at construction; exceeding it raises before
  the request is sent. *Proof: a test shows the eleventh call of a ten-call budget
  refusing.*

### Stage 2 — Memory *(commits 8–11)*

- **Commit 8 — `store: properties and snapshots`.** The two tables exactly as proven:
  identity versus observation, unique on (home, watch, sweep-time), timestamps as UTC
  ISO strings so they sort as strings. *Proof: schema creates; duplicate snapshot
  rejected.*
- **Commit 9 — `store: latest-per-home and previous-per-home window queries`.** The
  heart of the history model, as two pure functions returning plain dictionaries.
  *Proof: synthetic three-sweep data returns the right rows.*
- **Commit 10 — `store: schema_version and a migration runner`.** A one-table version
  stamp and a folder of numbered migration scripts, applied in order by `init`. The
  original's "additive changes only, hand-run column adds" worked until it didn't.
  *Proof: a fake migration bumps the version once and never re-runs.*
- **Commit 11 — `test: store round-trips under churn`.** Homes appearing, changing
  price, vanishing, returning. *Proof: the window queries stay correct through all of it.*

### Stage 3 — The sweep *(commits 12–15)*

- **Commit 12 — `geo: haversine radius, absent coordinates are outside`.** Twenty-six
  lines in the original; keep it that small. *Proof: inside/outside/no-coords tests.*
- **Commit 13 — `sweep: collect — fan out queries, dedupe nearest-wins, filter to radius`.**
  Also the loud warning that saved us once: a query returning listings with *zero*
  in-radius means a mis-resolved place string, not an empty market. *Proof: overlapping
  fixture queries dedupe correctly; the warning fires on a mis-resolve fixture.*
- **Commit 14 — `sweep: persist in one transaction, parents before children`.** Upsert
  all properties, flush, then insert snapshots — narrating in a comment the autoflush
  foreign-key failure this prevents. Returns the diff summary against the previous
  sweep. *Proof: a mid-sweep failure leaves the database untouched.*
- **Commit 15 — `cli: init, watches, sweep — first live run`.** Three commands only.
  This is the walking skeleton complete, and the project's first real API call. Run it
  once against one watch; commit the moment it prints its first diff. *Proof: a real
  sweep lands N homes and prints "N new, 0 changed" — and the budget object reports the
  spend.*

### Stage 4 — The first page *(commits 16–18)*

- **Commit 16 — `report: payload builder`.** A pure function: database in, one JSON
  document out — homes, medians, the sweep date. No HTML anywhere near it. *Proof: the
  payload snapshot-tests cleanly.*
- **Commit 17 — `report: template and page builder`.** An HTML template file with
  `/*__PAYLOAD__*/` token, a builder script that splices JSON into it, a self-contained
  output. Light and dark themes from the start; every number labelled in plain English.
  *Proof: the page opens from the filesystem with no network.*
- **Commit 18 — `cli: report — dated archive plus canonical latest`.**
  `reports/<watch>-YYYY-MM-DD.html` and `reports/<watch>.html`, same bytes. *Proof: two
  runs on one day are idempotent.*

### Stage 5 — History is the product *(commits 19–21)*

- **Commit 19 — `store: sweep_changes — new, cuts, rises, status flips, gone`.**
- **Commit 20 — `report: the movement strip`.** "Since the last sweep: 3 cuts, 5 new,
  1 gone" at the top of the page; degrades to "history begins today" on a single-sweep
  database. *Proof: both states render from fixtures.*
- **Commit 21 — `store: cumulative price-change map`.** First-ask versus current-ask per
  home — the "cut $83,000 since July" number that turned out to be the single most
  persuasive figure in every report we shipped.

### Stage 6 — Money *(commits 22–24)*

- **Commit 22 — `costmodel: monthly carry with assessments as percent OR flat dollars`.**
  Principal-and-interest, tax, insurance, dues — and the fix from post-mortem item 3:
  a special assessment is `{"pct": 0.35}` *or* `{"flat_annual": 3271}`, per watch and
  overridable per property. Round half-up so a JavaScript recomputation on the page
  agrees with Python to the dollar (a real bug we hit). *Proof: parity test between the
  two rounding worlds; both assessment forms compute.*
- **Commit 23 — `config: per-watch finance merges over global`.** Merge, don't replace —
  the original got this wrong once and every per-watch block silently dropped global
  defaults. *Proof: a watch overriding one field keeps the other nine.*
- **Commit 24 — `report: carry column and a verified-tax appendix`.** Bake the verified
  numbers in with their sources (for Walsh: the 2.339427% ad-valorem stack from the
  taxing entities' adopted rates; the two-tier district assessment). The lesson: we ran
  for weeks on a guessed 2.9% before verifying, and the correction moved every monthly
  number by hundreds of dollars. Verify early; cite in the page footer.

### Stage 7 — Valuation, without the Zestimate *(commits 25–29)*

Now — and only now — the `stats` extra gets installed.

- **Commit 25 — `baseline: sold-comps dollars-per-foot with basis labels`.** A sold
  watch's latest snapshots → per-segment percentiles and velocity. Where prices are
  undisclosed (Texas), the proxy basis is tagged on the object and surfaced in every
  page that uses it. Plan-sheets and land excluded. *Proof: basis label follows the data
  through to rendered output.*
- **Commit 26 — `stats: hedonic model`.** Ordinary least squares of log price on log
  square-footage, beds, baths, home type. The original found a size elasticity near
  0.83 — which is *why* raw price-per-foot misleads and why this model exists. Expected
  price, prediction band, standardised residual. *Proof: synthetic data with known
  coefficients recovers them.*
- **Commit 27 — `stats: nearest-neighbour comps and the spatial residual adjustment`.**
  Independent K-nearest-neighbours within a size band, plus the location adjustment that
  stopped the original's false "great deal" flags in premium pockets: smooth the base
  model's residuals over the map and add them back. *Proof: a synthetic cheap pocket no
  longer flags as a bargain.*
- **Commit 28 — `deals: fuse into a scored card with confidence and condition flags`.**
  The 0–100 score with a traceable ledger (base 50 plus each named adjustment), and the
  flags — statistical outlier, stale, distressed, land — that separate "underpriced"
  from "there is a reason". *Proof: ledger sums to score, always.*
- **Commit 29 — `predictions: freeze, resolve, calibrate`.** Record an expected price
  per active home each sweep; resolve when it appears sold; report error per segment,
  counting real-price resolutions separately from proxy ones. The honesty loop goes in
  *now*, not as an afterthought — it is the only way to know whether commit 26 earns
  its keep.

### Stage 8 — Enrichment *(commits 30–31)*

- **Commit 30 — `enrich: detail pulls with attempt-stamping and bounded batches`.**
  Year built, lot size, dues, tax rate from the detail endpoint — one call per home, so
  budgeted. The endpoint fails ~1 in 5; stamp every *attempt* so retries do not hammer,
  and let coverage fill across passes. *Proof: a failing fixture consumes its attempt and
  is not retried the same run.*
- **Commit 31 — `stats: enriched model when coverage clears the bar, per-home fallback`.**
  Age and lot-size predictors join only when ≥60% of comps carry them; each home is
  scored by the best model *available for it*. Partial data degrades gracefully instead
  of gating the feature.

### Stage 9 — Neighbourhood truth *(commits 32–33)*

- **Commit 32 — `segments: subdivision membership from three offline signals`.** Plan
  community name, curated street allowlist, address token — because the detail
  endpoint's subdivision field is absent on ~80% of lookups and all new construction.
  The allowlist is data, not code: a YAML file with a comment telling the maintainer how
  to triage additions.
- **Commit 33 — `sweep: subdivision applied after radius, dropped counts logged`.**
  Geometry first (excludes same-named streets elsewhere), membership second, and a log
  line whenever members are dropped — that line is the maintenance signal.

### Stage 10 — New construction, and the data-quality module *(commits 34–36)*

- **Commit 34 — `newcon: split plan-sheets from spec homes, build the ask curve`.**
  Plan-sheets (address contains "Plan,") are the builder's price list; specs are the
  purchasable homes scored against it.
- **Commit 35 — `dataquality: the feed's known lies, as code`.** This module is the
  rebuild's biggest addition, and it exists because every item in it burned us:
  half-bath counts rounded up (systematic, one-directional — 27 of 68 plans wrong);
  square footage reported as base while plans run larger (one plan had 775 square feet
  of hidden headroom); duplicate listings of one home at two addresses (match on
  identical price and footage); builder attribution absent entirely (attribute from
  description text, plan-name matching, and price-ending signatures — and carry a
  confidence tier, never a bare guess). Every correction travels with the record as a
  flag the reports can show. *Proof: fixtures reproducing each real incident.*
- **Commit 36 — `newcon: score specs against the plan ask, cuts and staleness weighted`.**

### Stage 11 — The map report *(commits 37–39)*

- **Commit 37 — `webmap: payload assembly`.** Deal cards, comps, movement, market
  medians — one JSON document, snapshot-tested. Still no HTML.
- **Commit 38 — `webmap: template as real files`.** The Leaflet page — markers coloured
  by score, cross-filtering sidebar, score ledger, methodology section — written as an
  actual `.html` file with its JavaScript in actual `<script>` blocks, spliced with the
  payload by the same builder pattern as Stage 4. This is the commit that fixes the
  1,334-line monolith. *Proof: edit the template in an editor with highlighting; the
  build script splices and the page renders offline.*
- **Commit 39 — `report: one pipeline, graceful internal degradation`.** No `--classic`
  twin: if the hedonic model cannot fit (too few solds), the same page renders without
  the model-dependent sections and says why. One code path, honest about what it has.

### Stage 12 — Automation *(commits 40–42)*

- **Commit 40 — `digest: pure summary over the database; notify: the mail send`.**
  Build separated from send, so the digest is testable without an outbox.
- **Commit 41 — `cli: daily — sweep all, predict, rebuild, digest, within budget`.**
  The orchestrator asks the budget object for the month's remaining spend and trims
  page depth before it trims watches.
- **Commit 42 — `docs: scheduling`.** The launchd/cron recipes, and the quota
  arithmetic that decides cadence, in one place.

### Stage 13 — Publish *(commits 43–45)*

- **Commit 43 — `site: build a publishable folder from an explicit manifest`.** The
  manifest names every page and marks it `private` or `public`. Copying is allowlist-
  only — the secrets file and database are unpublishable by construction, and adding
  a page means declaring its visibility on purpose.
- **Commit 44 — `site: edge authentication, fail closed`.** Password required before
  anything private is served; if the password variable is unset, everything returns
  unauthorised. Public carve-outs come only from the manifest. Public pages send
  do-not-index headers — reachable by link, invisible to search.
- **Commit 45 — `deploy: one command to the hosting service`.** Dated archives cached
  immutably, latest pages revalidating, and the deploy script callable from `daily` so
  the published site refreshes with the morning run.

### Stage 14 (optional annex) — Lending, as its own package *(commits 46+)*

If the financing engine is wanted again, it is built as `propertywatch-lending`: its own
package, its own tests, importing core's cost model — **never the reverse**. The borrower
profile lives in the annex. Shared constants that drift (the conforming-loan limit burned
us at two different values in two files) live in exactly one module with the year in the
name. Core's command line grows one lazy-loading subcommand group, present only when the
annex is installed.

---

## Part IV — What the rebuild deliberately leaves out

- **No always-on server, no cloud database, no framework.** The static-page model is a
  feature, not a shortcut. The moment a page needs a live backend, that page has outgrown
  this tool.
- **No per-property alert stream.** The daily digest was a deliberate choice against
  notification fatigue, and it held up. One email a day.
- **No Zestimate-derived valuation, ever.** It may appear as a *displayed* reference
  field, labelled; it may not enter a score.
- **No speculative generality.** One provider adapter, built for the provider we pay.
  The seam makes a second provider a contained project if it is ever real.

## Appendix — Constants worth carrying over (verified, with dates)

- Walsh ad-valorem stack, tax year 2025, verified against adopted rates 2026-08-06:
  **2.339427%** (school district 1.1942, city 0.6700, county+road 0.2851, college
  0.1061, hospital 0.0841).
- Walsh improvement-district assessment, from the adopted service-and-assessment plan
  (May 2026 update): new 70-foot lot ≈ **$3,271/year** ($40,193 principal, to 2056);
  60-foot ≈ $2,642/year; early-phase 70-foot ≈ **$928/year** remaining. Two-tier; ask
  per lot, in writing.
- Query rule: always `"City, ST ZIP"`, never a bare ZIP.
- Politeness delay 0.35 s between calls; monthly budget target ≤1,000 calls for a
  single-community watch pair on a daily-ish cadence.
- 2026 conforming-loan limit: **$832,750** (annex only).
- Feed corrections: half-baths round up; square footage is base-of-range; duplicate
  listings share price+footage across two addresses; builder attribution requires
  description text or plan matching, with confidence tiers.
