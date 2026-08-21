#!/usr/bin/env bash
# One command to republish the luxury listing-agent ledger: build, stage, upload, verify.
#
# The ledger is the annex's page and its own Vercel project (annex/agent-finder/publish-manifest.yaml
# explains why, and docs/vercel.md lists all three targets). This script exists so that "its own
# project" means a declared path somebody can run and check, rather than an ad-hoc upload nobody
# can reproduce — which is what it was, and which is the original's post-mortem item 8 in
# miniature.
#
# The staging folder is built fresh from the one report file, so nothing else can travel with it:
# the same allowlist instinct scripts/build_site.py enforces for the reports site, applied here
# with a list of length one.
#
# PROPERTYFINDER_ROOT, PYTHON and NPX are overridable, exactly as in deploy.sh, so every line up
# to the actual upload is testable against stubs.
set -euo pipefail

ROOT="${PROPERTYFINDER_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
PYTHON="${PYTHON:-.venv/bin/python}"
NPX="${NPX:-npx}"
STAGE="site-agents"
CONFIG="annex/agent-finder/agent-config.yaml"
MANIFEST="annex/agent-finder/publish-manifest.yaml"
REPORT="reports/walsh-luxury-25mi.html"

cd "$ROOT"

refuse() {
  echo "refusing to publish: $1" >&2
  exit 1
}

[[ -f "$CONFIG" ]] || refuse "$CONFIG is missing"

"$PYTHON" -m agentfinder.cli --config "$CONFIG" report

[[ -s "$REPORT" ]] || refuse "$REPORT was not built, or is empty"

# Rendered checks before anything is uploaded: a placeholder left in visible text, a count that
# disagrees with the payload, or an uncaught script error is a page not worth publishing.
# Skipped, loudly, where there is no browser to drive.
if ! "$PYTHON" scripts/verify_page.py "$REPORT"; then
  refuse "$REPORT does not render cleanly — see the problems above"
fi

rm -rf "$STAGE"
mkdir -p "$STAGE"
cp "$REPORT" "$STAGE/index.html"
cat > "$STAGE/vercel.json" <<'JSON'
{
  "cleanUrls": true,
  "trailingSlash": false,
  "headers": [
    {
      "source": "/(.*)",
      "headers": [
        { "key": "X-Robots-Tag", "value": "noindex" },
        { "key": "Cache-Control", "value": "public, max-age=0, must-revalidate" }
      ]
    }
  ]
}
JSON

echo "publishing $REPORT from $STAGE"
"$NPX" vercel deploy --prod --yes --cwd "$STAGE"

if [[ -z "${LEDGER_BASE_URL:-}" ]]; then
  echo "NOT VERIFIED: set LEDGER_BASE_URL to the ledger's production alias and this script" >&2
  echo "              will fetch it as a visitor would. Until then nothing has confirmed" >&2
  echo "              that what went up is what a reader gets." >&2
  exit 0
fi

echo "verifying $LEDGER_BASE_URL from outside"
"$PYTHON" scripts/verify_deploy.py "$LEDGER_BASE_URL" --manifest "$MANIFEST"
