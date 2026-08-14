"""Two tables that hold the market, and one that holds the tool to account.

`properties` is **identity**: one row per home ever seen, in any watch, holding the facts
that change slowly or never — where it stands, how big it is, when we first laid eyes on
it. `snapshots` is **observation**: one row per home per watch per sweep, holding the
facts that change constantly — the asking price, the status word, days on market.

Splitting them this way is what makes history queryable rather than merely archived.
Reading a report is "the newest observation per home"; diffing a sweep is "the newest
observation per home *before* this one"; a price-cut ledger is "all of them, in order".
Every claim this tool will ever make about a market — cut $15,000 in nine days, back on
market, gone — is one of those three shapes over these two tables. The original build ran
eighteen sweeps and five and a half thousand observations through this schema and never
once had to argue with it.

A home is stored once and observed many times, so a snapshot points at a property and
never the other way round. The foreign key is declared and, thanks to the pragma
`build_engine` sets, actually enforced: an observation of a home the database has never
heard of is a bug, not a row.

`predictions` is the third table and a different kind of thing: not a fact about the
market but a claim this tool made about it, written down before the answer was known so
that it can be marked afterwards. Nothing else here can be wrong; that one can, on
purpose.
"""
from __future__ import annotations

from sqlalchemy import Float, ForeignKey, Integer, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class WatchedProperty(Base):
    """One home, once — the identity row, shared by every watch that sees it.

    Fields here are the ones a home carries around with it rather than the ones a
    listing asserts today. They are backfilled rather than overwritten: the feed
    routinely omits square footage on one sighting and supplies it on the next, and a
    later "unknown" must never erase an earlier fact.
    """

    __tablename__ = "properties"

    zpid: Mapped[str] = mapped_column(Text, primary_key=True)
    address: Mapped[str | None] = mapped_column(Text)
    home_type: Mapped[str | None] = mapped_column(Text)
    beds: Mapped[float | None] = mapped_column(Float)
    baths: Mapped[float | None] = mapped_column(Float)
    sqft: Mapped[float | None] = mapped_column(Float)
    lot_sqft: Mapped[float | None] = mapped_column(Float)
    lat: Mapped[float | None] = mapped_column(Float)
    lon: Mapped[float | None] = mapped_column(Float)
    link: Mapped[str | None] = mapped_column(Text)
    image_url: Mapped[str | None] = mapped_column(Text)
    date_sold: Mapped[str | None] = mapped_column(Text)  # set on a `sold` sweep
    first_seen: Mapped[str] = mapped_column(Text, nullable=False)
    last_seen: Mapped[str] = mapped_column(Text, nullable=False)


class PropertySnapshot(Base):
    """One home as one watch saw it at one moment.

    The unique constraint is the whole history model in one line: a sweep observes each
    home exactly once, so re-running a sweep at the same timestamp is refused by the
    database rather than quietly doubling the record. Nothing here is ever updated in
    place — an observation that can be edited is not evidence.
    """

    __tablename__ = "snapshots"
    __table_args__ = (
        UniqueConstraint("zpid", "watch_name", "snapshot_ts", name="uq_snapshot"),
    )

    snapshot_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    zpid: Mapped[str] = mapped_column(Text, ForeignKey("properties.zpid"), nullable=False)
    watch_name: Mapped[str] = mapped_column(Text, nullable=False)
    snapshot_ts: Mapped[str] = mapped_column(Text, nullable=False)
    listing_status: Mapped[str | None] = mapped_column(Text)
    price: Mapped[float | None] = mapped_column(Float)
    zestimate: Mapped[float | None] = mapped_column(Float)
    rent_zestimate: Mapped[float | None] = mapped_column(Float)
    tax_assessed_value: Mapped[float | None] = mapped_column(Float)
    days_on_zillow: Mapped[int | None] = mapped_column(Integer)
    status_text: Mapped[str | None] = mapped_column(Text)
    distance_miles: Mapped[float | None] = mapped_column(Float)


class Prediction(Base):
    """A price this tool expected, frozen before the market answered.

    The calibration loop lives in this table. Each sweep freezes what the model expects a
    listing to fetch; when that home later turns up in the sold watch, the row is resolved
    against what it actually went for and the error is kept. Without it there is no way to
    answer the only question that matters about a valuation model — *how wrong is it,
    typically?* — and every claim the tool makes is a claim about itself.

    `observed_basis` records which surface the resolution used: a real disclosed sale
    price, or the post-sale re-anchored estimate where the state publishes nothing. The
    two are counted separately for the rest of the report's life, because only the first
    is a genuine accuracy test.

    Unique on (home, watch, when it was made), and at most one *unresolved* row per home
    and watch — enforced by `record_predictions` rather than by the schema, since it is a
    rule about openness rather than about identity.
    """

    __tablename__ = "predictions"
    __table_args__ = (
        UniqueConstraint("zpid", "watch_name", "made_ts", name="uq_prediction"),
    )

    prediction_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    zpid: Mapped[str] = mapped_column(Text, ForeignKey("properties.zpid"), nullable=False)
    watch_name: Mapped[str] = mapped_column(Text, nullable=False)
    made_ts: Mapped[str] = mapped_column(Text, nullable=False)
    track: Mapped[str] = mapped_column(Text, nullable=False)  # resale (new build joins later)
    segment: Mapped[str] = mapped_column(Text, nullable=False)
    expected_price: Mapped[float] = mapped_column(Float, nullable=False)
    list_price: Mapped[float | None] = mapped_column(Float)
    sqft: Mapped[float | None] = mapped_column(Float)
    resolved_ts: Mapped[str | None] = mapped_column(Text)
    observed_price: Mapped[float | None] = mapped_column(Float)
    observed_basis: Mapped[str | None] = mapped_column(Text)  # disclosed | proxy
    error_pct: Mapped[float | None] = mapped_column(Float)  # (expected − observed) / observed
