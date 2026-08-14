"""003 — enrichment: what the detail engine adds, and when it was last asked.

Hand-written SQL, per this package's rule: a migration written against today's mapped
classes bakes today's model into a step that must still run in five years. `lot_sqft`
already exists — the search feed supplies it since Stage 3 — so this step adds only what
`zillow_property` contributes and the base schema does not already have: year built,
monthly dues, the effective tax rate, and the timestamp of the last detail-pull attempt.

SQLite has no `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`, so idempotence here costs an
extra line the other migrations do not need: check `PRAGMA table_info` before adding each
column, rather than leaning on the statement itself to no-op. The same seam noted in
m002 applies again — `properties` has carried these columns in the mapped metadata since
this commit, so a brand-new database gets them at step 001 and this step finds them
already there, while every database that ran 001 before today gets them here.
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Connection

# Column name -> SQL type. Order matters not at all; SQLite appends wherever it likes.
_NEW_COLUMNS = {
    "year_built": "INTEGER",
    "hoa_monthly": "REAL",
    "tax_rate": "REAL",
    "enriched_ts": "TEXT",
}


def apply(conn: Connection) -> None:
    existing = {row[1] for row in conn.execute(text("PRAGMA table_info(properties)"))}
    for name, coltype in _NEW_COLUMNS.items():
        if name not in existing:
            conn.execute(text(f"ALTER TABLE properties ADD COLUMN {name} {coltype}"))
