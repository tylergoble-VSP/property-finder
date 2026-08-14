"""002 — predictions: what the tool expected, so it can be marked afterwards.

Hand-written SQL rather than `create_all`, per the rule in this package's docstring: a
migration written in terms of today's mapped classes bakes today's model into a step that
must still run years from now.

One seam worth naming. Migration 001 *is* `create_all` over the mapped metadata, and that
metadata has since grown this table — so a brand-new database gets `predictions` at step
001 and this step finds it already there, while every database that ran 001 before today
gets it here. `CREATE TABLE IF NOT EXISTS` is what makes both paths land on the same
schema, which is also the reason every step in this package is required to survive being
run twice.
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Connection

_PREDICTIONS = """
CREATE TABLE IF NOT EXISTS predictions (
    prediction_id  INTEGER PRIMARY KEY,
    zpid           TEXT NOT NULL REFERENCES properties(zpid),
    watch_name     TEXT NOT NULL,
    made_ts        TEXT NOT NULL,
    track          TEXT NOT NULL,
    segment        TEXT NOT NULL,
    expected_price REAL NOT NULL,
    list_price     REAL,
    sqft           REAL,
    resolved_ts    TEXT,
    observed_price REAL,
    observed_basis TEXT,
    error_pct      REAL,
    CONSTRAINT uq_prediction UNIQUE (zpid, watch_name, made_ts)
)
"""

# Resolution reads "every open prediction for these homes" on every sweep, and openness is
# the selective half of that question — almost every row is resolved eventually.
_OPEN_INDEX = """
CREATE INDEX IF NOT EXISTS ix_predictions_open
ON predictions (watch_name, resolved_ts)
"""


def apply(conn: Connection) -> None:
    conn.execute(text(_PREDICTIONS))
    conn.execute(text(_OPEN_INDEX))
