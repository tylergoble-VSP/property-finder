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

## launchd (recommended on macOS)

Save as `~/Library/LaunchAgents/com.propertyfinder.daily.plist`, edit the working directory
to match where the repository actually lives, then
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
    <string>/path/to/property-finder/.venv/bin/propertyfinder</string>
    <string>daily</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict><key>Hour</key><integer>7</integer><key>Minute</key><integer>0</integer></dict>
  <key>StandardOutPath</key><string>/tmp/propertyfinder-daily.log</string>
  <key>StandardErrorPath</key><string>/tmp/propertyfinder-daily.err</string>
</dict>
</plist>
```

The `ProgramArguments` entry is the console script `pyproject.toml` installs into the
virtualenv (`propertyfinder = "propertyfinder.cli:main"`) — launchd does not source shell
profiles, so the full path to that binary is what stands in for "activate the venv, then
run the command."

## cron

```cron
0 7 * * *  cd "/path/to/property-finder" && .venv/bin/propertyfinder daily >> /tmp/propertyfinder-daily.log 2>&1
```

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
