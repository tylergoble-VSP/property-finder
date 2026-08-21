"""A run that says it happened, in a place something else looks.

The scheduled job kept pointing at a virtualenv that had moved and failed with exit 127 every
morning for a fortnight. launchd swallowed it. "It runs every morning" had quietly stopped
being true and nothing anywhere said so (docs/PORTING-THE-REPORTS.md, lesson 16).

The failure mode is not that the run broke. Runs break. It is that a pipeline whose silence is
indistinguishable from success will eventually be silent, and nobody will know which of the two
they are looking at. So a run leaves a mark, the digest reads the mark out loud, and the deploy
script complains when the mark has gone stale. Three cheap things, and between them a missing
morning becomes visible on the next one.

`reports/.last-daily` rather than a log line, because a log is a thing you go and read after you
have already suspected something. This is a fact something else can check without being asked.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

HEARTBEAT_NAME = ".last-daily"

# How long a heartbeat stays believable. `daily` is a once-a-morning job, so a mark older than
# this means at least one morning went missing — generous enough to survive a laptop that was
# shut for a day, tight enough that a fortnight of exit 127 cannot hide inside it.
STALE_AFTER = timedelta(hours=36)

_FMT = "%Y-%m-%dT%H:%M:%SZ"


@dataclass(frozen=True)
class Heartbeat:
    """What the last run of `daily` did. Deliberately small, and deliberately not a log."""

    finished_ts: str
    exit_status: int
    calls_spent: int
    note: str = ""

    @property
    def ok(self) -> bool:
        return self.exit_status == 0

    def age(self, now_iso: str) -> timedelta:
        return datetime.strptime(now_iso, _FMT) - datetime.strptime(self.finished_ts, _FMT)

    def is_stale(self, now_iso: str, after: timedelta = STALE_AFTER) -> bool:
        return self.age(now_iso) > after

    def sentence(self, now_iso: str | None = None) -> str:
        """One line a person reads in a digest, or a deploy script prints to stderr."""
        state = "ok" if self.ok else f"FAILED (exit {self.exit_status})"
        age = ""
        if now_iso:
            hours = self.age(now_iso).total_seconds() / 3600
            age = f", {hours:.0f}h ago" if hours >= 1 else ", within the hour"
        spend = f"{self.calls_spent} billable call{'' if self.calls_spent == 1 else 's'}"
        return f"last daily run: {self.finished_ts} — {state}, {spend}{age}"


def write(reports_dir: Path, finished_ts: str, exit_status: int, calls_spent: int,
          note: str = "") -> Path:
    """Stamp the run. Called at the very end of `daily`, whatever the run's outcome.

    A failed run writes a heartbeat too, carrying its exit status. That is the point: "it ran
    and it failed" and "it never ran" are different problems, and a heartbeat only written on
    success cannot tell them apart.
    """
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / HEARTBEAT_NAME
    path.write_text(
        json.dumps(
            {
                "finished_ts": finished_ts,
                "exit_status": exit_status,
                "calls_spent": calls_spent,
                "note": note,
            },
            indent=2,
        )
        + "\n"
    )
    return path


def read(reports_dir: Path) -> Heartbeat | None:
    """The last run's mark, or None where there is none — which is itself an answer."""
    path = reports_dir / HEARTBEAT_NAME
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text())
        return Heartbeat(
            finished_ts=str(raw["finished_ts"]),
            exit_status=int(raw["exit_status"]),
            calls_spent=int(raw["calls_spent"]),
            note=str(raw.get("note") or ""),
        )
    except (ValueError, KeyError, TypeError):
        # A corrupt heartbeat is treated as no heartbeat. It is a claim about the world, and a
        # claim that cannot be read is not a weaker claim, it is none — the same instinct
        # `dataquality.attribute_builder` applies to contradictory evidence.
        return None
