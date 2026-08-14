#!/usr/bin/env bash
# One command to the hosting service: rebuild site/ from the manifest, then push it live.
#
# Refuses outright, before ever calling `vercel`, if there is nothing worth deploying —
# site/ missing entirely, or present but holding no real page (the shape an empty
# site-manifest.yaml produces: an index and nothing to link from it). Uploading a folder
# nobody asked for is a worse failure than a clear refusal with a reason attached.
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

"$PYTHON" scripts/build_site.py

[[ -d "$SITE_DIR" ]] || refuse "$SITE_DIR does not exist (build_site.py should have created it)"

page_count=$(find "$SITE_DIR" -maxdepth 1 -type f -name '*.html' ! -name 'index.html' | wc -l | tr -d ' ')
[[ "$page_count" -gt 0 ]] || refuse "$SITE_DIR has no published page — is site-manifest.yaml empty?"

echo "deploying $page_count page(s) from $SITE_DIR"
"$NPX" vercel deploy --prod --yes --cwd "$SITE_DIR"
