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
