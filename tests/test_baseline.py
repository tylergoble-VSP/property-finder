"""The sold baseline, and the label that says how much to trust it.

Two markets are simulated here and only one of them exists in reality: a non-disclosure
state where the feed returns no sale prices at all, and a disclosure state where it
returns most of them. The same code runs on both; the difference must show up in `basis`
and be countable in `price_disclosed`, because everything downstream inherits it.
"""
from conftest import make_listing

from propertyfinder.baseline import compute_sold_baseline
from propertyfinder.store import record_snapshot, upsert_property

NOW = "2026-07-10T00:00:00Z"
SOLD_WATCH = "walsh-aledo-sold"


def _sold(zpid: str, sqft, *, price=None, estimate=None, home_type="SINGLE_FAMILY",
          date_sold="2026-06-01", address=None):
    """One closed sale, as a sold sweep records it: a size, a date, and — in Texas — an
    estimate where the price should be."""
    return make_listing(
        zpid,
        address=address or f"{zpid} Walsh Ave, Aledo, TX 76008",
        price=price,
        zestimate=estimate,
        sqft=sqft,
        home_type=home_type,
        listing_status="sold",
        status_text="Sold",
        date_sold=date_sold,
    )


def _record(sessions, listings, watch=SOLD_WATCH, ts=NOW):
    with sessions() as s:
        for listing in listings:
            upsert_property(s, listing, ts)
        s.flush()
        for listing in listings:
            record_snapshot(s, listing, watch, ts, distance_miles=0.5)
        s.commit()


def _baseline(sessions, **kwargs):
    with sessions() as s:
        return compute_sold_baseline(s, SOLD_WATCH, NOW, **kwargs)


def test_a_non_disclosure_market_is_labelled_a_proxy(sessions):
    """Texas: not one of these sales published a price, so the surface is built on the
    post-sale estimate and every number derived from it must say so."""
    _record(
        sessions,
        [
            _sold("a", 2000, estimate=400_000),  # $200/sqft
            _sold("b", 2000, estimate=420_000),  # $210
            _sold("c", 2000, estimate=440_000),  # $220
        ],
    )
    baseline = _baseline(sessions)

    assert baseline.basis == "proxy" and baseline.is_proxy
    assert baseline.n_solds == 3 and baseline.price_disclosed == 0
    assert baseline.segments["all"].p50 == 210


def test_a_disclosure_market_is_labelled_disclosed(sessions):
    _record(
        sessions,
        [
            _sold("a", 2000, price=400_000),
            _sold("b", 2000, price=420_000),
            _sold("c", 2000, estimate=440_000),  # one sale still hiding its price
        ],
    )
    baseline = _baseline(sessions)

    assert baseline.basis == "disclosed" and not baseline.is_proxy
    assert baseline.price_disclosed == 2 and baseline.n_solds == 3


def test_the_label_flips_on_the_share_of_real_prices(sessions):
    """Half is enough to call a market disclosed; below half it is mostly estimate."""
    _record(
        sessions,
        [_sold("a", 2000, price=400_000)]
        + [_sold(f"e{i}", 2000, estimate=400_000) for i in range(2)],
    )
    assert _baseline(sessions).basis == "proxy"  # 1 of 3

    _record(sessions, [_sold("b", 2000, price=400_000)])
    assert _baseline(sessions).basis == "disclosed"  # 2 of 4


def test_a_real_price_is_preferred_over_the_estimate(sessions):
    _record(sessions, [_sold("a", 2000, price=420_000, estimate=999_999)])
    baseline = _baseline(sessions)

    assert baseline.segments["all"].p50 == 210  # the price, not the estimate
    assert baseline.price_disclosed == 1


# -- what is not a comp -----------------------------------------------------------------


def test_builder_plan_sheets_are_not_sales(sessions):
    """"Jasmine Plan, Walsh" is a page from a price list. Counting it as a sale would put
    a number nobody has paid into the surface that decides what is worth paying."""
    _record(
        sessions,
        [
            _sold("real", 2000, estimate=420_000),
            _sold("plan", 2000, estimate=1_000_000, address="Jasmine Plan, Walsh"),
        ],
    )
    baseline = _baseline(sessions)

    assert baseline.n_solds == 1
    assert baseline.segments["all"].p50 == 210


def test_land_is_a_different_asset(sessions):
    _record(
        sessions,
        [
            _sold("home", 2000, estimate=420_000),
            _sold("lot", 2000, estimate=100_000, home_type="LOT"),
            _sold("acreage", 2000, estimate=90_000, home_type="LAND"),
        ],
    )
    assert _baseline(sessions).n_solds == 1


def test_a_sale_without_a_size_cannot_price_a_square_foot(sessions):
    _record(sessions, [_sold("sized", 2000, estimate=420_000), _sold("blank", None, estimate=500_000)])
    assert _baseline(sessions).n_solds == 1


def test_the_window_trims_sales_that_describe_another_market(sessions):
    _record(
        sessions,
        [
            _sold("recent", 2000, estimate=420_000, date_sold="2026-06-01"),
            _sold("ancient", 2000, estimate=300_000, date_sold="2020-01-01"),
        ],
    )
    baseline = _baseline(sessions, window_months=12)

    assert baseline.n_solds == 1
    assert baseline.segments["all"].p50 == 210


def test_an_undated_sale_is_kept_rather_than_silently_dropped(sessions):
    """Dropping undated rows would shrink the thinnest comp sets to nothing, on exactly
    the markets that most need one."""
    _record(sessions, [_sold("undated", 2000, estimate=420_000, date_sold=None)])
    baseline = _baseline(sessions)

    assert baseline.n_solds == 1
    assert baseline.window_start is None and baseline.solds_per_month == 0.0


# -- segments and velocity ---------------------------------------------------------------


def test_segments_split_by_home_type(sessions):
    _record(
        sessions,
        [_sold(f"sf{i}", 2000, estimate=420_000) for i in range(3)]
        + [_sold("th", 1500, estimate=375_000, home_type="TOWNHOUSE")],  # $250/sqft
    )
    baseline = _baseline(sessions)

    assert baseline.segments["SINGLE_FAMILY"].n == 3
    assert baseline.segments["SINGLE_FAMILY"].p50 == 210
    assert baseline.segments["TOWNHOUSE"].p50 == 250
    assert baseline.segments["all"].n == 4


def test_a_thin_segment_falls_back_to_the_whole_market(sessions):
    """One townhouse is not a townhouse market. Its own median would be an anecdote with a
    statistic's face on it, so a caller asking for it gets the market instead."""
    _record(
        sessions,
        [_sold(f"sf{i}", 2000, estimate=420_000) for i in range(10)]
        + [_sold("th", 1500, estimate=600_000, home_type="TOWNHOUSE")],
    )
    baseline = _baseline(sessions)

    assert baseline.segments["TOWNHOUSE"].n == 1  # recorded
    assert baseline.segment_for("TOWNHOUSE").key == "all"  # but not used on its own
    assert baseline.segment_for("SINGLE_FAMILY").key == "SINGLE_FAMILY"
    assert baseline.segment_for("MANUFACTURED").key == "all"  # a type never seen at all


def test_velocity_is_measured_over_the_span_actually_observed(sessions):
    """Four sales across roughly two months is two a month — not four twelfths of one,
    which is what dividing by the configured window would say."""
    _record(
        sessions,
        [
            _sold("a", 2000, estimate=400_000, date_sold="2026-05-01"),
            _sold("b", 2000, estimate=400_000, date_sold="2026-05-20"),
            _sold("c", 2000, estimate=400_000, date_sold="2026-06-10"),
            _sold("d", 2000, estimate=400_000, date_sold="2026-07-01"),
        ],
    )
    baseline = _baseline(sessions)

    assert (baseline.window_start, baseline.window_end) == ("2026-05-01", "2026-07-01")
    assert baseline.solds_per_month == 2.0


def test_a_market_with_no_sales_has_no_segments_to_offer(sessions):
    baseline = _baseline(sessions)

    assert baseline.n_solds == 0 and baseline.segments == {}
    assert baseline.segment_for("SINGLE_FAMILY") is None  # not scored, never invented
    assert baseline.basis == "proxy"  # the cautious label, absent evidence otherwise


def test_active_listings_in_the_watch_are_not_sales(sessions):
    """A sold watch occasionally carries a row the feed has not marked sold. An asking
    price is not a sale price and must never enter the surface of what homes fetched."""
    _record(sessions, [_sold("closed", 2000, estimate=420_000)])
    _record(sessions, [make_listing("open", price=900_000, sqft=2000, listing_status="for_sale")])

    assert _baseline(sessions).n_solds == 1
