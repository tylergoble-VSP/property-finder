#!/usr/bin/env bash
# One command to republish the Crockett 75835 shortlist: rebuild, render-check, stage, upload,
# verify from outside.
#
# The page is its own Vercel project and answers at `/` with no auth gate anywhere in it
# (publish/crockett-75835/publish-manifest.yaml explains why, and docs/vercel.md lists all four
# targets). This script exists so that "its own project" means a declared path somebody can run
# and check, rather than an ad-hoc upload nobody can reproduce.
#
# IT SPENDS NO API QUOTA. The rebuild is `map --public`, a pure read over what the database
# already holds — so republishing after a wording change, a template fix, or a change to the
# brief costs nothing. Refreshing the *data* is a separate, deliberate act:
# `propertyfinder sweep --watch crockett-75835` and its `-sold` companion, then this.
#
# The staging folder is built fresh from the one report file, so nothing else can travel with
# it: the same allowlist instinct scripts/build_site.py enforces for the reports site, applied
# here with a list of length one. `.vercel` is the one thing preserved across rebuilds, for the
# reason build_site.py preserves it too — without it every run would create a new project and
# the URL people were given would stop being the URL that gets updated.
#
# PROPERTYFINDER_ROOT, PYTHON and NPX are overridable, exactly as in deploy.sh and
# publish_ledger.sh, so every line up to the actual upload is testable against stubs.
set -euo pipefail

ROOT="${PROPERTYFINDER_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
PYTHON="${PYTHON:-.venv/bin/python}"
NPX="${NPX:-npx}"
STAGE="site-crockett"
MANIFEST="publish/crockett-75835/publish-manifest.yaml"
REPORT="reports/crockett-75835-map-public.html"
WATCH="crockett-75835"

cd "$ROOT"

refuse() {
  echo "refusing to publish: $1" >&2
  exit 1
}

[[ -f "$MANIFEST" ]] || refuse "$MANIFEST is missing"

# Rebuilt rather than assumed present. A publish script that uploads whatever happens to be on
# disk will one day upload a page built from a config that has since changed underneath it.
"$PYTHON" -m propertyfinder.cli map --watch "$WATCH" --public

[[ -s "$REPORT" ]] || refuse "$REPORT was not built, or is empty"

# Rendered checks before anything is uploaded: a placeholder left in visible text, a count that
# disagrees with the payload, or an uncaught script error is a page not worth publishing.
# Skipped, loudly, where there is no browser to drive.
if ! "$PYTHON" scripts/verify_page.py "$REPORT"; then
  refuse "$REPORT does not render cleanly — see the problems above"
fi

mkdir -p "$STAGE"
find "$STAGE" -mindepth 1 -maxdepth 1 ! -name '.vercel' -exec rm -rf {} +
cp "$REPORT" "$STAGE/index.html"

# No middleware, and nothing that could grow one. The whole point of this target is a link that
# opens for anybody; the only headers here keep it out of search results and stop an edge cache
# serving yesterday's shortlist after a rebuild.
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

if [[ -z "${CROCKETT_BASE_URL:-}" ]]; then
  echo "NOT VERIFIED: set CROCKETT_BASE_URL to this project's production alias and this" >&2
  echo "              script will fetch it as a visitor would. Until then nothing has" >&2
  echo "              confirmed that what went up is what a reader gets." >&2
  exit 0
fi

echo "verifying $CROCKETT_BASE_URL from outside"
"$PYTHON" scripts/verify_deploy.py "$CROCKETT_BASE_URL" --manifest "$MANIFEST"
