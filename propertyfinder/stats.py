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

from propertyfinder.baseline import disclosure_basis

# The design. Everything here comes off the search feed, so the model is always available;
# lot size and age arrive later with the detail engine and join at Stage 8.
BASE_PREDICTORS = ["log_sqft", "beds", "baths", "townhouse"]

# Below this many complete sales an OLS fit is a curve drawn through noise. Twenty is not
# generous — it is the point at which four predictors stop being able to fit anything they
# like — and falling short of it produces None rather than a worse model.
MIN_COMPS = 20


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
        return cls(
            result=result,
            predictors=list(BASE_PREDICTORS),
            sigma=float(np.sqrt(result.scale)),
            r2=float(result.rsquared),
            n=len(homes),
            basis=basis,
        )

    def log_expected(self, home: dict) -> float:
        """The fitted log price for one prepared row."""
        design = sm.add_constant(
            pd.DataFrame([{k: home[k] for k in self.predictors}]), has_constant="add"
        )[["const"] + self.predictors]
        return float(np.asarray(self.result.predict(design))[0])

    def expected(self, home: dict) -> Expectation | None:
        """What this home should fetch, the band around it, and how far off the ask is.

        The home is judged on its **asking** price — that is the question, after all — and
        returns None when the feed never gave enough of it to ask. The band is one residual
        standard deviation either side, which is roughly the middle two-thirds of how much
        homes here vary from the surface.
        """
        prepared = prepare([home], basis="disclosed")  # an active listing's ask is `price`
        if not prepared:
            return None
        row = prepared[0]

        log_expected = self.log_expected(row)
        return Expectation(
            price=row["px"],
            expected=float(np.exp(log_expected)),
            lo=float(np.exp(log_expected - self.sigma)),
            hi=float(np.exp(log_expected + self.sigma)),
            z=float((np.log(row["px"]) - log_expected) / self.sigma),
            basis=self.basis,
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
        lines.append(
            "Fitted on real disclosed sale prices."
            if self.basis == "disclosed"
            else "Fitted on post-sale re-anchored estimates — this state discloses no sale "
            "prices, so every figure derived from this model carries that caveat."
        )
        return lines
