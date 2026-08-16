"""The annex's own door to the network — SearchApi's zillow, google, and google_maps engines.

Core's `ZillowAdapter` speaks only Zillow; the annex needs two more engines (google for
attribution, google_maps for contact), which is exactly the "a second provider concern is a
contained project" case docs/REBUILD.md describes. So this is a separate, thin adapter — but
it obeys core's rules verbatim: the httpx client is injected (so tests run offline), every
call is counted against a `CallBudget` and refused before it is sent, and a validation
failure raises `SchemaDrift` rather than persisting half-parsed scraped data.

It reuses core's `SearchResult`/`to_listing` for the Listing conversion so the annex speaks
the same frozen record as everything downstream. The free luxury *extras* the search feed
carries (is_showcase, the photo array, builder name) are read straight off the raw row —
they are dropped by core's `SearchResult` (extra="ignore"), and they are the designer's
signal, so the annex keeps them alongside the Listing.
"""
from __future__ import annotations

import time
from collections.abc import Callable

import httpx
from pydantic import ValidationError

from propertyfinder.adapters.listing import Listing, to_listing
from propertyfinder.adapters.models import SearchResult
from propertyfinder.adapters.zillow import SchemaDrift, ZillowHTTPError
from propertyfinder.budget import CallBudget

BASE_URL = "https://www.searchapi.io/api/v1/search"
PER_CALL_DELAY_S = 0.35


class LuxeExtras(dict):
    """The free, conditionally-present luxury signals the search feed carries but core's
    Listing does not. A missing key means *unknown*, never False — the same backfill
    discipline core applies to identity."""


def extras_of(row: dict) -> LuxeExtras:
    """Pull the designer-relevant free fields off a raw search row.

    `listing_sub_type` is a bare string in the search engine and a dict of booleans in the
    detail engine (a real provider inconsistency); both shapes are read, and a third would
    simply leave is_fsba unknown rather than crash — this is a display flag, not a gate."""
    sub = row.get("listing_sub_type")
    if isinstance(sub, dict):
        is_fsba = bool(sub.get("is_FSBA")) if "is_FSBA" in sub else None
    elif isinstance(sub, str):
        is_fsba = sub.upper() == "FSBA"
    else:
        is_fsba = None
    images = row.get("images")
    return LuxeExtras(
        is_showcase=row.get("is_showcase"),
        is_fsba=is_fsba,
        builder_name=row.get("builder_name"),
        new_construction_type=row.get("new_construction_type"),
        has_3d_model=row.get("has_3d_model"),
        price_change=row.get("price_change"),
        price_reduction=row.get("price_reduction"),
        images=list(images) if isinstance(images, list) else None,
    )


class SearchApi:
    """One key, three engines, one budget. Nothing downstream sees raw JSON except the
    extras dict, which is deliberately loose because it is display-only signal."""

    def __init__(
        self,
        api_key: str,
        client: httpx.Client,
        budget: CallBudget | None = None,
        delay_s: float = PER_CALL_DELAY_S,
        sleep: Callable[[float], None] = time.sleep,
    ):
        if not api_key:
            raise ValueError("no SearchApi key: set SEARCHAPI_API_KEY in .env")
        self._api_key = api_key
        self._client = client
        self._budget = budget
        self._delay_s = delay_s
        self._sleep = sleep
        self.request_count = 0

    def _get(self, params: dict) -> dict:
        if self._budget is not None:
            self._budget.spend()  # refuse before sending, so an exceeded budget costs nothing
        self.request_count += 1
        resp = self._client.get(BASE_URL, params={"api_key": self._api_key, **params})
        if self._delay_s:
            self._sleep(self._delay_s)
        if resp.status_code != 200:
            raise ZillowHTTPError(f"HTTP {resp.status_code}: {resp.text[:200]}")
        return resp.json()

    def zillow_page(
        self, query: str, page: int = 1, sort_by: str = "price_desc"
    ) -> tuple[list[tuple[Listing, LuxeExtras]], dict]:
        """One page of for-sale listings, sorted (price_desc reaches the luxury tail first).

        Returns (rows, pagination) where each row is (Listing, extras). A row with no zpid
        has no identity and is dropped; a body carrying a provider error is raised, not
        silently returned as an empty market."""
        body = self._get(
            {"engine": "zillow", "q": query, "listing_status": "for_sale",
             "sort_by": sort_by, "page": page}
        )
        if body.get("error"):
            raise ZillowHTTPError(f"zillow error: {body['error']}")
        rows: list[tuple[Listing, LuxeExtras]] = []
        for raw in body.get("properties", []):
            try:
                result = SearchResult.model_validate(raw)
            except ValidationError as exc:
                raise SchemaDrift(f"zillow search drifted: {exc}") from exc
            listing = to_listing(result, "for_sale")
            if listing is not None:
                rows.append((listing, extras_of(raw)))
        return rows, body.get("pagination", {})

    def google(self, query: str) -> list[dict]:
        """Organic results for a query. The attribution engine — a Google index of the
        syndicated 'Listed by' block is where the listing agent's name actually lives."""
        return self._get({"engine": "google", "q": query}).get("organic_results", []) or []

    def google_maps(self, query: str) -> list[dict]:
        """Local business results — a brokerage's phone, website, office. Contact, not
        identity: used to reach an agent once the person or firm is known."""
        return self._get({"engine": "google_maps", "q": query}).get("local_results", []) or []
