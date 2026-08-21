# Porting the reports

*Written 2026-08-21. A body of report-building and publishing work — the Walsh Ranch
new-construction buyer report, the public deal map, the talk deck, and the publishing
runs behind all three — was done in the wrong repository: `property-watch`, the
deprecated original that `docs/REBUILD.md` is the post-mortem of. This document is the
plan for redoing that work in `property-finder`, the rebuild, where it should have
happened in the first place. It assumes the same reader as REBUILD.md: a person driving
an agentic coding assistant, one commit-sized step per loop. The difference is that this
time the mistakes have already been made, catalogued, and in most cases the mechanism
that prevents them is already sitting in this repo with tests on it. The job is not to
invent; it is to replicate, on the right foundations, with the lessons paid for.*

---

## Part I — What is live today, and where it builds from

Four pages are on the internet right now. All four were produced during the
wrong-repo period; only one of them is currently reproducible from this repository.

| Page | URL | Builds from | Status |
|---|---|---|---|
| The talk deck, "So Easy A Grunt Could Do It" (31 slides) | `so-easy-a-grunt-could-do-it.vercel.app` | `property-finder/site-talk/` | Right repo already. Also published as a claude.ai artifact. |
| The Walsh new-construction buyer report | `walsh-new-construction.vercel.app` | `property-watch/scripts/newcon/` | **The main port.** No equivalent template exists here. |
| The public, privacy-sanitised deal map | `walsh-deal-map.vercel.app` | rebuilt by hand from `property-watch` | Needs a first-class public build here. |
| The luxury listing-agent ledger | `walsh-luxury-agents.vercel.app` | `annex/agent-finder` (right repo) | Needs a declared publish path, not an ad-hoc upload. |

The data behind the buyer report, as of the live sweep of 2026-08-21 (12 API calls for
actives, 20 for solds): 145 live homes in the Walsh watch — 68 builder plan sheets, 39
ready-now spec homes, 37 resale actives. 54 price cuts totalling $2,498,799 across a
41-day observed window; 22 specs past 90 days on market; median spec ask $638,320 at
$240 per square foot; standing spec inventory grew 34 → 39 with 4 homes absorbed, about
13.3 months of supply. Those numbers are what the ported report must reproduce from this
repo's database on its first honest run — they are the acceptance test.

One more thing is live in a different sense: the researched builder roster for the 68
Walsh plan sheets. David Weekley Homes owns 20 plans, Drees Homes 18, Highland Homes 12,
Village Homes 10, GFO Home 8. The spec homes additionally involve Perry Homes, Britton
Homes, High Street Homes, and one genuine "Perry or Britton" ambiguity that no evidence
resolves. That roster cost real research effort — reading builders' own plan pages — and
it currently survives only as JSON files in the wrong repo (`plan_builder_map.json`,
`spec_builder_map.json`), which were themselves *recovered by parsing the embedded JSON
out of a previously published HTML report* after the working copies were lost. It enters
this repo as versioned data, first commit of the plan, before anything else moves.

## Part II — What this repo already provides

Most of what the wrong-repo scripts had to improvise exists here as a tested module.
The port's first discipline is to *use these* rather than translate the workarounds.

**The template/payload/builder split is law here, with a real enforcer.**
`propertyfinder/pagebuild.py` — `render()` splices exactly one
`/*__PAYLOAD__*/{}` token per template, refuses zero or two, escapes `</` so a payload
string can never truncate the page, and inlines `{{VENDOR:name}}` files from
`propertyfinder/templates/vendor/` (Leaflet 1.9.4 is already vendored there, pinned,
with its own README explaining the `</script` rule). The original `build_report.py`
spliced *three* tokens — payload, builders, plans — because its curated research lived
in separate JSON files. That design does not survive the port: `render()`'s
exactly-one-token contract means the payload builder merges everything into one dict,
which turns out to be the correct enforcement of the derived-versus-curated separation
anyway (Part IV, lesson 5).

**"On the market today" is already solved for listings.** `reportdata.build_payload`
and `mapdata.build_map_payload` both compute `sweep_ts = max(snapshot_ts)` and filter to
`active = rows where snapshot_ts == sweep_ts`. The 180-homes-when-145-were-live bug
(lesson 2) cannot happen in those two pipelines. It *can* still happen in
`newcon.compute_plan_baseline` and `newcon.score_specs`, which read
`latest_snapshot_rows` unfiltered — the same trap the original `refresh.py` had to
work around by reimplementing the baseline over an explicit row list. Fixing that in the
module, once, is a commit in Part V.

**Builder attribution already refuses to guess.** `dataquality.attribute_builder`
returns `(builder, tier)` where the tiers are CONFIRMED (the builder names itself in the
listing's own prose), INFERRED (exact plan-name or exact-footage match against a known
plan sheet), and UNRESOLVED — including, deliberately, when evidence points at *two*
builders, because ambiguous evidence is not weaker evidence, it is none. What it lacks
is a way for hand-researched truth to enter, which is the data file proposed in lesson 4.

**Bath corrections, duplicate detection, and the correction-provenance style all
exist.** `dataquality.bath_correction` applies verified counts with a staleness guard
(if the feed no longer shows the value that was verified against, the correction is
re-verify, not trust); `find_duplicates` catches the one-home-two-listings incident on
identical price + footage + near-matching address, and knows that two identical specs at
two real addresses on one street are two houses; `propertyfinder/data/bath-corrections.yaml`
is the provenance template every new data file copies — listed value, verified value,
source, date, and a header telling the maintainer how to add an entry and why the
`listed` field is the guard.

**New-construction mechanics are built and tested.** `newcon.py` splits plan sheets
from specs, builds the sqft-indexed ask curve (`comparable_ppsf` with its
band/community/none basis labels), and scores specs with a traceable ledger. It
deliberately dropped the original's rent-yield bonus ("two guesses in a trench coat").
The ported report consumes these; it does not reimplement them.

**Publishing has a manifest, an index, edge auth, and a deploy script.**
`site-manifest.yaml` names every published file and its visibility (`private` behind
`SITE_PASSWORD`, `public` reachable by direct URL with noindex headers — a real,
test-proven code path, per the manifest's own comments); `scripts/build_site.py` copies
*only* manifest-named `reports/*.html` paths, so the database and `.env` are
unpublishable by construction; `scripts/deploy.sh` builds and pushes in one command and
refuses to deploy an empty site. Today the manifest holds two entries, both private.
The port grows it; it does not grow a second deploy path.

**Personal finance is structurally absent from core.** `costmodel.FinanceAssumptions`
carries only market assumptions — mortgage rate, down payment, term, insurance per
$1,000, default tax rate with citation, HOA default, and a `SpecialAssessment` that is
percent *or* flat dollars. There is no HELOC balance, no current-home value, no rental
income anywhere in this package — the lending annex was excluded by design (REBUILD.md
rule 9). The deal map that leaked-by-default in the original cannot leak the same way
here, because the fields do not exist to embed. Lesson 9 below is therefore mostly a
tripwire against regression rather than a remediation.

**412 core tests and 29 annex tests pass offline.** The CLI has `init`, `watches`,
`sweep`, `report`, `map`, `predictions`, `enrich`, `daily`; the annex CLI `agentfinder`
has `init`, `sweep`, `resolve`, `report`. `report` already writes the dated-archive plus
canonical-latest pair and already chooses its page kind from the data
(`cli._kind_for`), which is the seam the new report kind plugs into.

## Part III — What the port genuinely requires

Strip away everything Part II covers and the new work is smaller than it looks:

1. **A `builder-attribution.yaml` data file** seeding the researched roster, and a
   research tier in `attribute_builder` that consults it before the heuristics.
2. **Current-sweep filtering in `newcon.py`** — the one place the latest-per-home trap
   still exists here.
3. **A new-construction payload builder and template** — the buyer report itself:
   `newconreport.py` (or a section of `reportdata`) producing one dict, and
   `templates/newcon.html` with the sections the live page has (the market read, the
   place, the map, the builder roster, warranties, each builder's ask curve, the
   price-cut ledger, every floor plan, move-in-ready homes, what Walsh costs to hold,
   methodology). Curated prose enters from versioned YAML, never from a rewritable
   payload file.
4. **Market-window and absorption arithmetic in the payload** — `window_days`,
   `window_from`, `window_to`, `n_sweeps`, inventory start/end, absorbed count, months
   of supply, all computed over the full observed history and all read by the template
   rather than written into it.
5. **A render-verification harness** committed as a script — the headless-Chrome
   `--dump-dom` checks that caught most of the real bugs, rebuilt one last time and
   never again.
6. **Manifest entries and a public-page tripwire test** for the buyer report and the
   public deal map, plus an outside-in deploy check.
7. **Operational honesty for the scheduled run** — a heartbeat, and a job that asserts
   its own paths.

Everything else in the original `scripts/newcon/` folder — `analyze.py` through
`analyze3.py`, `refresh.py`'s five-layer attribution heuristics, the three-token
builder, the `payload.curated-backup.json` — is scar tissue. Read it for what it
learned (this document is that reading); port none of it as code.

## Part IV — The lessons

These are the spine. Each one is a failure that actually happened during the wrong-repo
work, the reason it happened, and the rule that follows. Where this repo already has
the right mechanism, that is said plainly, because the point of the port is to use it.

**1. Scripts must not reach outside the repo.** The original report pipeline began life
reading its template and data from a Claude session's scratchpad directory. Weeks later
the session was gone, the directory was gone, and the pipeline was simply dead — not
degraded, dead — and had to be reconstructed from its outputs. The failure mode is
seductive because a scratchpad is where iteration naturally happens; the rule is that
the moment a file becomes an input to a build, it moves into the repo beside the code
that reads it. This repo already lives by that rule: templates in
`propertyfinder/templates/`, curated data in `propertyfinder/data/`, vendor assets
checked in and pinned. The port adds files to those folders and nowhere else.

**2. "Latest per home" is not "on the market today".** `latest_snapshot_rows` returns
each home's most recent sighting across *all* sweeps — which, on any database older
than one sweep, includes homes that have since been delisted. The first refreshed
buyer report was built on 180 homes when the live inventory was 145; it silently
counted 35 dead listings, and every median, every count, and the fitted ask curve were
polluted by them. The query is not wrong — it is the correct question for history — it
is just not the question a page dated today is asking. The rule: a current-market page
filters to `snapshot_ts == max(snapshot_ts)`, and any baseline or model fitted *for
that page* is fitted on the same filtered set, never on the raw query.
`reportdata.build_payload` and `mapdata.build_map_payload` already do this;
`newcon.compute_plan_baseline` and `score_specs` do not yet, and fixing them is commit
A2 below — in the module, so no caller ever has to re-learn it.

**3. A placeholder in static markup renders as literal source text.** One `${...}`
expression sat in plain HTML rather than inside a JavaScript template literal, and the
built page would have shown the reader the raw expression. It happened because a
template mixing static markup with JS-generated markup makes the two visually
identical in an editor. The rule has two halves: placeholders belong only in elements
JavaScript fills, and the check that catches a violation is not reading the source — it
is rendering the built page and grepping the *visible text* for `${`, `undefined`, and
`NaN`. That check is part of the harness in lesson 15 and runs on every page the port
builds.

**4. Builder attribution heuristics guess confidently and wrongly.** The feed carries
no builder field, so the original grew heuristics: plans named "Plan 1234" read as
Perry Homes (really Highland), plans named "The <name>" read as GFO Home (really
Village). A ported version of those heuristics misassigned 22 of 68 plan sheets — a
third of the roster, wrong with full confidence. This repo's `attribute_builder` is
deliberately better: it returns UNRESOLVED rather than guessing, and treats evidence
pointing at two builders as no evidence at all. But its strongest tier, CONFIRMED,
needs the builder named in the listing's own prose, and the search feed carries no
prose — so on search rows alone it honestly resolves almost nothing. The researched
roster therefore has to enter as **data, not code**: a new
`propertyfinder/data/builder-attribution.yaml`, written in exactly the provenance style
of `bath-corrections.yaml` (the value the feed showed, the verified truth, the source,
the date), keyed the same way (plan address as the feed writes it, or zpid for a
one-off spec), seeded with the roster in Part I, and consulted by `attribute_builder`
as a research tier before any heuristic runs. The "Perry or Britton" spec is recorded
*as* ambiguous — an entry that says the evidence resolves to two builders is worth
keeping precisely so nobody re-researches it into a false certainty. Remember why this
file must exist: the roster was recovered from a published HTML page's embedded JSON
because every working copy had been lost. That it was recoverable at all was luck.
Versioned data is how it stops depending on luck.

**5. Separate derived from curated, explicitly.** The original `payload.json` mixed
machine-derived aggregates (per-builder plan counts, medians, cut totals) with
hand-researched prose (the HOA fee schedule, warranty comparisons, the improvement-
district table, practical notes) in one file, and the refresh carried the whole thing
forward. Result: builders appeared in the roster table with plans they no longer had,
and the cheapest-plan column rendered `undefined`. The rule: derived blocks are
recomputed on every build from the database; curated blocks are read from versioned
files a build never writes; and the payload builder is structured so a reader of the
code can point at each top-level key and say which it is. In the port this separation
is physical — derived keys come from `newconreport.build_payload`'s own arithmetic,
curated keys come from `propertyfinder/data/walsh-newcon-curated.yaml` (provenance
header in the `bath-corrections.yaml` style, one section per hand-researched block) —
and `pagebuild.render`'s single-token contract forces them to meet in exactly one
place, where a test can verify the boundary.

**6. Prose that states a number must read it from the payload.** The original template
hardcoded "Across 26 days", "and one increase", "since 11 July", "five sweeps". Every
one of those was wrong within a fortnight, because a sentence in a template does not
know the database moved. The rule: windows, counts, and dates live in the payload —
`window_days`, `window_from`, `window_to`, `n_sweeps` — and the template's sentences
interpolate them, so the prose *cannot* drift from the data. And because a rule without
a test is a hope: commit a test that fails when a month name, a "N days"/"N sweeps"
phrase, or a bare date appears in a template's static text outside script blocks. The
regex will need judgement (glossary prose legitimately says "days on market"); start
strict and whitelist deliberately.

**7. A statistic on a tiny sample is worse than no statistic.** Absorption measured
between the last two sweeps found exactly one home absorbed in fourteen days, which put
"months of supply" at 18.2 — a headline number resting on a sample of one. Measured
across the full 41-day observed history it is 13.3 on a sample of four. Neither window
is *the* right one in general; the rule is to pick the window that makes the rate
meaningful (here: the longest available), put the window in the payload, and have the
page say which window it used, so a reader can weigh the number. A related small shame
worth not repeating: the original kept the payload key `absorbed_26d` after the window
stopped being 26 days, because renaming it meant touching the template. Name payload
keys for what they are (`absorbed`, `window_days`), not for the value they had one day.

**8. Do not overwrite the only copy of curated data.** The first refresh rewrote
`payload.json` in place before anyone had backed it up. The hand-researched sections
survived only because the refresh happened to carry them forward; the builder roster
did not, and was recovered from a published artefact by luck (lesson 4). Back up before
any script writes over a file containing human work — but the better rule, and the one
the port adopts, is structural: curated research never lives in a file a build writes
to. Builds write only to `reports/`; `propertyfinder/data/*.yaml` is edited by people
and versioned by git, and git *is* the backup.

**9. Personal financial data must be structurally impossible to publish, not merely
absent.** The original deal map embedded a finance block carrying the owner's HELOC
balance, current home value, monthly payment, and rental income — fine on a laptop,
disqualifying on a URL. The public copy was produced by rebuilding through a neutral
finance configuration (plain cash purchase, current-home fields zeroed) and then
audited **on the deployed URL, not on the local file**: fetch the live page, check
every personal field reads zero, grep the response for the literal figures. This repo
is already most of the way there by construction — `FinanceAssumptions` carries only
market assumptions, and the lending annex that would hold a borrower profile is
excluded from core — and `site-manifest.yaml` already distinguishes `public` from
`private` as a tested code path. Two additions make it a guarantee rather than a
current fact: make the neutral-finance public build a first-class option on the
`report` command (a `--public` flag that renders with default `FinanceAssumptions`
rather than the watch's block — never a temporary hand-edited config file, which is how
the original did it and how a private build one day ships by accident), and a test that
fails if any page marked `public` in the manifest embeds a finance block containing
anything beyond the whitelisted market-assumption keys, or any non-zero value in a
field that names a person's position. The audit-the-deployed-URL step stays manual but
scripted (lesson 12).

**10. A published page should not depend on a CDN.** The deployed deal map loaded
Leaflet from unpkg while `property-watch`'s own architecture documentation claimed the
library was vendored and inlined — the documentation was simply false, and nobody had
checked, because the page worked when the network did. This repo has the real thing:
Leaflet 1.9.4 checked in at `propertyfinder/templates/vendor/`, inlined by
`pagebuild.inline_vendor()` wherever a template writes `{{VENDOR:leaflet-1.9.4.js}}`,
with the vendor README documenting the pinning and the `</script` truncation rule. The
ported buyer report's map section uses those tokens; the only thing a reader's browser
fetches is basemap tiles. A test already proves the vendored bytes land in the built
map page; the new template gets the same test.

**11. An artifact fragment is not a web page.** The claude.ai artifact host injects the
doctype, `<html>`, `<head>`, `<body>`, and a CSS reset around what you give it; a
static host serves your bytes and nothing else. The talk deck was authored as an
artifact fragment, and the same content arriving at a Vercel URL had no reset, no
metadata, and a `<title>` sitting in the body where a browser ignores it. The rule: one
piece of content published to both hosts is **two builds from one source** — the full,
self-contained document is canonical (that is what this repo's pipeline produces
anyway), and the artifact fragment is derived from it by stripping the skeleton, never
the reverse. And a `<title>` belongs in the head.

**12. Verify a deployment from outside, as a visitor.** Vercel leaves a project's
production alias public but SSO-gates the per-deployment URL: the long
`project-hash.vercel.app` URL returns a 302 to a login page while the short alias
returns 200. Sharing the wrong one hands the audience a password prompt, and the deploy
tool's success message distinguishes neither. Separately, `og:image` must be an
absolute URL or every link preview silently fails — a relative path passes every local
check. Both were caught only by fetching the live URL and reading the actual response.
The rule: `scripts/deploy.sh` grows a post-deploy step that fetches the production
alias, asserts a 200 with no auth redirect on what should be public (and, for private
pages, asserts the password gate *is* there — fail closed cuts both ways), and checks
that any `og:image` in the served bytes is absolute. Trust nothing that only the
deploying machine saw.

**13. Theme handling has three states, not two.** An explicit reader choice stamps
`data-theme="dark"` or `"light"` on the root; the default "system" setting stamps
*nothing*, and only `prefers-color-scheme` separates light from dark. A colour defined
solely inside a `[data-theme]` block therefore never applies in the unstamped state —
which is most readers. The working pattern, already used by `templates/map.html` and to
be used verbatim by the new template: define the complete light palette as tokens on
bare `:root`; redefine tokens under `@media (prefers-color-scheme: dark)` guarded as
`:root:not([data-theme="light"])`; redefine them again under `:root[data-theme="dark"]`
so an explicit toggle wins in both directions. A Leaflet tile layer must resolve its
tile URL through the same three-state logic and re-resolve it if the reader switches
mid-visit — the map page already listens to the `matchMedia` change event; keep that.

**14. Design for the projector, not the laptop.** Twelve of the deck's thirty-one
slides overflowed at 1280×720 — a projector's reality — while all thirty-one fit at
1920×1080, the resolution they were authored at. The fix that repaired all twelve
without touching the full-size design: a height-based media query (`max-height: 820px`
or thereabouts) that tightens vertical rhythm — margins, gaps, font scale — plus a cap
on figure heights, because a fixed-aspect figure cannot reflow like text, only scale.
The rule generalises to every page the port builds: verify at the *smallest* display
the content will realistically meet, and prefer height queries over per-slide surgery.

**15. Verify by rendering, not by reading.** The harness that caught most of the real
bugs — per-slide overflow, the un-interpolated placeholder, missing payload keys, the
map's marker count — was headless Chrome with `--dump-dom`, driving the built file and
emitting a marker-delimited result block a script can assert on. Its practical traps
are worth writing down so nobody rediscovers them: headless Chrome defaults to dark and
*ignores* `--force-prefers-color-scheme`; use
`--blink-settings=preferredColorScheme=1` (dark) or `=2` (light) to test both theme
states. And `--screenshot` resets scroll position, so isolating a section into its own
viewport beats trying to scroll to it. The original rebuilt this harness from scratch
every time it was needed, which is why it was sometimes skipped. The rule: commit it
once as `scripts/verify_page.py` — visible-text grep for `${` / `undefined` / `NaN`,
element counts against the embedded payload, overflow detection at a stated viewport,
both theme states — and make running it against a freshly built page part of finishing
any template change. This is a development harness, not a test-suite member: the suite
stays offline and browser-free (`test_map_page.py` explains the division of labour),
and the harness covers exactly the layer the suite deliberately does not.

**16. Small frictions that cost real time.** The virtual environment broke when the
project folder moved, because the interpreter path inside `.venv` is hard-coded at
creation — REBUILD.md already flags this; the news is what followed. The scheduled
`launchd` job kept pointing at the old path and failed silently with exit 127 every
morning for two weeks, so "it runs every morning" had quietly stopped being true and
nothing said so. Two rules. First, a scheduled job asserts its own preconditions: the
plist's program is a small wrapper that checks its working directory and interpreter
exist and logs loudly (somewhere a person looks) when they do not, rather than letting
launchd swallow a 127. Second, a run writes a heartbeat that something else checks:
`daily` touches `reports/.last-daily` with the UTC timestamp, the digest email states
it, and `scripts/deploy.sh` warns when the heartbeat is older than the cadence. A
pipeline whose silence is indistinguishable from success will eventually be silent.

**17. Two things about content, not code.** Hand-authored SVG is excellent for
diagrams and objects and hopeless for character illustration: four attempts at a
cartoon figure for the deck produced, in order, a beanie, a sunburst, a crown, and a
Lego head, before the idea was abandoned for a simple two-object composition that
worked immediately. Budget accordingly — draw things, not people. And nobody's
biography should be silently edited: expanding someone's own abbreviations "for
consistency" is a change to their voice, not a copy-edit, and the only edit from that
pass worth keeping was a genuine typo. Content that belongs to a person is quoted, not
normalised.

## Part V — The build, commit by commit

Same contract as REBUILD.md Part III: each commit is one loop with the assistant —
describe it, let it build, run the proof, commit. `.venv/bin/pytest tests` (412 today)
and `.venv/bin/pytest annex/agent-finder` (29) pass at every single commit.

### Stage A — The roster becomes data *(commits 1–3)*

This stage goes first because it is the work most exposed to loss: it exists today as
two JSON files in a deprecated repo, recovered once already by luck.

- **Commit A1 — `data: builder-attribution.yaml, the researched Walsh roster`.**
  A new `propertyfinder/data/builder-attribution.yaml` in exactly the
  `bath-corrections.yaml` provenance style: a header explaining that the feed carries
  no builder field, how the roster was researched (builders' own plan pages, plus the
  recovery-from-published-HTML story as the cautionary tale), and how to add an entry.
  Keys are the plan address as the feed writes it (`"GRANTLEY Plan, Walsh Ranch 70'"`)
  or `zpid` for a one-off spec; each entry carries `builder`, `source`,
  `verified_on`, and — the staleness guard, analogous to `listed` in the bath file —
  the `plan_name` and `community` the entry was verified against. Seed it with the full
  roster: David Weekley Homes 20 plans, Drees Homes 18, Highland Homes 12, Village
  Homes 10, GFO Home 8, the spec-home entries for Perry Homes, Britton Homes, and High
  Street Homes, and the one "Perry or Britton" home recorded explicitly as ambiguous
  (`builder: null`, a `candidates:` pair, and a note) so the ambiguity is preserved
  rather than re-guessed. A loader in `dataquality.py` beside `bath_corrections()`,
  same `_load` shape, same `MappingProxyType` read-only cache. *Proof: the loader
  round-trips all 68 plan entries; the ambiguous entry loads with no builder.*
- **Commit A2 — `dataquality: the research tier in attribute_builder`.**
  `attribute_builder` consults the attribution file before any heuristic: a matching
  entry returns its builder at a new strongest tier, `RESEARCHED` (a fourth constant
  beside CONFIRMED/INFERRED/UNRESOLVED — a person's verification against the builder's
  own page outranks prose in a scraped listing; this naming is a judgement call, and
  folding it into CONFIRMED with a recorded source would also be defensible). The
  ambiguous entry returns `(None, UNRESOLVED)` — data agreeing with the module's own
  philosophy. An entry whose recorded plan name no longer matches the row it keys is
  stale and skipped, same doctrine as bath corrections. *Proof: fixtures reproducing
  the two real heuristic failures — a "Plan 1234" row researched to Highland and a
  "The <name>" row researched to Village — both resolve from data; with the file
  absent, both are UNRESOLVED, never Perry, never GFO.*
- **Commit A3 — `newcon: baselines and scores from the current sweep only`.**
  `compute_plan_baseline` and `score_specs` filter to
  `snapshot_ts == max(snapshot_ts)` before anything else (or grow a `rows=` parameter
  the CLI fills with the active set — pick whichever reads better, but the filter lives
  in the module, not in callers). This closes the last latest-per-home trap in the
  repo. *Proof: a fixture with a plan sheet whose newest sighting is one sweep old
  contributes nothing to the ask curve; the live-plan count matches the live sweep.*

### Stage B — The buyer report *(commits 4–8)*

- **Commit B1 — `data: walsh-newcon-curated.yaml, the hand-researched sections`.**
  The curated blocks from the original payload — builder profiles and warranty
  comparisons, the HOA fee schedule by lot type, the improvement-district table with
  its source ("City of Fort Worth PID No. 16 Service and Assessment Plan, Improvement
  Area 4, adopted 12 May 2026"), other/custom builders, practical notes — moved into
  one versioned YAML file with a provenance header per section (source, date, and what
  would make it stale). Builds read this file; nothing ever writes it (lesson 8).
  *Proof: the loader returns every section; the file diffs cleanly in review, which is
  the point of it being YAML and versioned.*
- **Commit B2 — `newconreport: the payload builder`.** A pure function in a new
  `propertyfinder/newconreport.py`: session and watch in, one dict out. Derived keys —
  `plans`, `specs`, `resale`, `builders` (per-builder counts, price spans, ask ppsf,
  cut totals — *recomputed every build*, the exact block whose carry-forward rendered
  `undefined`), `market` with the movement counts and the window block (`window_days`,
  `window_from`, `window_to`, `n_sweeps`, `inventory_start`, `inventory_end`,
  `absorbed`, `months_supply` over the full observed history — lesson 7), `finance`
  from `cfg.finance_for(watch)` — all computed from the current sweep through
  `newcon.py`, `dataquality.assess`/`apply_corrections`, `price_change_map`,
  `sweep_changes`, and `costmodel.monthly_cost`. Curated keys come only from B1's file.
  *Proof: a snapshot test on fixtures; a test asserting the derived/curated boundary
  (every top-level key is produced by exactly one of the two sources); on the real
  database, the print line reproduces Part I's numbers — 68 plans, 39 specs, 37
  resales, 54 cuts, $2,498,799, 13.3 months on a 41-day window.*
- **Commit B3 — `newconreport: the template`.** `propertyfinder/templates/newcon.html`,
  a real file, one `/*__PAYLOAD__*/{}` token, `{{VENDOR:leaflet-1.9.4.js}}` and
  `{{VENDOR:leaflet-1.9.4.css}}` for the map section, the three-state theme pattern
  from `map.html` (lesson 13), every number and every date interpolated from the
  payload by script (lesson 6), placeholders only inside template literals (lesson 3).
  The sections mirror the live page: the market read, the place, the map, the builder
  roster, warranties, ask curves, the price-cut ledger, the plan table, move-in-ready
  homes with their score ledgers, carry cost, methodology. Author it fresh against the
  payload rather than transplanting the original's 2,007 lines — the original template
  is the *reference for content and section order*, not a source file. *Proof:
  `render("newcon.html", payload)` produces one self-contained page; the
  `test_map_page.py`-style checks pass (payload embedded and reversible, vendor bytes
  present, no token survived).*
- **Commit B4 — `test: no hardcoded dates or durations in templates`.** The regex test
  from lesson 6, over every file in `templates/`: month names, `\d+ (days|sweeps)`,
  and `\d{4}-\d{2}-\d{2}` in static text outside `<script>` blocks fail the build,
  with a deliberate whitelist for legitimate glossary prose. Run it against `map.html`
  and `report.html` too — if either fails today, that is the test earning its keep on
  day one. *Proof: seeding "since 11 July" into a template fixture fails; the shipped
  templates pass.*
- **Commit B5 — `cli: report --kind newcon`.** The new kind joins `_kind_for`'s
  vocabulary and `_build_page`'s dispatch; output through the existing
  `_write_page_pair`, so `reports/walsh-aledo-newcon.html` and its dated archive appear
  beside the others. Whether `daily` builds it every morning is a judgement call —
  start with on-demand, promote to `daily` once it has run clean for a week. *Proof:
  the command builds the page from the real database and prints the counts; two runs
  in one day are idempotent.*

### Stage C — Verification as equipment *(commit 9)*

- **Commit C1 — `scripts: verify_page.py, the render harness`.** Headless Chrome,
  `--dump-dom`, a marker-delimited result block, exit non-zero on: `${` / `undefined`
  / `NaN` in visible text; element counts disagreeing with the embedded payload
  (markers on the map, rows in the plan table); horizontal or per-section vertical
  overflow at a stated viewport (default 1280×720 — the projector, lesson 14); run
  twice, once per theme state via `--blink-settings=preferredColorScheme=1|2`, because
  `--force-prefers-color-scheme` is ignored (lesson 15). Not a pytest member — a
  script a person or `daily` runs against a freshly built page. *Proof: it passes on
  the B5 page and on `reports/walsh-aledo-map.html`; a fixture page with a leaked
  placeholder fails.*

### Stage D — Publishing, public and verified *(commits 10–12)*

- **Commit D1 — `site: the buyer report and the public deal map enter the manifest`.**
  `site-manifest.yaml` gains `walsh-aledo-newcon.html` (visibility a judgement call —
  the live page is effectively public today, but `private` until the D2 tripwire
  exists is the safe order) and the deal map's public entry. This exercises the
  manifest's `public` path with a real page for the first time — the code path its own
  comments promise is real and test-proven. *Proof: `scripts/build_site.py` copies
  exactly the named files; the index links only private pages; noindex headers land on
  the public one.*
- **Commit D2 — `report --public, and the public-finance tripwire`.** The `report`
  command gains `--public`: render with default (market-neutral) `FinanceAssumptions`
  instead of the watch's block, never a hand-edited config (lesson 9). A test walks
  every manifest entry marked `public`, parses the embedded payload out of the built
  page, and fails if its `finance` block contains any key outside the
  `FinanceAssumptions` whitelist or anything shaped like a personal position. Today
  that test passes trivially because core carries no personal fields — which is
  exactly what makes it a tripwire: it exists for the day someone wires the lending
  annex too close. *Proof: the tripwire test; a doctored payload fixture fails it.*
- **Commit D3 — `deploy: verify from outside`.** `scripts/deploy.sh` (or a
  `scripts/verify_deploy.py` it calls) fetches the production alias after deploying:
  200 and page bytes for public paths, the auth gate for private ones, no 302-to-login
  on the URL that will be shared, `og:image` absolute in the served response (lesson
  12). Overridable base URL so the test suite can point it at a stub, matching
  `deploy.sh`'s existing `PYTHON`/`NPX` pattern. *Proof: against a stub server, a
  302-to-login on a public path fails the deploy; a relative `og:image` fails it.*

### Stage E — The deck and the annex settle in *(commits 13–14)*

- **Commit E1 — `site-talk: one source, two builds, verified at 720p`.** The deck
  already lives in the right repo; give it the port's discipline. The full document in
  `site-talk/index.html` is canonical — `<title>` in the head, reset and metadata of
  its own, absolute `og:image`; a small build step derives the artifact fragment from
  it by stripping the injected-by-the-host skeleton (lesson 11), rather than
  maintaining two hand-edited copies. Run `verify_page.py` over all 31 slides at
  1280×720 and keep the height-media-query fix (lesson 14) under test. *Proof: the
  harness reports zero overflowing slides at 720p in both themes.*
- **Commit E2 — `site: the agent ledger gets a declared publish path`.** The
  agent-finder annex's report enters the publishing story on purpose — either as a
  manifest entry (which requires the manifest to accept the annex's output directory,
  a small, deliberate widening of `build_site.py`'s reports-only rule) or as its own
  Vercel project with the D3 outside-in check pointed at it. Judgement call; the
  second is less code and matches how it is deployed today. Either way the choice is
  written down here and in `docs/vercel.md`, because three deploy targets that
  "accreted" is exactly the original's post-mortem item 8. *Proof: one command
  republishes the ledger; the outside-in check passes on its URL.*

### Stage F — The morning run tells the truth *(commit 15)*

- **Commit F1 — `daily: heartbeat, and a scheduler that cannot fail silently`.**
  `daily` writes `reports/.last-daily` (UTC timestamp, exit status, call spend); the
  digest email states it; `deploy.sh` warns when it is stale. The launchd plist runs a
  wrapper that asserts its working directory and `.venv/bin/python` exist before doing
  anything, and logs a loud line to a file a person actually opens when they do not —
  the two-weeks-of-exit-127 incident (lesson 16), made impossible to repeat quietly.
  `docs/scheduling.md` gains the recipe and the note that a moved folder means a
  recreated venv. *Proof: pointing the wrapper at a missing venv produces the loud
  line, not silence; a normal run refreshes the heartbeat.*

---

Fifteen commits, each one runnable and testable alone, and at the end of them the four
live pages all build from this repository with one command each, the researched roster
is versioned data no future accident can lose, and every lesson above is either
enforced by a test or written into the tool that would otherwise re-learn it.
