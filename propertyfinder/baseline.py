"""What comparable homes actually sold for — the anchor every later valuation hangs on.

This is the first module in the tool that says anything about *value*, and it arrives at
Stage 7 rather than Stage 1 on purpose. The original built its first deal signal on the
estimate a listing site publishes, as a "placeholder", and had to retire it publicly:
asking prices re-anchor to that estimate, so scoring a listing against it largely measures
how closely the seller read the same web page. Placeholders become load-bearing. Nothing
here is valued until there are sales to value it against.

What "sales" can honestly mean depends on the state. Texas does not disclose sale prices,
so on the seed market the feed returned a real price for none of the sold homes. What it
does return once a home closes is a re-anchored estimate — the site's own number, revised
after the fact toward what the home actually fetched — plus the size and the sale date.
That is a usable surface and a worse one than a deed record, so the difference is carried
as a **basis label**: `disclosed` when real prices dominate, `proxy` when they do not. The
label travels with every number derived from it, all the way into the rendered page. A
Florida watch and a Texas watch run the same code and say different things about how much
to trust the result, which is the only honest way to run both.

Excluded from the comp set, always: builder plan-sheet rows (an address containing
"Plan," is a price list, not a home — a builder's ask-curve would drag the sold surface
toward what nobody has yet paid), land and lots (a different asset), and any home whose
size or price the feed never supplied.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from propertyfinder.store import latest_snapshot_rows
from propertyfinder.timeutil import TS_FORMAT, utc_now_iso

# Below this many sales a segment is noise dressed as a statistic, and `segment_for` hands
# back the whole market instead. Eight is small; it is also the point past which a median
# stops moving wildly when one more sale lands.
MIN_SEGMENT_N = 8

_EXCLUDED_TYPES = {"LOT", "LAND"}


def _percentile(sorted_values: list[float], q: float) -> float:
    """The nearest-rank percentile: an actually-observed value, never an interpolation.

    On a comp set of a dozen sales, a value half-way between two real sales is a number no
    home ever sold at. Reporting one of the real ones keeps the figure quotable — "homes
    like this one sold at $214 a foot" is a sentence that survives being checked.
    """
    index = min(len(sorted_values) - 1, int(q * len(sorted_values)))
    return sorted_values[index]


@dataclass(frozen=True)
class Segment:
    """Dollars per square foot for one slice of the market."""

    key: str  # "all" | a home type, e.g. "SINGLE_FAMILY"
    n: int
    p25: float
    p50: float
    p75: float


@dataclass(frozen=True)
class SoldBaseline:
    """A market's recent sales, reduced to what they cost per foot — and how much to
    trust that."""

    watch_name: str
    window_months: int
    n_solds: int  # sales that survived every exclusion and had a usable price and size
    price_disclosed: int  # of those, how many carried a real sale price
    basis: str  # "disclosed" | "proxy"
    solds_per_month: float  # velocity: how fast this market actually turns over
    window_start: str | None  # earliest sale date observed
    window_end: str | None
    segments: dict[str, Segment] = field(default_factory=dict)

    @property
    def is_proxy(self) -> bool:
        return self.basis == "proxy"

    def segment_for(self, home_type: str | None) -> Segment | None:
        """This home type's numbers, or the whole market's when its own slice is thin.

        Returns None on a market with no usable sales at all — a caller that cannot get a
        comp must say "not scored", not fall back to something it made up.
        """
        segment = self.segments.get(home_type or "")
        if segment is None or segment.n < MIN_SEGMENT_N:
            return self.segments.get("all")
        return segment


def compute_sold_baseline(
    session: Session,
    sold_watch_name: str,
    now_iso: str | None = None,
    window_months: int = 12,
) -> SoldBaseline:
    """Reduce a sold watch's latest observations to per-segment dollars per foot.

    The window trims sales old enough to describe a different market. A sale the feed gave
    no date is *kept* rather than dropped: it is almost certainly recent (it appeared in a
    sold sweep of a market we are watching now), and dropping every undated row would
    quietly shrink a thin comp set to nothing on exactly the markets that most need one.
    """
    now = datetime.strptime(now_iso or utc_now_iso(), TS_FORMAT)
    cutoff = now - timedelta(days=int(window_months * 30.4))
    rows = latest_snapshot_rows(session, sold_watch_name)

    values: list[float] = []
    by_type: dict[str, list[float]] = {}
    dates: list[str] = []
    disclosed = 0

    for row in rows:
        if not _is_comp(row, cutoff):
            continue
        # The real sale price where the state discloses one; the re-anchored post-sale
        # estimate where it does not. Which of the two was used is counted, not assumed.
        price = row.get("price") or row.get("zestimate")
        if not price:
            continue
        ppsf = price / row["sqft"]
        values.append(ppsf)
        by_type.setdefault(row.get("home_type") or "UNKNOWN", []).append(ppsf)
        if row.get("price"):
            disclosed += 1
        if row.get("date_sold"):
            dates.append(row["date_sold"][:10])

    segments: dict[str, Segment] = {}
    if values:
        segments["all"] = _segment("all", values)
        for key, vals in by_type.items():
            segments[key] = _segment(key, vals)

    dates.sort()
    return SoldBaseline(
        watch_name=sold_watch_name,
        window_months=window_months,
        n_solds=len(values),
        price_disclosed=disclosed,
        # A market is only called disclosed when real prices are the majority of what we
        # have. Anything less and the surface is mostly estimate, and says so.
        basis="disclosed" if values and disclosed >= 0.5 * len(values) else "proxy",
        solds_per_month=_velocity(dates),
        window_start=dates[0] if dates else None,
        window_end=dates[-1] if dates else None,
        segments=segments,
    )


def _is_comp(row: dict, cutoff: datetime) -> bool:
    """Whether one sold row belongs in the comp set at all."""
    if (row.get("listing_status") or "") != "sold":
        return False
    if "Plan," in (row.get("address") or ""):
        return False  # a builder's price list, not a sale
    if (row.get("home_type") or "") in _EXCLUDED_TYPES:
        return False  # land is a different asset priced a different way
    if not row.get("sqft"):
        return False
    sold_on = row.get("date_sold")
    if not sold_on:
        return True
    try:
        return datetime.strptime(sold_on[:10], "%Y-%m-%d") >= cutoff
    except ValueError:
        return True  # an unparseable date is missing data, not an old sale


def _segment(key: str, values: list[float]) -> Segment:
    ordered = sorted(values)
    return Segment(
        key=key,
        n=len(ordered),
        p25=_percentile(ordered, 0.25),
        p50=_percentile(ordered, 0.50),
        p75=_percentile(ordered, 0.75),
    )


def _velocity(dates: list[str]) -> float:
    """Sales per month across the span actually observed, rounded to one decimal.

    Measured over the observed span rather than the configured window, because a watch
    twelve months wide holding three months of sales turns over at the rate those three
    months show, not at a quarter of it.
    """
    if not dates:
        return 0.0
    span_days = max(
        1,
        (
            datetime.strptime(dates[-1], "%Y-%m-%d") - datetime.strptime(dates[0], "%Y-%m-%d")
        ).days,
    )
    return round(len(dates) / (span_days / 30.4), 1)
