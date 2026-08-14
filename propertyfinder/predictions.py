"""The honesty loop: write the expectation down first, mark it later.

A valuation model that is never scored is an opinion with arithmetic attached. So every
sweep freezes what the model expects each active listing to fetch, and when that home
later appears in the sold watch the frozen number is marked against what it actually went
for. The error accumulates per segment, and the report prints it where the person relying
on the tool can read it.

This goes in *now*, in the same stage as the model itself, rather than as an afterthought.
It is the only mechanism that can ever tell us whether the hedonic fit earns its keep, and
a calibration loop added after six months of predictions has six months of nothing to say.

Two rules keep the numbers meaningful. Recording is **idempotent**: one open prediction per
home per watch, so running the daily job twice does not double-count, and a home that sits
on the market for four months is judged on the expectation formed when it first appeared —
not on a fresher one revised as the market moved under it. And resolutions are counted
**by basis**: a home whose sale price the state published is a genuine test, and one closed
against a post-sale estimate is a weaker one, so the report never mixes them into a single
number that would flatter the first or libel the second.

No scientific stack is imported here on purpose. The model arrives as an argument and is
used through one method, so recording predictions costs a caller nothing it was not
already paying, and the calibration report — which needs no model at all — stays readable
from a plain command line.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from propertyfinder.domain import Prediction
from propertyfinder.store import latest_snapshot_rows

# Below this, a segment's error figure is an anecdote. Reported anyway — hiding it would
# be its own dishonesty — but the count travels with it so a reader can judge.
MEANINGFUL_N = 5


def record_predictions(session: Session, watch_name: str, now_iso: str, model) -> int:
    """Freeze an expected price for every active listing that has no open one yet.

    `model` is anything with an `expected(row)` returning an object carrying `.expected`
    — in practice `stats.HedonicModel`, passed in rather than imported so this module
    stays free of the scientific stack. A model that cannot judge a particular home
    returns None and that home simply gets no prediction: an unscored home is an honest
    outcome, and a guessed one would poison the very measurement this exists to take.

    Returns how many new predictions were written.
    """
    if model is None:
        return 0

    # Homes that already have something to say. Open predictions, obviously — but also
    # anything predicted at this very timestamp, which is how a home that sold and was
    # resolved, yet still appears in the for-sale watch, avoids colliding with itself when
    # the job is run twice inside the same second.
    already = {
        row[0]
        for row in session.execute(
            select(Prediction.zpid).where(
                Prediction.watch_name == watch_name,
                or_(Prediction.resolved_ts.is_(None), Prediction.made_ts == now_iso),
            )
        ).all()
    }

    written = 0
    for row in latest_snapshot_rows(session, watch_name):
        if (row.get("listing_status") or "") == "sold" or row["zpid"] in already:
            continue
        expectation = model.expected(row)
        if expectation is None:
            continue
        session.add(
            Prediction(
                zpid=row["zpid"],
                watch_name=watch_name,
                made_ts=now_iso,
                track="resale",
                segment=f"resale:{row.get('home_type') or 'UNKNOWN'}",
                expected_price=round(float(expectation.expected), 2),
                list_price=row.get("price"),
                sqft=row.get("sqft"),
            )
        )
        already.add(row["zpid"])
        written += 1

    session.commit()
    return written


def resolve_predictions(session: Session, sold_watch_name: str, now_iso: str) -> int:
    """Mark every open prediction for a home that has now turned up sold.

    The real sale price where the state discloses one; the post-sale re-anchored estimate
    where it does not. Which was used is written on the row, once, and never inferred
    again. Returns how many were resolved.
    """
    observed: dict[str, tuple[float, str]] = {}
    for row in latest_snapshot_rows(session, sold_watch_name):
        if row.get("price"):
            observed[row["zpid"]] = (row["price"], "disclosed")
        elif row.get("zestimate"):
            observed[row["zpid"]] = (row["zestimate"], "proxy")
    if not observed:
        return 0

    open_predictions = (
        session.execute(
            select(Prediction).where(
                Prediction.resolved_ts.is_(None), Prediction.zpid.in_(observed.keys())
            )
        )
        .scalars()
        .all()
    )

    for prediction in open_predictions:
        price, basis = observed[prediction.zpid]
        prediction.observed_price = price
        prediction.observed_basis = basis
        prediction.error_pct = (prediction.expected_price - price) / price * 100
        prediction.resolved_ts = now_iso

    session.commit()
    return len(open_predictions)


@dataclass(frozen=True)
class SegmentCalibration:
    """How wrong this tool has been, in one slice of one market."""

    segment: str
    n: int
    mape: float  # mean absolute error, %
    bias: float  # mean signed error, % — positive means we expected too much
    n_disclosed: int  # of n, resolved against a real published sale price
    n_proxy: int


@dataclass(frozen=True)
class CalibrationReport:
    n_open: int
    n_resolved: int
    n_disclosed: int
    n_proxy: int
    segments: list[SegmentCalibration] = field(default_factory=list)


def calibration_report(session: Session) -> CalibrationReport:
    """Every resolved prediction, reduced to error per segment.

    Mean *absolute* error is the headline because errors in both directions are equally
    wrong to a buyer; the signed mean rides alongside it, because a model that is wrong
    by 8% in one direction every time is a different problem from one that is wrong by 8%
    at random, and only the second is noise.
    """
    predictions = session.execute(select(Prediction)).scalars().all()
    resolved = [p for p in predictions if p.resolved_ts and p.error_pct is not None]

    by_segment: dict[str, list[Prediction]] = {}
    for prediction in resolved:
        by_segment.setdefault(prediction.segment, []).append(prediction)

    segments = []
    for segment, rows in sorted(by_segment.items()):
        errors = [p.error_pct for p in rows]
        disclosed = sum(1 for p in rows if p.observed_basis == "disclosed")
        segments.append(
            SegmentCalibration(
                segment=segment,
                n=len(errors),
                mape=round(statistics.fmean(abs(e) for e in errors), 1),
                bias=round(statistics.fmean(errors), 1),
                n_disclosed=disclosed,
                n_proxy=len(errors) - disclosed,
            )
        )

    disclosed_total = sum(1 for p in resolved if p.observed_basis == "disclosed")
    return CalibrationReport(
        n_open=sum(1 for p in predictions if not p.resolved_ts),
        n_resolved=len(resolved),
        n_disclosed=disclosed_total,
        n_proxy=len(resolved) - disclosed_total,
        segments=segments,
    )


def format_calibration(report: CalibrationReport) -> str:
    """The report as a person reads it, in a terminal, with its caveats attached."""
    lines = [
        f"Calibration · {report.n_resolved} resolved "
        f"({report.n_disclosed} against real sale prices, {report.n_proxy} against "
        f"post-sale estimates) · {report.n_open} still open",
    ]
    if not report.segments:
        lines.append(
            "  nothing resolved yet — this fills in as watched listings sell, which is "
            "the only schedule it can keep"
        )
        return "\n".join(lines)

    lines.append(f"  {'segment':24} {'n':>4} {'real':>5} {'error%':>8} {'bias%':>8}")
    for segment in report.segments:
        note = ""
        if segment.n_disclosed == 0:
            note = "  (estimate basis — not a true accuracy test)"
        elif segment.n < MEANINGFUL_N:
            note = "  (too few to read much into)"
        lines.append(
            f"  {segment.segment:24} {segment.n:>4} {segment.n_disclosed:>5} "
            f"{segment.mape:>8.1f} {segment.bias:>+8.1f}{note}"
        )
    return "\n".join(lines)
