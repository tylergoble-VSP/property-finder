# Scheduling the daily run

Property Finder needs no resident daemon. `propertyfinder daily` does everything in one
invocation — sweep every watch, freeze and resolve predictions, rebuild the report and map
for every for-sale watch, and send (or print) one digest — so a once-a-day timer is the
whole of "automation." Use launchd (native macOS) or cron; either just needs to run one
command at one time of day.

The digest is emailed only when SMTP is configured in `.env` (`smtp_host`, `smtp_username`,
`smtp_password`, `alert_email_from`, `alert_email_to`, and optionally `smtp_port` /
`smtp_tls`, which default to `587` / true); left unset, `daily` prints the digest to its own
log instead of failing, which is what makes the launchd/cron examples below safe to enable
before mail is ever set up.

## A scheduled job that cannot fail silently

**Point the scheduler at `scripts/daily.sh`, not at the console script.** This is the whole
lesson of one real fortnight: the plist invoked `.venv/bin/propertyfinder` directly, the project
folder moved, the interpreter path baked into `.venv` at creation stopped resolving, and launchd
swallowed **exit 127 every morning for two weeks**. Nothing broke loudly. "It runs every
morning" had quietly stopped being true and nothing anywhere said so
(`docs/PORTING-THE-REPORTS.md`, lesson 16).

`scripts/daily.sh` asserts its own preconditions before doing anything: that the working
directory exists and can be entered, that the interpreter is there **and can import
`propertyfinder.cli`** — which is the real test, because a moved folder leaves a `.venv` that is
present and unusable, and because a bare system python run from the repository root imports the
package happily and then dies on the first dependency — and that the watch config is where it
should be. Any of those failing produces one greppable line on stderr and exit **78**
(`EX_CONFIG`), rather than the 127 that hid before:

```
PROPERTYFINDER DAILY ABORTED: no interpreter at /old/path/.venv/bin/python — a moved project folder means a recreated venv
  checked at 2026-08-21T14:00:00Z
  root=/old/path python=/old/path/.venv/bin/python config=/old/path/watch-config.yaml
```

**A moved project folder means a recreated virtualenv.** A virtualenv hard-codes its
interpreter's absolute path at creation; moving the folder does not update it. After any move:
`rm -rf .venv && uv venv && uv pip install -e '.[stats]'`, then re-check the plist's paths.

### launchd (recommended on macOS)

Save as `~/Library/LaunchAgents/com.propertyfinder.daily.plist`, edit the two paths to match
where the repository actually lives, then
`launchctl load ~/Library/LaunchAgents/com.propertyfinder.daily.plist`.

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.propertyfinder.daily</string>
  <key>WorkingDirectory</key><string>/path/to/property-finder</string>
  <key>ProgramArguments</key>
  <array>
    <string>/path/to/property-finder/scripts/daily.sh</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PROPERTYFINDER_ROOT</key><string>/path/to/property-finder</string>
    <key>DAILY_ARGS</key><string>--deploy</string>
  </dict>
  <key>StartCalendarInterval</key>
  <dict><key>Hour</key><integer>7</integer><key>Minute</key><integer>0</integer></dict>
  <key>StandardOutPath</key><string>/path/to/property-finder/logs/daily.log</string>
  <key>StandardErrorPath</key><string>/path/to/property-finder/logs/daily.err</string>
</dict>
</plist>
```

`PROPERTYFINDER_ROOT` is set explicitly rather than inferred, so the wrapper can *tell you*
which directory it was looking for when it is not there. The logs go inside the repository
rather than `/tmp` for the same reason the heartbeat exists: a log in a temporary folder is a
log nobody opens. `DAILY_ARGS` passes flags through (`--deploy`, `--no-sweep`, `--budget N`).

### cron

```cron
0 7 * * *  PROPERTYFINDER_ROOT=/path/to/property-finder DAILY_ARGS=--deploy /path/to/property-finder/scripts/daily.sh >> /path/to/property-finder/logs/daily.log 2>&1
```

## The heartbeat: how you find out a morning went missing

Every `daily` run stamps `reports/.last-daily` with the UTC time it finished, its exit status,
and what it spent — **whatever the outcome**, because "it ran and it failed" and "it never ran"
are different problems and a mark written only on success cannot tell them apart.

Three things read it, so that a missing morning surfaces without anyone going looking:

* **the next digest** states the previous run's line (`last daily run: … — ok, 32 billable
  calls, 24h ago`), in an email somebody is already reading;
* **`scripts/deploy.sh`** prints a warning before publishing when the heartbeat is missing,
  older than 36 hours, or carries a failure — it does not refuse, because a person deploying by
  hand has every right to publish a stale page, but staleness cannot be published unknowingly;
* **you**, directly: `cat reports/.last-daily`.

36 hours is deliberate: generous enough to survive a laptop that was shut for a day, tight
enough that a fortnight of exit 127 cannot hide inside it.

## Quota arithmetic: cap, calls per sweep, cadence

`daily`'s own default budget (`propertyfinder/cli.py`, `DAILY_SLICE_DAYS`) is the monthly
cap divided across 30 days — `QUOTA_CAP_SEARCHAPI_MONTHLY // 30`, at least 1 — rather than
the whole allowance, so a scheduler that runs every morning cannot spend a month's quota on
its first one. `--budget N` overrides that arithmetic for a run that should behave
differently, and `sweep`/`enrich` outside of `daily` still default to the *full* monthly cap
per invocation, since those are commands a person runs by hand and can judge for themselves.

A watch's **worst case** is its query count times `max_pages` — the ceiling the adapter
will not cross, reached only if a query actually has that many pages of results. The table
below is `watch-config.yaml` as committed:

| watch | listing_status | queries | max_pages | worst-case calls/sweep |
|---|---|---:|---:|---:|
| walsh-aledo | for_sale | 1 | 12 | 12 |
| walsh-aledo-sold | sold | 1 | 20 | 20 |
| **total** | | | | **32** |

At the default monthly cap of 1,000, `daily`'s own slice is `1000 // 30 = 33` calls a
day — one more than today's worst-case full sweep of both watches, so a daily cadence fits
inside its own budget even in the pessimistic case, and comfortably under it in the ordinary
one (a sweep stops paging as soon as a query's results run out; it rarely walks every page
`max_pages` allows). This table is also the check to run *before* adding a third watch or
raising a `max_pages`, rather than after — the "Quota knowledge lived in documentation" item
in `docs/REBUILD.md`'s post-mortem is exactly this arithmetic going unverified until a bill
said otherwise.

## Verifying without spending anything

```bash
.venv/bin/propertyfinder daily --no-sweep   # rebuild predictions/pages/digest from the DB
                                             # as it stands, with no network call at all
```

Useful for testing the digest, SMTP settings, or a template change against real (already
swept) data without touching the API budget at all.

## Verifying a built page before it goes out

`scripts/verify_page.py` renders a built page in headless Chrome and checks what came out:
no `${`, `undefined`, `NaN` or `[[token]]` in visible text; element counts matching the
embedded payload (markers, plan rows, ready-now cards, builder cards, ask-curve panels); no
horizontal page overflow and no clipped section at the stated viewport; and — the failure that
silently truncates a page while every build-time check still passes — no uncaught JavaScript
error on the console.

```bash
.venv/bin/python scripts/verify_page.py reports/walsh-aledo-newcon.html
.venv/bin/python scripts/verify_page.py reports/*.html --viewport 1920x1080
```

It renders both theme states by default, because a colour defined in only one of them is a bug
in the other. It is **not** a member of the test suite: the suite stays offline and browser-free
(`tests/test_map_page.py` explains the division of labour), and this covers exactly the layer
the suite deliberately does not. Run it after any template change, before deploying.
