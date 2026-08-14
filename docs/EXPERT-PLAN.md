# The methodology — what this tool judges, and how

*This document precedes the code that implements it, on purpose. When the code and this
document disagree, one of them is wrong and it is usually the code. Its predecessor was
written seven minutes after the first commit of the original tool and pulled the project
back on course every time it wandered; this version is rewritten with four weeks of
production lessons baked in (see docs/REBUILD.md for the full post-mortem).*

## The one-sentence product

Look at a housing market every day, write down what you see, and compare today against
every day before it — because a price is just a number, but a price that *fell $85,000
last Tuesday* is information.

## The watch

A **watch** pins a centre point and a radius. Its queries (always `"City, ST ZIP"`
strings — a bare ZIP mis-resolves; we once got Minerva, Ohio when asking about Texas) are
swept, results are deduplicated per home, and **the radius is authoritative**: whatever
query surfaced a listing, if it is outside the circle it is discarded, and a listing with
no coordinates is *outside*, never guessed in. A watch may additionally name a
subdivision, in which case geometry runs first and membership second. Sold-side watches
pair with for-sale watches by naming convention (`<name>-sold`) and flow through the same
seam unchanged.

## History is the product

Two tables carry everything: one row per home ever seen (identity), one row per home per
sweep (observation). Every downstream feature — the movement strip, the price-cut ledger,
days-on-market pressure, "back on market" — is a query over those observations. Nothing
else in this tool exists without them, which is why they are built and tested before any
report, model, or page.

## Valuation: two tracks, no Zestimate

**The Zestimate is banned from valuation.** List prices re-anchor to it, roughly half of
resales lack one, and the original tool's "placeholder" signal built on it had to be
publicly retired. It may be *displayed*, labelled, as a reference; it may never enter a
score.

**Track A — resale.** The anchor is what comparable homes actually sold for. A hedonic
model (log price on log size, beds, baths, type) fitted on the sold watch gives each
active listing an expected price, a prediction band, and a standardised residual; an
independent nearest-neighbour comp set within a size band cross-checks it; a spatial
smoothing of the model's residuals corrects for cheap and premium pockets a size-only
model cannot see. Texas is a non-disclosure state, so where true prices are absent the
sold-side estimate re-anchored after closing serves as a proxy — and every figure derived
from it carries a `basis` label that follows the number all the way into the rendered
page. Calibration is not optional: every sweep freezes a prediction per active home,
resolutions are scored when homes reappear sold, and the error report counts real-price
resolutions separately from proxy ones.

**Track B — new construction.** A builder is a rational repeat seller publishing a price
list. Rows whose address contains `"Plan,"` are that list — an ask-curve, not homes — and
are excluded from every resale comp. Purchasable spec homes are scored against the
ask-curve for their size, with days-on-market and observed cuts as pressure signals, and
builder attribution carried with an explicit confidence tier (the feed has no builder
field; attribution comes from description text, plan matching, and pricing signatures).

## The cost model must express reality

Monthly carry is principal-and-interest, tax, insurance, and dues — and the special
assessment is **either a percentage or flat dollars per year**, per watch, overridable
per property. This is non-negotiable: the district assessment where this tool first ran
is a fixed dollar principal per lot ($3,271/year on a new 70-foot lot versus $928 on an
early-phase one), and forcing that through a percentage field mispriced homes by
$200/month for weeks. Tax rates are verified against adopted rates and cited, not
guessed; rounding is half-up so a page recomputing in JavaScript agrees with Python to
the dollar.

## Data quality is code, not memory

The feed lies in known ways: half-bath counts round **up** (27 of 68 builder plans were
wrong, all in one direction), square footage is the **base** of a range that can run 775
feet higher, and one home occasionally lists twice at two addresses. Each known lie is a
detection function with a fixture reproducing the real incident, and the resulting flags
travel with the record into every report. A correction that lives in a person's memory
is a regression waiting to happen.

## Honesty over coverage

Roughly half of listings lack an estimate, two-thirds lack a tax rate, and the detail
endpoint fails one pull in five. Every signal therefore degrades to **"not scored"** with
a reason, never to a guess. A confidence tier accompanies every score, and the page
explains every number in plain English — the reader this tool serves is a family making
a six-figure decision, not an analyst.

## The budget is an object

API calls cost real money against a monthly cap (`QUOTA_CAP_SEARCHAPI_MONTHLY`, default
1,000). The adapter counts calls, sleeps 0.35 s between them, and **refuses** to exceed a
per-run ceiling rather than trusting documentation to restrain anyone. Tests never touch
the network — a recorded fixture replayed through a fake transport is the test suite's
internet, which is why the whole suite runs offline in seconds and development burns no
quota.

## What done looks like

- A single `daily` command sweeps every watch, refreshes predictions, rebuilds pages,
  and sends one digest email — schedulable by the operating system's own timer.
- Every page is one self-contained file, built from a template plus a JSON payload
  (never HTML inside Python), openable offline, publishable by copying.
- The calibration report can answer, per segment and per basis: *how wrong are we,
  typically?* — and the answer is printed where the user can see it.
