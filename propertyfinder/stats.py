"""The hedonic model: what a home of this size and shape actually fetches here.

Dollars per square foot is the number everybody quotes and it is the wrong yardstick. On
the seed market's sold comps the size elasticity came out near 0.83 — a home 10% larger
sells for about 8% more, not 10% — so bigger homes systematically show a *lower* price per
foot whether or not they are cheap. Rank a market on raw price per foot and it hands you a
list of large houses and calls them bargains.

So: an ordinary least-squares fit of log price on log size, bedrooms, bathrooms and home
type, over homes that have actually sold. Logs on both sides because that is what makes
the size coefficient an elasticity and the residual a *percentage* miss rather than a
dollar one — a $40,000 gap means something different on a $400,000 home than on a
$1.4m one, and the model should not have to be told so.

What comes back per home is three things: an expected price, a band around it wide enough
to contain ordinary variation, and a standardised residual `z` saying how many
standard deviations the asking price sits below (or above) that expectation. z at −1 is
outside the market's own noise; that, not a low price per foot, is what "statistically
underpriced" means.

Two things sit on top of that fit, and both are corrections for what a size-and-shape
model structurally cannot see.

**Nearest-neighbour comps** (`knn_comps`) are the appraiser's method, kept deliberately
independent of the regression: the closest recent sales of similar size, by great-circle
distance. When the two methods disagree about a home, that disagreement is information,
and fusing them is `deals.py`'s job rather than this module's.

**The spatial adjustment** is the fix for the original tool's most embarrassing failure.
A global fit over a market containing a premium pocket and ordinary streets lands
somewhere between them, so it over-predicts every ordinary home and hands back a page of
"great deals" that are simply houses not on the golf course. The correction: the fit's own
residual at each sold home is, overwhelmingly, location — so smooth those residuals over
the map with a haversine nearest-neighbour average and add the local value back into the
expectation. A home is then judged against what its *own* streets fetch.

Two rules this module keeps. It fits on **sales**, never on asking prices — a market of
sellers agreeing with each other is not evidence. And where a state discloses no prices it
fits on the post-sale re-anchored estimate and records `basis="proxy"`, so nothing
downstream can forget which surface it is standing on. Too few sales to fit is not an
occasion for a weaker model: `fit` returns None, the caller says "not scored", and that
is the whole of it.

This is the first module to import the scientific stack. It stays quarantined here (and in
`deals.py`) on purpose: sweeping a market and reading a report must not require numpy.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import numpy as np
import pandas as pd
import statsmodels.api as sm
from sklearn.neighbors import NearestNeighbors

from propertyfinder.baseline import disclosure_basis

# The design. Everything here comes off the search feed, so the model is always available;
# lot size and age arrive later with the detail engine and join at Stage 8.
BASE_PREDICTORS = ["log_sqft", "beds", "baths", "townhouse"]

# Below this many complete sales an OLS fit is a curve drawn through noise. Twenty is not
# generous — it is the point at which four predictors stop being able to fit anything they
# like — and falling short of it produces None rather than a worse model.
MIN_COMPS = 20

KNN_K = 8  # how many neighbouring sales make a comp set
KNN_SQFT_BAND = 0.25  # and how far from the subject's size they may be
MIN_KNN_POOL = 4  # fewer candidates than this and there is no comp set worth quoting

# The location field: how many sales are needed before smoothing residuals over the map
# says anything, and how many neighbours each point averages over.
MIN_LOCATION_N = 25
LOCATION_K = 10
# Clamp on the adjustment, in log price (±0.35 ≈ ±42%). A pocket really can be worth that
# much; a single mis-geocoded sale should not be able to claim more.
LOCATION_CAP = 0.35

EARTH_MI = 3958.7613  # mean radius, for turning haversine radians into miles


def _price_of(row: dict, basis: str) -> float | None:
    """The sale price this row contributes, under the market's basis.

    In a disclosed market that is the real price. In a non-disclosure market the feed
    supplies none, and the post-sale estimate — revised after closing toward what the home
    fetched — is the only surface there is. An *asking* price never plays this role.
    """
    value = row.get("price") if basis == "disclosed" else row.get("zestimate")
    return float(value) if value else None


def prepare(rows: Iterable[dict], basis: str = "disclosed") -> list[dict]:
    """Rows the model can use, with their design columns attached.

    Dropped: builder plan-sheets (a price list, not a home), and anything missing the
    price, size, bedroom count or coordinates the design needs. Missing data is dropped
    rather than imputed — a filled-in bedroom count is a made-up sale.
    """
    out: list[dict] = []
    for row in rows:
        if "Plan," in (row.get("address") or ""):
            continue
        price = _price_of(row, basis)
        sqft = row.get("sqft")
        if not (price and sqft and row.get("beds") is not None and row.get("lat") is not None):
            continue
        out.append(
            {
                **row,
                "px": price,
                "log_sqft": float(np.log(sqft)),
                "beds": float(row["beds"]),
                "baths": float(row.get("baths") or 0.0),
                "townhouse": 1.0 if row.get("home_type") == "TOWNHOUSE" else 0.0,
            }
        )
    return out


@dataclass(frozen=True)
class Expectation:
    """One home, judged against the market's own fitted surface."""

    price: float  # the asking price being judged
    expected: float  # what a home like this fetches here
    lo: float  # the ~68% prediction band, low side
    hi: float  # and high side
    z: float  # standardised residual: negative means listed below expectation
    basis: str  # "disclosed" | "proxy" — inherited from the sales it was fitted on
    location_pct: float = 0.0  # what nearby sales add to, or take off, the size model
    location_comps: int = 0  # how many sales the location figure rests on

    @property
    def discount_pct(self) -> float:
        """How far below the expectation the ask sits, as a percentage of expectation.

        Positive means listed under. This is the human-readable twin of `z`: `z` says how
        unusual the gap is, this says how big it is in money.
        """
        return (self.expected - self.price) / self.expected * 100


@dataclass
class HedonicModel:
    """A fitted market surface, and everything needed to explain it to a reader."""

    result: object  # statsmodels RegressionResults
    predictors: list[str]
    sigma: float  # residual standard deviation, in log price — the market's own noise
    r2: float
    n: int
    basis: str
    # The location field: a haversine nearest-neighbour index over the sold comps, and the
    # fit's residual at each of them. Absent on a market too small or too tightly clustered
    # for the smoothing to mean anything, in which case the adjustment is simply zero.
    location_index: object = None
    location_residuals: object = None

    @classmethod
    def fit(
        cls,
        sold_rows: list[dict],
        basis: str | None = None,
        min_comps: int = MIN_COMPS,
    ) -> "HedonicModel | None":
        """Fit on closed sales. Returns None when there are too few to fit honestly.

        `basis` is detected from the sales themselves unless forced: a market where most
        sales published a price is fitted on prices, and one where they did not is fitted
        on the re-anchored estimate and says so for the rest of its life.
        """
        rows = list(sold_rows)
        if basis is None:
            usable = [r for r in rows if r.get("sqft")]
            basis = disclosure_basis(sum(1 for r in usable if r.get("price")), len(usable))

        homes = prepare(rows, basis)
        if len(homes) < min_comps:
            return None

        frame = pd.DataFrame(homes)
        # has_constant="add" forces the intercept even when a predictor is constant across
        # this comp set — a market with no townhouses in it would otherwise lose the
        # intercept along with the all-zero column, and every prediction with it.
        design = sm.add_constant(frame[BASE_PREDICTORS], has_constant="add")
        result = sm.OLS(np.log(frame["px"].to_numpy()), design).fit()
        model = cls(
            result=result,
            predictors=list(BASE_PREDICTORS),
            sigma=float(np.sqrt(result.scale)),
            r2=float(result.rsquared),
            n=len(homes),
            basis=basis,
        )
        model._build_location_field(homes)
        return model

    def _build_location_field(self, homes: list[dict]) -> None:
        """Index the fit's own residuals by where the sale happened.

        The residual is what size, bedrooms, bathrooms and type failed to explain — and in
        a residential market that is mostly *where the house is*. Averaging nearby
        residuals therefore recovers the local premium or discount without anyone having to
        draw neighbourhood boundaries, which is fortunate, because the feed does not know
        them either.
        """
        located = [h for h in homes if h.get("lat") is not None and h.get("lon") is not None]
        if len(located) < MIN_LOCATION_N:
            return
        frame = pd.DataFrame(located)
        design = sm.add_constant(frame[BASE_PREDICTORS], has_constant="add")[
            ["const"] + BASE_PREDICTORS
        ]
        predicted = np.asarray(self.result.predict(design))
        self.location_residuals = np.log(frame["px"].to_numpy()) - predicted
        self.location_index = NearestNeighbors(
            n_neighbors=min(LOCATION_K, len(located)), metric="haversine"
        ).fit(np.radians(frame[["lat", "lon"]].to_numpy()))

    def location_adjustment(self, lat: float | None, lon: float | None) -> tuple[float, int]:
        """(log-price adjustment, sales it rests on) for a point on the map.

        An inverse-distance-weighted mean of the nearest sales' residuals, so the house
        across the street counts for more than the one half a mile away, clamped either
        way. Zero when there is no location field, which is the correct answer rather than
        a missing one: with nothing known about the pocket, the global fit stands.
        """
        if self.location_index is None or lat is None or lon is None:
            return 0.0, 0
        distances, indices = self.location_index.kneighbors(np.radians([[lat, lon]]))
        miles = distances[0] * EARTH_MI
        weights = 1.0 / (miles + 0.05)  # a 0.05-mile floor, so a comp on the lot line
        #                                 dominates without dividing by zero
        adjustment = float(np.average(self.location_residuals[indices[0]], weights=weights))
        return max(-LOCATION_CAP, min(LOCATION_CAP, adjustment)), int(len(indices[0]))

    def log_expected(self, home: dict) -> float:
        """The fitted log price for one prepared row."""
        design = sm.add_constant(
            pd.DataFrame([{k: home[k] for k in self.predictors}]), has_constant="add"
        )[["const"] + self.predictors]
        return float(np.asarray(self.result.predict(design))[0])

    def expected(self, home: dict, adjust: bool = True) -> Expectation | None:
        """What this home should fetch, the band around it, and how far off the ask is.

        The home is judged on its **asking** price — that is the question, after all — and
        returns None when the feed never gave enough of it to ask. The band is one residual
        standard deviation either side, which is roughly the middle two-thirds of how much
        homes here vary from the surface.

        `adjust` folds in what nearby sales say about this home's pocket, and defaults to
        on because leaving it off is how the original produced pages of false bargains.
        Pass `adjust=False` for the deliberately location-blind number — useful for showing
        a reader precisely how much the neighbourhood is worth here.
        """
        prepared = prepare([home], basis="disclosed")  # an active listing's ask is `price`
        if not prepared:
            return None
        row = prepared[0]

        log_expected = self.log_expected(row)
        location, comps = (
            self.location_adjustment(row.get("lat"), row.get("lon")) if adjust else (0.0, 0)
        )
        log_expected += location
        return Expectation(
            price=row["px"],
            expected=float(np.exp(log_expected)),
            lo=float(np.exp(log_expected - self.sigma)),
            hi=float(np.exp(log_expected + self.sigma)),
            z=float((np.log(row["px"]) - log_expected) / self.sigma),
            basis=self.basis,
            location_pct=float(np.expm1(location) * 100) if comps else 0.0,
            location_comps=comps,
        )

    def coefficients(self) -> dict[str, float]:
        return {str(k): float(v) for k, v in self.result.params.items()}

    def plain_english(self) -> list[str]:
        """The fit, in sentences a reader who has never met a regression can check."""
        coef = self.coefficients()
        elasticity = coef.get("log_sqft", float("nan"))
        lines = [
            f"Square footage elasticity {elasticity:.2f} — a home 10% larger sells for about "
            f"{((1.1 ** elasticity) - 1) * 100:.0f}% more, which is why dollars per square "
            f"foot makes big homes look cheap.",
        ]
        if "baths" in coef:
            lines.append(f"Each bathroom is worth about {coef['baths'] * 100:+.0f}%.")
        lines.append(
            f"The fit explains {self.r2 * 100:.0f}% of the variation across {self.n} sales, "
            f"with typical scatter of ±{(np.exp(self.sigma) - 1) * 100:.0f}%."
        )
        if self.location_index is not None:
            lines.append(
                "What nearby sales fetch is added on top, so a home on ordinary streets is "
                "not mistaken for a bargain against a market that includes a premium pocket."
            )
        lines.append(
            "Fitted on real disclosed sale prices."
            if self.basis == "disclosed"
            else "Fitted on post-sale re-anchored estimates — this state discloses no sale "
            "prices, so every figure derived from this model carries that caveat."
        )
        return lines


@dataclass(frozen=True)
class Comp:
    """One nearby sale, as a reader would want it quoted back."""

    zpid: str
    address: str | None
    distance_mi: float
    price: float
    sqft: float
    ppsf: float
    lat: float | None
    lon: float | None


def knn_comps(
    home: dict,
    sold_rows: list[dict],
    k: int = KNN_K,
    sqft_band: float = KNN_SQFT_BAND,
    basis: str = "disclosed",
) -> tuple[float | None, list[Comp]]:
    """(local dollars per square foot, the sales behind it) — the appraiser's method.

    Deliberately not the regression: nearest sales within a size band, ranked by
    great-circle distance and nothing else. It sees micro-location the global fit cannot,
    and it is wrong in different ways, which is exactly what makes agreement between the
    two worth something.

    Returns (None, []) when the band holds too few sales to quote. A median of two
    neighbours is not a comp set; it is two houses.
    """
    subject = prepare([home], basis="disclosed")
    if not subject:
        return None, []
    subj = subject[0]
    if subj.get("lon") is None:
        return None, []

    pool = [
        r
        for r in prepare(sold_rows, basis)
        if r.get("lon") is not None
        and abs(r["sqft"] - subj["sqft"]) <= sqft_band * subj["sqft"]
    ]
    if len(pool) < MIN_KNN_POOL:
        return None, []

    index = NearestNeighbors(n_neighbors=min(k, len(pool)), metric="haversine").fit(
        np.radians([[r["lat"], r["lon"]] for r in pool])
    )
    distances, indices = index.kneighbors(np.radians([[subj["lat"], subj["lon"]]]))

    comps = [
        Comp(
            zpid=pool[int(i)].get("zpid"),
            address=pool[int(i)].get("address"),
            distance_mi=float(d) * EARTH_MI,
            price=pool[int(i)]["px"],
            sqft=pool[int(i)]["sqft"],
            ppsf=pool[int(i)]["px"] / pool[int(i)]["sqft"],
            lat=pool[int(i)].get("lat"),
            lon=pool[int(i)].get("lon"),
        )
        for d, i in zip(distances[0], indices[0])
    ]
    return float(np.median([c.ppsf for c in comps])), comps
