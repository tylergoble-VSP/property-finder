"""The seam: one frozen record that every other layer speaks.

Storage, geometry, valuation, the pages — none of them may see the provider's JSON or
its vocabulary. They see this. That is what makes a provider rename a one-file problem,
and what lets the rest of the codebase be tested with hand-built records instead of
recorded HTTP.

It is frozen because a listing is an *observation*: what the feed said about a home at
one moment. Correcting an observation in place would quietly destroy the history this
tool exists to keep.
"""
from __future__ import annotations

from dataclasses import dataclass

from propertyfinder.adapters.models import SearchResult

# The feed's status words, mapped onto the only three this tool uses. Pending and
# contingent homes are still observations of the for-sale side of the market; the
# nuance survives in status_text, which is displayed rather than switched on.
_STATUS = {
    "FOR_SALE": "for_sale",
    "PENDING": "for_sale",
    "CONTINGENT": "for_sale",
    "ACCEPTING_BACKUP_OFFERS": "for_sale",
    "FOR_RENT": "for_rent",
    "SOLD": "sold",
    "RECENTLY_SOLD": "sold",
}


@dataclass(frozen=True)
class Listing:
    """One home as observed in one sweep."""

    zpid: str
    address: str | None = None
    lat: float | None = None
    lon: float | None = None
    price: float | None = None
    beds: float | None = None
    baths: float | None = None
    sqft: float | None = None
    lot_sqft: float | None = None
    home_type: str | None = None
    listing_status: str | None = None   # for_sale | for_rent | sold — this tool's words
    status_text: str | None = None      # the feed's phrasing, for display only
    days_on_zillow: int | None = None
    zestimate: float | None = None
    rent_zestimate: float | None = None
    tax_assessed_value: float | None = None
    date_sold: str | None = None
    link: str | None = None
    image_url: str | None = None

    @property
    def price_per_sqft(self) -> float | None:
        if self.price and self.sqft:
            return self.price / self.sqft
        return None

    # There is deliberately no price-versus-Zestimate helper here. The estimate is a
    # displayed reference, never an input to a judgement, and the original tool proved
    # that a convenient placeholder becomes load-bearing before anyone notices.


def to_listing(result: SearchResult, listing_status: str | None = None) -> Listing | None:
    """Convert one validated search result. Returns None if it has no identity.

    `listing_status` is what the caller *asked the provider for*, which is more reliable
    than the row's own status word; absent that, the row's word is translated. Missing
    numbers stay missing — a coordinate of 0.0 is a real place in the Gulf of Guinea,
    so absence is never filled in with a zero.
    """
    if result.zpid is None:
        return None
    status = listing_status or _STATUS.get((result.home_status or "").upper())
    return Listing(
        zpid=str(result.zpid),
        address=result.address,
        lat=result.latitude,
        lon=result.longitude,
        price=result.extracted_price,
        beds=result.beds,
        baths=result.baths,
        sqft=result.sqft,
        lot_sqft=result.lot_sqft,
        home_type=result.home_type,
        listing_status=status,
        status_text=result.status_text,
        days_on_zillow=result.days_on_zillow,
        zestimate=result.zestimate,
        rent_zestimate=result.rent_zestimate,
        tax_assessed_value=result.tax_assessed_value,
        date_sold=result.date_sold,
        link=result.link,
        image_url=result.thumbnail,
    )
