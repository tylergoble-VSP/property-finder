"""Freezing an expectation, marking it, and counting the two bases apart.

The model is a stand-in here rather than the real fit. That is deliberate: this module's
job is bookkeeping — one open prediction per home, resolution against the right price, the
right arithmetic in the report — and a real regression in the middle of it would make
every assertion depend on a coefficient nobody is testing.
"""
from dataclasses import dataclass

import pytest
from conftest import make_listing
from sqlalchemy import select

from propertyfinder.domain import Prediction
from propertyfinder.predictions import (
    calibration_report,
    format_calibration,
    record_predictions,
    resolve_predictions,
)
from propertyfinder.store import record_snapshot, upsert_property

NOW = "2026-07-10T00:00:00Z"
LATER = "2026-08-01T00:00:00Z"
WATCH, SOLD_WATCH = "walsh-aledo", "walsh-aledo-sold"


@dataclass
class FlatRateModel:
    """A model that thinks every home is worth a fixed amount per square foot. Enough of
    an interface for `record_predictions`, and nothing else."""

    ppsf: float = 200.0
    basis: str = "proxy"

    def expected(self, row):
        if not row.get("sqft"):
            return None
        return _Expectation(expected=self.ppsf * row["sqft"])


@dataclass
class _Expectation:
    expected: float


def _sweep(sessions, listings, watch=WATCH, ts=NOW):
    with sessions() as s:
        for listing in listings:
            upsert_property(s, listing, ts)
        s.flush()
        for listing in listings:
            record_snapshot(s, listing, watch, ts, listing_status=listing.listing_status)
        s.commit()


def _sold(zpid, *, price=None, estimate=None, sqft=2000):
    return make_listing(
        zpid, price=price, zestimate=estimate, sqft=sqft,
        listing_status="sold", status_text="Sold", date_sold="2026-07-05",
    )


# -- freezing ----------------------------------------------------------------------------


def test_a_prediction_is_frozen_per_active_listing(sessions):
    _sweep(sessions, [make_listing("a", price=380_000, sqft=2000)])
    with sessions() as s:
        assert record_predictions(s, WATCH, NOW, FlatRateModel()) == 1
        row = s.execute(select(Prediction)).scalars().one()

    assert row.expected_price == 400_000  # 2,000 square feet at $200
    assert row.list_price == 380_000 and row.sqft == 2000
    assert row.segment == "resale:SINGLE_FAMILY" and row.track == "resale"
    assert row.resolved_ts is None and row.made_ts == NOW


def test_recording_twice_leaves_one_open_prediction(sessions):
    """The daily job runs every morning and a listing sits for months. A home is judged on
    the expectation formed when it first appeared, not on a fresher one revised as the
    market moved under it — otherwise the model is scored against its own hindsight."""
    _sweep(sessions, [make_listing("a", price=380_000, sqft=2000)])
    with sessions() as s:
        assert record_predictions(s, WATCH, NOW, FlatRateModel()) == 1
        assert record_predictions(s, WATCH, LATER, FlatRateModel(ppsf=999)) == 0
        assert len(s.execute(select(Prediction)).scalars().all()) == 1
        assert calibration_report(s).n_open == 1


def test_a_home_the_model_cannot_judge_gets_no_prediction(sessions):
    _sweep(sessions, [make_listing("sized", price=380_000, sqft=2000),
                      make_listing("blank", price=380_000, sqft=None)])
    with sessions() as s:
        assert record_predictions(s, WATCH, NOW, FlatRateModel()) == 1
        assert [p.zpid for p in s.execute(select(Prediction)).scalars()] == ["sized"]


def test_sold_rows_are_not_predicted_about(sessions):
    """A prediction about a home that has already sold is not a prediction."""
    _sweep(sessions, [_sold("closed", estimate=400_000)], watch=WATCH)
    with sessions() as s:
        assert record_predictions(s, WATCH, NOW, FlatRateModel()) == 0


def test_no_model_records_nothing(sessions):
    """A market too thin to fit produces no model, and no predictions — rather than
    predictions from something weaker that would then be scored as if it were the model."""
    _sweep(sessions, [make_listing("a", price=380_000, sqft=2000)])
    with sessions() as s:
        assert record_predictions(s, WATCH, NOW, None) == 0


def test_predictions_are_scoped_to_their_watch(sessions):
    _sweep(sessions, [make_listing("a", price=380_000, sqft=2000)])
    _sweep(sessions, [make_listing("b", price=500_000, sqft=2500)], watch="other")
    with sessions() as s:
        assert record_predictions(s, WATCH, NOW, FlatRateModel()) == 1
        assert record_predictions(s, "other", NOW, FlatRateModel()) == 1
        watches = {p.watch_name for p in s.execute(select(Prediction)).scalars()}
    assert watches == {WATCH, "other"}


# -- resolving ---------------------------------------------------------------------------


def test_resolution_against_a_real_disclosed_price(sessions):
    _sweep(sessions, [make_listing("a", price=380_000, sqft=2000)])
    with sessions() as s:
        record_predictions(s, WATCH, NOW, FlatRateModel())
    _sweep(sessions, [_sold("a", price=372_000)], watch=SOLD_WATCH, ts=LATER)

    with sessions() as s:
        assert resolve_predictions(s, SOLD_WATCH, LATER) == 1
        row = s.execute(select(Prediction)).scalars().one()

    assert row.observed_basis == "disclosed" and row.observed_price == 372_000
    assert row.resolved_ts == LATER
    assert row.error_pct == pytest.approx((400_000 - 372_000) / 372_000 * 100)


def test_resolution_falls_back_to_the_post_sale_estimate(sessions):
    """Texas. The home sold and the state published nothing, so the re-anchored estimate
    is what there is — used, and labelled, and counted apart from the real ones."""
    _sweep(sessions, [make_listing("b", price=380_000, sqft=2000)])
    with sessions() as s:
        record_predictions(s, WATCH, NOW, FlatRateModel())
    _sweep(sessions, [_sold("b", estimate=390_000)], watch=SOLD_WATCH, ts=LATER)

    with sessions() as s:
        assert resolve_predictions(s, SOLD_WATCH, LATER) == 1
        row = s.execute(select(Prediction)).scalars().one()

    assert row.observed_basis == "proxy" and row.observed_price == 390_000
    assert calibration_report(s).n_disclosed == 0


def test_a_disclosed_price_wins_over_the_estimate_on_the_same_row(sessions):
    _sweep(sessions, [make_listing("c", price=380_000, sqft=2000)])
    with sessions() as s:
        record_predictions(s, WATCH, NOW, FlatRateModel())
    _sweep(sessions, [_sold("c", price=372_000, estimate=999_999)], watch=SOLD_WATCH, ts=LATER)

    with sessions() as s:
        resolve_predictions(s, SOLD_WATCH, LATER)
        assert s.execute(select(Prediction)).scalars().one().observed_price == 372_000


def test_a_resolved_prediction_is_not_resolved_again(sessions):
    _sweep(sessions, [make_listing("a", price=380_000, sqft=2000)])
    with sessions() as s:
        record_predictions(s, WATCH, NOW, FlatRateModel())
    _sweep(sessions, [_sold("a", price=372_000)], watch=SOLD_WATCH, ts=LATER)

    with sessions() as s:
        assert resolve_predictions(s, SOLD_WATCH, LATER) == 1
        assert resolve_predictions(s, SOLD_WATCH, "2026-09-01T00:00:00Z") == 0
        assert s.execute(select(Prediction)).scalars().one().resolved_ts == LATER


def test_resolving_reopens_a_home_for_a_fresh_prediction(sessions):
    """Once the open prediction is closed, the home may be predicted about again — a home
    that comes back on the market is a new question, not a re-run of the old one."""
    _sweep(sessions, [make_listing("a", price=380_000, sqft=2000)])
    with sessions() as s:
        record_predictions(s, WATCH, NOW, FlatRateModel())
    _sweep(sessions, [_sold("a", price=372_000)], watch=SOLD_WATCH, ts=LATER)
    with sessions() as s:
        resolve_predictions(s, SOLD_WATCH, LATER)
        assert record_predictions(s, WATCH, LATER, FlatRateModel()) == 1
        assert len(s.execute(select(Prediction)).scalars().all()) == 2


def test_nothing_sold_resolves_nothing(sessions):
    _sweep(sessions, [make_listing("a", price=380_000, sqft=2000)])
    with sessions() as s:
        record_predictions(s, WATCH, NOW, FlatRateModel())
        assert resolve_predictions(s, SOLD_WATCH, LATER) == 0


# -- the report ----------------------------------------------------------------------------


def _resolve_one(sessions, zpid, expected_ppsf, *, price=None, estimate=None):
    _sweep(sessions, [make_listing(zpid, price=380_000, sqft=2000)])
    with sessions() as s:
        record_predictions(s, WATCH, NOW, FlatRateModel(ppsf=expected_ppsf))
    _sweep(sessions, [_sold(zpid, price=price, estimate=estimate)], watch=SOLD_WATCH, ts=LATER)
    with sessions() as s:
        resolve_predictions(s, SOLD_WATCH, LATER)


def test_the_report_counts_the_two_bases_apart(sessions):
    """Only a resolution against a published sale price is a real accuracy test, so the
    report never blends the two into one number that would flatter it."""
    _resolve_one(sessions, "real", 200, price=400_000)  # expected 400k, sold 400k: 0% off
    _resolve_one(sessions, "est", 210, estimate=400_000)  # expected 420k: +5%

    with sessions() as s:
        report = calibration_report(s)

    assert (report.n_resolved, report.n_open) == (2, 0)
    assert (report.n_disclosed, report.n_proxy) == (1, 1)
    segment = report.segments[0]
    assert segment.segment == "resale:SINGLE_FAMILY" and segment.n == 2
    assert (segment.n_disclosed, segment.n_proxy) == (1, 1)


def test_error_and_bias_are_different_questions(sessions):
    """One prediction 5% high and one 5% low average to no bias at all, while the typical
    miss is 5% either way. A model wrong in one direction every time is a different
    problem from one wrong at random, and only the second is noise."""
    _resolve_one(sessions, "high", 210, price=400_000)  # expected 420k vs 400k: +5%
    _resolve_one(sessions, "low", 190, price=400_000)  # expected 380k vs 400k: −5%

    with sessions() as s:
        segment = calibration_report(s).segments[0]

    assert segment.mape == 5.0
    assert segment.bias == 0.0


def test_a_report_with_nothing_resolved_says_so_rather_than_showing_zeroes(sessions):
    _sweep(sessions, [make_listing("a", price=380_000, sqft=2000)])
    with sessions() as s:
        record_predictions(s, WATCH, NOW, FlatRateModel())
        report = calibration_report(s)

    assert (report.n_resolved, report.n_open, report.segments) == (0, 1, [])
    assert "nothing resolved yet" in format_calibration(report)


def test_the_printed_report_flags_a_segment_resolved_only_against_estimates(sessions):
    _resolve_one(sessions, "est", 210, estimate=400_000)
    with sessions() as s:
        text = format_calibration(calibration_report(s))

    assert "not a true accuracy test" in text
    assert "resale:SINGLE_FAMILY" in text and "1 resolved" in text
