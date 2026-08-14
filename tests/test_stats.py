"""The hedonic model, held to a surface whose answer is known in advance.

Synthetic sales are generated from an explicit price rule — a stated elasticity, a stated
bathroom premium — and the fit has to recover them. That is the only kind of test that can
tell a working regression from one that has learned the noise: on real data every fit
looks plausible.
"""
import math

import pytest

from propertyfinder.stats import (
    KNN_K,
    MIN_COMPS,
    HedonicModel,
    knn_comps,
    prepare,
)


def home(zpid, price, sqft=2400, beds=4, baths=3, home_type="SINGLE_FAMILY",
         lat=32.74, lon=-97.57, estimate=None, address=None, **extra):
    return {
        "zpid": zpid,
        "address": address or f"{zpid} Walsh Ave",
        "price": price,
        "zestimate": estimate,
        "sqft": sqft,
        "beds": beds,
        "baths": baths,
        "home_type": home_type,
        "lat": lat,
        "lon": lon,
        **extra,
    }


def priced(sqft, baths=3.0, elasticity=0.83, bath_premium=0.04):
    """The surface the synthetic market obeys: $200 a foot at 2,400 square feet, sized by
    a stated elasticity, plus a stated premium per bathroom."""
    base = 200 * 2400
    return base * (sqft / 2400) ** elasticity * (1 + bath_premium) ** (baths - 3.0)


def synthetic_market(n=60, disclosed=True, noise=0.02):
    """Sales on that surface, with a deterministic wobble so the fit has residual variance
    to estimate but no randomness to make the test flaky.

    `noise` is the amplitude of that wobble, and it matters more than it looks: it *is*
    the market's own scatter, and the scatter is what "unusually cheap" is measured
    against. A market that varies by 2% treats a 10% discount as an extreme, correctly.
    Tests about the fit itself use the near-noiseless default; tests about how a price
    reads to a buyer pass something like a real market's 13%.
    """
    rows = []
    for i in range(n):
        sqft = 1800 + (i % 20) * 120  # 1,800 to 4,080 square feet
        baths = 2.0 + (i % 3)
        price = priced(sqft, baths) * (1 + noise * math.sin(i))
        rows.append(
            home(
                f"s{i}",
                price if disclosed else None,
                sqft=sqft,
                baths=baths,
                estimate=None if disclosed else price,
                lat=32.74 + (i % 7) * 0.002,
                lon=-97.57 + (i % 5) * 0.002,
            )
        )
    return rows


# -- what goes into the fit --------------------------------------------------------------


def test_prepare_drops_what_it_cannot_model_and_derives_what_it_can():
    rows = [
        home("keep", 500_000, sqft=2500),
        home("plan", 1, sqft=2000, address="Jasmine Plan, Walsh"),  # a price list
        home("priceless", None, sqft=2500),
        home("sizeless", 500_000, sqft=None),
        home("placeless", 500_000, lat=None),
    ]
    kept = prepare(rows)

    assert [r["zpid"] for r in kept] == ["keep"]
    assert kept[0]["log_sqft"] == pytest.approx(math.log(2500))
    assert kept[0]["townhouse"] == 0.0


def test_prepare_reads_the_estimate_only_where_the_market_discloses_nothing():
    sold = home("s", None, estimate=420_000)
    assert prepare([sold], basis="disclosed") == []
    assert prepare([sold], basis="proxy")[0]["px"] == 420_000


# -- the fit ------------------------------------------------------------------------------


def test_the_fit_recovers_the_coefficients_it_was_generated_from():
    model = HedonicModel.fit(synthetic_market())

    assert model.r2 > 0.95
    assert model.n == 60 and model.basis == "disclosed"
    # the whole reason this module exists: below 1.0, so price per foot falls with size
    assert model.coefficients()["log_sqft"] == pytest.approx(0.83, abs=0.05)
    assert model.coefficients()["baths"] == pytest.approx(math.log(1.04), abs=0.02)


def test_size_elasticity_below_one_is_why_dollars_per_foot_misleads():
    """Two homes on the surface, both priced exactly right. The larger one shows a lower
    price per square foot — a raw ranking would call it the better buy, and it is not."""
    model = HedonicModel.fit(synthetic_market())
    small = model.expected(home("small", priced(2000), sqft=2000))
    large = model.expected(home("large", priced(4000), sqft=4000))

    assert abs(small.z) < 0.5 and abs(large.z) < 0.5  # both fair, per the model
    assert priced(4000) / 4000 < priced(2000) / 2000  # yet the big one looks cheaper


def test_a_home_priced_under_the_surface_reads_as_underpriced():
    model = HedonicModel.fit(synthetic_market())
    fair = priced(3000)
    cheap = model.expected(home("cheap", fair * 0.80, sqft=3000))

    assert cheap.expected == pytest.approx(fair, rel=0.05)
    assert cheap.lo < cheap.expected < cheap.hi
    assert cheap.z < -1  # outside the market's own noise band
    assert cheap.discount_pct == pytest.approx(20, abs=2)
    assert cheap.price == fair * 0.80


def test_a_home_priced_over_the_surface_reads_the_other_way():
    model = HedonicModel.fit(synthetic_market())
    rich = model.expected(home("rich", priced(3000) * 1.25, sqft=3000))

    assert rich.z > 1 and rich.discount_pct < 0


def test_the_band_is_the_markets_own_scatter_not_a_fixed_percentage():
    model = HedonicModel.fit(synthetic_market())
    exp = model.expected(home("x", priced(3000), sqft=3000))

    assert exp.lo == pytest.approx(exp.expected / math.exp(model.sigma))
    assert exp.hi == pytest.approx(exp.expected * math.exp(model.sigma))


# -- degrading rather than guessing --------------------------------------------------------


def test_too_few_sales_returns_none_rather_than_a_worse_model():
    assert HedonicModel.fit(synthetic_market(n=MIN_COMPS - 1)) is None
    assert HedonicModel.fit([]) is None


def test_exactly_the_minimum_fits():
    assert HedonicModel.fit(synthetic_market(n=MIN_COMPS)) is not None


def test_a_home_the_feed_barely_described_cannot_be_scored():
    model = HedonicModel.fit(synthetic_market())

    assert model.expected(home("nosize", 500_000, sqft=None)) is None
    assert model.expected(home("noprice", None)) is None
    assert model.expected(home("plan", 500_000, address="Jasmine Plan, Walsh")) is None


# -- the basis label travels ---------------------------------------------------------------


def test_a_non_disclosure_market_fits_on_the_estimate_and_says_so():
    """Texas: no sale published a price, so the fit stands on the re-anchored post-sale
    estimate — and every expectation it produces carries that label."""
    model = HedonicModel.fit(synthetic_market(disclosed=False))

    assert model.basis == "proxy" and model.n == 60
    assert model.expected(home("x", priced(3000), sqft=3000)).basis == "proxy"
    assert any("discloses no sale prices" in line for line in model.plain_english())


def test_the_ask_is_never_what_the_model_learns_from():
    """A sold row in a proxy market carries no price at all, so a model that accidentally
    fitted on asking prices would have nothing to fit on and return None. It does not —
    and it also does not quietly pick up the asking prices of active listings mixed in."""
    solds = synthetic_market(disclosed=False)
    for row in solds:
        assert row["price"] is None
    assert HedonicModel.fit(solds) is not None


def test_plain_english_explains_the_fit_without_jargon():
    lines = HedonicModel.fit(synthetic_market()).plain_english()
    joined = " ".join(lines)

    assert "elasticity" in joined and "10% larger" in joined
    assert "60 sales" in joined
    assert "disclosed sale prices" in joined


# -- nearest-neighbour comps --------------------------------------------------------------


def test_knn_returns_a_local_rate_and_the_sales_behind_it():
    solds = synthetic_market()
    ppsf, comps = knn_comps(home("x", 600_000, sqft=3000), solds)

    assert ppsf is not None and 150 < ppsf < 220
    assert 4 <= len(comps) <= KNN_K
    assert all(c.distance_mi >= 0 for c in comps)
    assert all(abs(c.sqft - 3000) <= 0.25 * 3000 for c in comps)  # inside the size band


def test_a_home_with_no_comparable_sizes_gets_no_comp_set():
    """A 9,000-square-foot house in a market of 2,000-foot houses has no comps, and saying
    so is the answer. Widening the band until something turns up is how a comp set stops
    being comparable."""
    ppsf, comps = knn_comps(home("mansion", 3_000_000, sqft=9000), synthetic_market())
    assert (ppsf, comps) == (None, [])


def test_comps_read_the_estimate_where_the_market_discloses_nothing():
    ppsf, comps = knn_comps(
        home("x", 600_000, sqft=3000), synthetic_market(disclosed=False), basis="proxy"
    )
    assert ppsf is not None and comps


def test_comps_are_ordered_nearest_first():
    solds = synthetic_market()
    _ppsf, comps = knn_comps(home("x", 600_000, sqft=3000), solds)
    assert [c.distance_mi for c in comps] == sorted(c.distance_mi for c in comps)


# -- the spatial adjustment: the false-bargain regression ----------------------------------


def two_pocket_market():
    """Forty ordinary sales spread over a wide area at the market rate, and twenty in one
    tight pocket at 40% below it. A single global fit lands between the two, so it
    over-predicts every ordinary home — which is precisely how the original tool produced
    a page of "great deals" that were only houses outside the premium pocket."""
    rows = []
    for i in range(40):
        sqft = 2000 + (i % 10) * 150
        rows.append(
            home(f"n{i}", 280 * sqft, sqft=sqft,
                 lat=27.40 + (i % 8) * 0.01, lon=-82.40 + (i % 5) * 0.01)
        )
    for i in range(20):
        sqft = 2000 + (i % 10) * 150
        rows.append(
            home(f"c{i}", 140 * sqft, sqft=sqft,
                 lat=27.60 + (i % 4) * 0.001, lon=-82.20 + (i % 4) * 0.001)
        )
    return rows


def test_the_location_field_separates_the_pockets():
    model = HedonicModel.fit(two_pocket_market())
    assert model.location_index is not None

    cheap, n = model.location_adjustment(27.60, -82.20)
    ordinary, _ = model.location_adjustment(27.40, -82.40)

    assert n > 0
    assert cheap < -0.15  # this pocket sells well below the blended fit
    assert ordinary - cheap > 0.25  # and the two are unmistakably different places


def test_an_ordinary_home_at_its_own_pockets_rate_stops_reading_as_a_bargain():
    """The regression test for the whole feature. Without location, a home priced exactly
    right for where it stands looks underpriced against a market that contains a pricier
    pocket; with it, the home is judged against its own streets."""
    model = HedonicModel.fit(two_pocket_market())
    subject = home("x", 140 * 2400, sqft=2400, lat=27.60, lon=-82.20)  # fair, for its pocket

    blind = model.expected(subject, adjust=False)
    located = model.expected(subject)

    assert blind.z < -0.8  # a false bargain
    assert located.z > blind.z + 0.7  # pulled back toward fair
    assert abs(located.z) < abs(blind.z)
    assert located.location_pct < -10 and located.location_comps > 0
    assert located.expected < blind.expected  # the pocket is worth less, and it says so


def test_a_home_in_the_premium_pocket_is_not_called_overpriced_for_being_there():
    """The same correction, the other way round: a home priced at the going rate on the
    better streets should read as fair, not as a home asking too much."""
    model = HedonicModel.fit(two_pocket_market())
    subject = home("y", 280 * 2400, sqft=2400, lat=27.40, lon=-82.40)

    assert model.expected(subject, adjust=False).z > 0.3  # looks dear against the blend
    assert abs(model.expected(subject).z) < 0.3  # fair, against its own streets


def test_a_market_too_small_to_locate_anything_simply_does_not_adjust():
    """No location field is a fine state to be in — the global fit stands, unmodified, and
    nothing is invented about a pocket nobody has data for."""
    model = HedonicModel.fit(synthetic_market(n=MIN_COMPS), min_comps=MIN_COMPS)
    small = HedonicModel(
        result=model.result, predictors=model.predictors, sigma=model.sigma,
        r2=model.r2, n=model.n, basis=model.basis,
    )

    assert small.location_adjustment(32.74, -97.57) == (0.0, 0)
    exp = small.expected(home("x", priced(3000), sqft=3000))
    assert exp.location_pct == 0.0 and exp.location_comps == 0


def test_a_home_with_no_coordinates_is_judged_by_size_alone():
    model = HedonicModel.fit(two_pocket_market())
    assert model.location_adjustment(None, None) == (0.0, 0)
