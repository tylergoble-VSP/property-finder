#!/usr/bin/env bash
# The wrapper a scheduler points at, which asserts its own preconditions before doing anything.
#
# WHY THIS FILE EXISTS. The launchd job used to invoke `.venv/bin/propertyfinder` directly. The
# project folder moved, the interpreter path baked into `.venv` at creation stopped resolving,
# and launchd swallowed exit 127 every morning for a fortnight. "It runs every morning" had
# quietly stopped being true and nothing said so (docs/PORTING-THE-REPORTS.md, lesson 16).
#
# So the scheduler's program is this script, and it refuses loudly rather than failing quietly:
# it checks that its working directory, its interpreter and its config are all actually there,
# and where they are not it writes a plain sentence to the log a person opens, prefixed so it
# can be grepped for, and exits non-zero. A wrapper that cannot find its own venv is the most
# likely failure of the whole pipeline and now the loudest.
#
# PROPERTYFINDER_ROOT, PYTHON and DAILY_ARGS are overridable so this is testable against a
# sandbox, the same seam deploy.sh and publish_ledger.sh use.
set -uo pipefail

ROOT="${PROPERTYFINDER_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
PYTHON="${PYTHON:-$ROOT/.venv/bin/python}"
CONFIG="${WATCH_CONFIG:-$ROOT/watch-config.yaml}"

shout() {
  # One shape of line, so that "did the morning run" is one grep whichever thing went wrong.
  echo "PROPERTYFINDER DAILY ABORTED: $1" >&2
  echo "  checked at $(date -u '+%Y-%m-%dT%H:%M:%SZ')" >&2
  echo "  root=$ROOT python=$PYTHON config=$CONFIG" >&2
  exit 78  # EX_CONFIG: a configuration problem, not a crash, and not the 127 that hid before
}

[[ -d "$ROOT" ]] || shout "the working directory does not exist — did the project folder move?"
cd "$ROOT" || shout "the working directory exists but cannot be entered"

# The exact failure that hid for a fortnight. A virtualenv's interpreter path is hard-coded at
# creation, so a moved folder leaves a .venv that is present and unusable — which is why this
# checks that the interpreter RUNS, not merely that the file is there.
[[ -x "$PYTHON" ]] || shout "no interpreter at $PYTHON — a moved project folder means a recreated venv"
# `import propertyfinder` alone is not the test: run from the repository root, a bare system
# interpreter finds the package directory on sys.path and imports it happily, then dies on the
# first dependency. Importing the CLI pulls in httpx, SQLAlchemy and pydantic, which is what
# actually distinguishes a working virtualenv from any old python.
"$PYTHON" -c "import propertyfinder.cli" 2>/dev/null ||
  shout "$PYTHON cannot import propertyfinder.cli — the venv is stale or incomplete; recreate it"

[[ -f "$CONFIG" ]] || shout "no watch config at $CONFIG"

echo "propertyfinder daily starting at $(date -u '+%Y-%m-%dT%H:%M:%SZ') in $ROOT"
# shellcheck disable=SC2086  # DAILY_ARGS is a deliberate word-split list of flags
"$PYTHON" -m propertyfinder.cli --watch-config "$CONFIG" daily ${DAILY_ARGS:-}
status=$?
echo "propertyfinder daily finished with exit $status"
exit "$status"
