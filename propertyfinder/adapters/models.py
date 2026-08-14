"""The wire shapes: what the provider's JSON is allowed to look like.

Zillow's listing data is scraped, so the feed adds and renames fields without notice.
Every model here therefore sets `extra="ignore"` — an unfamiliar field must never break
ingest. What must break ingest is a field we depend on arriving as the wrong *shape*,
because that is the difference between "Zillow shipped a new feature" and "we are about
to write nonsense into the database". The second case raises SchemaDrift at the boundary.

These models are the provider's vocabulary, not the tool's. Nothing downstream imports
them; they exist to be validated and then converted into the Listing seam.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class SearchResult(BaseModel):
    """One home as the `zillow` search engine reports it."""

    model_config = ConfigDict(extra="ignore")

    zpid: int | str | None = None
    address: str | None = None
    street: str | None = None
    city: str | None = None
    state: str | None = None
    zipcode: str | None = None
    home_type: str | None = None
    home_status: str | None = None
    price: str | None = None            # display string, e.g. "$500,000"
    extracted_price: float | None = None  # the numeric one — the only one we do maths on
    zestimate: float | None = None
    rent_zestimate: float | None = None
    tax_assessed_value: float | None = None
    beds: float | None = None
    baths: float | None = None
    sqft: float | None = None
    lot_sqft: float | None = None
    latitude: float | None = None
    longitude: float | None = None
    days_on_zillow: int | None = None
    status_text: str | None = None
    date_sold: str | None = None
    link: str | None = None
    thumbnail: str | None = None        # photos.zillowstatic.com listing photo


class SearchResponse(BaseModel):
    """One page of the `zillow` search engine."""

    model_config = ConfigDict(extra="ignore")

    properties: list[SearchResult] = []
    search_information: dict = {}
    pagination: dict = {}
    error: str | None = None
