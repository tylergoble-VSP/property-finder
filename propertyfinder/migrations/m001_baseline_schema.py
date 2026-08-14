"""001 — the baseline: properties and snapshots.

The first migration is simply the schema the models describe. `create_all` is honest
here and only here: there is nothing to migrate *from*, so the mapped metadata is the
truth. Every step after this one is written by hand, because from here on the models and
the database disagree until a migration says otherwise.
"""
from __future__ import annotations

from sqlalchemy.engine import Connection

from propertyfinder.domain import Base


def apply(conn: Connection) -> None:
    Base.metadata.create_all(bind=conn)
