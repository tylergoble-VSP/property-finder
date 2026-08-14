"""Numbered, one-way schema steps, discovered from this folder.

The original build's policy was "additive changes only, and hand-run the column adds",
which worked right up until the day it didn't: a laptop's database and a fresh one were
the same schema by coincidence rather than by record, and nothing could answer the
question "which version is this file?".

So the record exists. Each step is a module named `mNNN_what_it_does.py` exposing
`apply(conn)`, the number is its version, and the runner in `store.py` applies the ones a
database has not seen and stamps each as it goes. Steps are found by scanning this
package rather than by maintaining a list, because a list is a second place to forget.

Three rules for writing one:

1. **Never edit an applied migration.** Databases in the wild already ran it; correcting
   it changes only the schema of machines that never had the bug. Write the next number.
2. **Make it safe to run twice.** `CREATE TABLE IF NOT EXISTS`, one logical change per
   step. This is not belt-and-braces: Python's SQLite driver commits schema statements
   implicitly, so a step that raises half-way has already left part of its work behind.
   What the runner guarantees is narrower and still useful — the *stamp* is written only
   when the step returns, so a failed version is never recorded and is retried on the
   next run. A migration that cannot survive that retry is a migration that cannot fail.
3. **Keep it in SQL, not in models.** `apply(conn)` receives a connection, not a session.
   Writing a migration in terms of today's mapped classes bakes today's model into a step
   that must still run in five years' time.
"""
from __future__ import annotations

import importlib
import pkgutil
import re
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy.engine import Connection

_NUMBERED = re.compile(r"^m(\d+)_")


@dataclass(frozen=True)
class Migration:
    """One numbered step: its version, where it came from, and what it does."""

    version: int
    name: str
    apply: Callable[[Connection], None]


def ordered(migrations: list[Migration]) -> list[Migration]:
    """Sort by version, refusing any set in which two steps claim the same number.

    A version is an identity — it is what a database records having run — so two steps
    sharing one is not a tie to break quietly. It is a merge that went wrong, and the
    only safe response is to say so before either step touches a schema.
    """
    seen: dict[int, str] = {}
    for m in migrations:
        if m.version in seen:
            raise ValueError(
                f"two migrations claim version {m.version}: {seen[m.version]} and "
                f"{m.name} — a version is an identity, so renumber one of them"
            )
        seen[m.version] = m.name
    return sorted(migrations, key=lambda m: m.version)


def discover() -> list[Migration]:
    """Every migration module in this package, in version order."""
    found = []
    for info in pkgutil.iter_modules(__path__):
        match = _NUMBERED.match(info.name)
        if not match:
            continue
        module = importlib.import_module(f"{__name__}.{info.name}")
        apply = getattr(module, "apply", None)
        if not callable(apply):
            raise TypeError(f"migration {info.name} has no apply(conn) function")
        found.append(Migration(version=int(match.group(1)), name=info.name, apply=apply))
    return ordered(found)
