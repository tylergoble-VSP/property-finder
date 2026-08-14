"""Time, in the one format this tool keeps it: UTC, to the second, as a string.

`"%Y-%m-%dT%H:%M:%SZ"` is a stored value, a sort key and a diff key simultaneously, and
it can be all three because it is fixed-width UTC: string order and chronological order
are the same order. That is not a stylistic preference — it is what lets the window
queries rank observations with an `ORDER BY` on a text column, and what lets "the sweep
before this one" mean nothing more complicated than "strictly less than this string".

Local time is never stored. A database that remembers a market across a daylight-saving
boundary in local time has silently reordered its own history.
"""
from __future__ import annotations

from datetime import datetime, timezone

TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def utc_now_iso() -> str:
    """Now, as this tool writes times down."""
    return datetime.now(timezone.utc).strftime(TS_FORMAT)
