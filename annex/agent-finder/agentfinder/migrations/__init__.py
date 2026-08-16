"""The annex's numbered schema steps — same rules as core, a reserved version range.

Core owns migration versions 1–99; the annex owns 100+. Both series stamp the one
`schema_version` table in the one shared SQLite file, so a collision would mean a step
silently never running — which is why every list handed to core's runner passes through
`ordered()`, and why `agentfinder.store.migrate` unions the two series and lets `ordered()`
raise before either touches a schema.

`discover()` here is the twelve-line twin of core's, scanning this package. A parameter added
to core's `discover()` that only the annex would ever pass is core knowing about the annex by
implication (docs/REBUILD.md item 2); twelve duplicated lines is the cheaper honesty.
"""
from __future__ import annotations

import importlib
import pkgutil
import re

from propertyfinder.migrations import Migration, ordered

ANNEX_FLOOR = 100
_NUMBERED = re.compile(r"^m(\d+)_")


def discover() -> list[Migration]:
    """Every annex migration module, in version order."""
    found = []
    for info in pkgutil.iter_modules(__path__):
        match = _NUMBERED.match(info.name)
        if not match:
            continue
        version = int(match.group(1))
        if version < ANNEX_FLOOR:
            raise ValueError(
                f"annex migration {info.name} claims version {version}, below the reserved "
                f"floor {ANNEX_FLOOR} — core owns 1–99, the annex owns 100+"
            )
        module = importlib.import_module(f"{__name__}.{info.name}")
        apply = getattr(module, "apply", None)
        if not callable(apply):
            raise TypeError(f"migration {info.name} has no apply(conn) function")
        found.append(Migration(version=version, name=info.name, apply=apply))
    return ordered(found)
