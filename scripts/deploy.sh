#!/usr/bin/env bash
# One command to the hosting service: rebuild site/ from the manifest, then push it live.
#
# Refuses outright, before ever calling `vercel`, if there is nothing worth deploying —
# site/ missing entirely, or present but holding no real page (the shape an empty
# site-manifest.yaml produces: an index and nothing to link from it). Uploading a folder
# nobody asked for is a worse failure than a clear refusal with a reason attached.
#
# After the upload it fetches the production alias and checks what a *visitor* gets: 200 and
# real bytes on every public path, the password gate on every private one, and no relative
# og:image in the served response. Vercel leaves the production alias public and SSO-gates the
# per-deployment URL, and `vercel deploy`'s success line distinguishes neither — so the URL
# that will be shared is the one that gets checked, from outside
# (docs/PORTING-THE-REPORTS.md, lesson 12). Set SITE_BASE_URL to the alias; without it the
# post-deploy check is skipped with a loud line rather than silently.
#
# PROPERTYFINDER_ROOT, PYTHON, and NPX are overridable so this script is testable without
# ever touching the real network or a real Vercel project: point them at a sandbox and a
# stub, and every line up to the actual `vercel deploy` runs for real.
set -euo pipefail

ROOT="${PROPERTYFINDER_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
PYTHON="${PYTHON:-.venv/bin/python}"
NPX="${NPX:-npx}"
SITE_DIR="site"

cd "$ROOT"

refuse() {
  echo "refusing to deploy: $1" >&2
  exit 1
}

# The morning run's own mark, checked before publishing what it produced. A deploy that
# uploads a fortnight-old page without saying so is the silence lesson 16 is about, one layer
# further along: this does not refuse — a person deploying by hand has every right to publish
# a stale page — it just makes staleness impossible to publish unknowingly.
"$PYTHON" - <<'PYCHECK' >&2 || true
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, ".")
from propertyfinder.heartbeat import STALE_AFTER, read

now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
beat = read(Path("reports"))
if beat is None:
    print("WARNING: no daily heartbeat on record — these pages may be from any morning at all")
elif beat.is_stale(now):
    print(f"WARNING: the daily run is stale — {beat.sentence(now)}")
elif not beat.ok:
    print(f"WARNING: the last daily run failed — {beat.sentence(now)}")
PYCHECK

"$PYTHON" scripts/build_site.py

[[ -d "$SITE_DIR" ]] || refuse "$SITE_DIR does not exist (build_site.py should have created it)"

page_count=$(find "$SITE_DIR" -maxdepth 1 -type f -name '*.html' ! -name 'index.html' | wc -l | tr -d ' ')
[[ "$page_count" -gt 0 ]] || refuse "$SITE_DIR has no published page — is site-manifest.yaml empty?"

echo "deploying $page_count page(s) from $SITE_DIR"
"$NPX" vercel deploy --prod --yes --cwd "$SITE_DIR"

if [[ -z "${SITE_BASE_URL:-}" ]]; then
  echo "NOT VERIFIED: set SITE_BASE_URL to the production alias and this script will fetch" >&2
  echo "              every published path as a visitor would. Until then, nothing has" >&2
  echo "              confirmed that what went up is what a reader gets." >&2
  exit 0
fi

echo "verifying $SITE_BASE_URL from outside"
"$PYTHON" scripts/verify_deploy.py "$SITE_BASE_URL"
