# Property Finder

Property Finder watches a housing market so you don't have to: once a day it sweeps the
listings around a point on the map, records what it sees, and compares today against every
day before it. History is the product — it is what turns "a $649,900 house" into "a house
cut $85,000 since July that has sat unsold for 92 days." The output is a single
self-contained web page, built on your own computer and optionally published to a web
address; nothing about your data ever runs on the internet.

This repository is a ground-up rebuild of an earlier tool (`property-watch`), executed
commit by commit from a written plan. Read **`docs/REBUILD.md`** for the plan and the
post-mortem that produced it, and **`docs/EXPERT-PLAN.md`** for the methodology — what
this tool considers "a deal" and why. The commit history is meant to be read: each commit
is one bite-sized, tested step in the story of the construction.

## Watch the walkthrough

**[AIN Agent Development Process](https://www.loom.com/share/78b5633b475f4539894ab8103178949b)**
— 64 minutes, the whole thing end to end: voice-planning with Wispr Flow, Claude Code driving
VS Code and GitHub, Zillow data through a search API, hosting on Vercel, and why planning,
testing and *external* verification are what keep an agent from hallucinating or acting
unsafely. Forty-six commits, link-backed outputs, and a ZIP-code run that took about eleven
minutes.

<!-- Deliberately a link and not an <iframe>. GitHub's markdown sanitiser strips iframe
     elements outright, so an embed here renders as nothing at all — the video plays inline on
     slide 2 of the deck (site-talk/index.html), where a static host serves our own bytes and
     an iframe survives. Anyone "fixing" this by pasting the embed code back in will produce a
     blank space on github.com. -->

The same recording is slide 2 of the deck, where it plays inline:
**[so-easy-a-grunt-could-do-it.vercel.app](https://so-easy-a-grunt-could-do-it.vercel.app)**.

## Quick start

```bash
uv venv --python 3.12 && source .venv/bin/activate
uv pip install -e ".[stats,dev]"
python -m pytest -q          # the whole suite runs offline, in seconds
propertyfinder init          # create the database
propertyfinder sweep         # costs real API quota — see docs/EXPERT-PLAN.md on budget
propertyfinder report        # the map where there are sales to value against, else a table
propertyfinder map           # the map under its own name, whatever the report chose
propertyfinder daily         # sweep everything, rebuild everything, mail one digest
```

Secrets live in `.env` (never committed): `SEARCHAPI_API_KEY`, and optionally
`PROPERTYFINDER_DB_PATH`, `QUOTA_CAP_SEARCHAPI_MONTHLY`, and the SMTP settings `daily`
mails its digest through (unset means it prints the digest instead of sending it). See
`docs/scheduling.md` for running `daily` on a timer.

## Links

Everything this project points at, in one place — the tools it was built with, what it
produced, and the talk that walks through both.

### The tools

Five of these six are free or flat-rate; SearchApi.io is the only one that costs anything
per use.

- **[Claude](https://claude.ai/download)** — the agent that wrote this repository, commit by
  commit, from the plan in `docs/REBUILD.md`. The desktop app; `claude` on the command line
  is what actually did the work here.
- **[Visual Studio Code](https://code.visualstudio.com/download)** — the editor. Free. Used
  here mostly as a place to watch files change and read a diff.
- **[Wispr Flow](https://wisprflow.ai/r?TYLER2272)** — dictation. Most of the instructions
  that produced this code were spoken rather than typed, which matters more than it sounds:
  the bottleneck in agentic development is how fast you can describe what you want.
- **[Circleback](https://circleback.ai/signup?ref=tyler.goble@vspartners.us)** — meeting
  notes. Not part of the build, but part of the working day around it: the decisions that
  became `docs/REBUILD.md` were argued out loud in meetings first.
- **[SearchApi.io](https://www.searchapi.io/)** — the listings feed behind
  `SEARCHAPI_API_KEY`. The one component that spends real money per call, which is why
  `QUOTA_CAP_SEARCHAPI_MONTHLY` exists and why the test suite never touches the network.
- **[Vercel](https://vercel.com)** — where the reports get published. A static host, one
  command, and the password gate at the edge that keeps the private pages private
  (`docs/vercel.md`).

### What it produced

All four are live, and all four rebuild themselves from a fresh sweep.

- **[The new-construction report](https://walsh-new-construction.vercel.app)** — the buyer's
  report for a single market's new builds.
- **[The deal map](https://walsh-deal-map.vercel.app)** — every active listing, scored, on a
  map. Public, and privacy-sanitised by construction rather than by hand
  (`docs/PORTING-THE-REPORTS.md`).
- **[The luxury agent finder](https://walsh-luxury-agents.vercel.app)** — built by the agent
  on its own initiative, from the `annex/agent-finder` sub-project.
- **[The Crockett 75835 shortlist](https://crockett-75835.vercel.app)** — a second market,
  screened to one buyer's brief: 4+ bedrooms and 3,000+ sq ft, houses only, inside the ZIP.
  13 homes out of the 250 the sweep found, and the page shows the other 237 by the reason
  each was excluded. Public, no password (`publish/crockett-75835/publish-manifest.yaml`).

### The talk

- **[So Easy A Grunt Could Do It](https://so-easy-a-grunt-could-do-it.vercel.app)** — the
  deck. Thirty-two slides on how all of the above got built, and what broke on the way —
  the second of them a screen recording of the walkthrough.
  Source in `site-talk/`.
- **[Actual Intelligence](https://www.victorysquarepartners.com/events/actual-intelligence)**
  — the Victory Square Partners event the talk was given at.

### The code

- **[github.com/tylergoble-VSP/property-finder](https://github.com/tylergoble-VSP/property-finder)**
  — this repository. The commit history is the story: each commit is one tested step, and
  `docs/REBUILD.md` is the plan they were built from.
