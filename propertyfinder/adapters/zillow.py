"""SearchApi.io's Zillow engines, behind one seam.

Two engines, one client:

- `zillow`          — search and discovery: a place string plus filters, about forty
                      homes to a page, up to twenty-four pages.
- `zillow_property` — detail lookup by zpid: year built, lot, dues, tax rate, history.

The rules this module exists to enforce:

1. **Validate at the boundary.** An HTTP 200 whose body fails validation raises
   `SchemaDrift` and returns nothing. Half-parsed scraped data must never reach storage.
2. **The client is injected.** The adapter is handed an `httpx.Client`, which is the
   whole reason the test suite can run offline: the tests hand it a fake transport.
3. **Nothing downstream sees raw JSON.** Search results leave here as `Listing`, and
   nothing outside this package ever imports the provider's own models.
"""
from __future__ import annotations

import logging

import httpx
from pydantic import ValidationError

from propertyfinder.adapters.listing import Listing, to_listing
from propertyfinder.adapters.models import SearchResponse
from propertyfinder.config import Settings

log = logging.getLogger(__name__)

BASE_URL = "https://www.searchapi.io/api/v1/search"

# The provider stops paginating here; asking for more spends calls on empty pages.
MAX_PAGES = 24


class SchemaDrift(Exception):
    """An HTTP 200 whose body no longer means what we thought it meant.

    Raised instead of persisting a partial row. The feed is scraped, so this is a
    normal event over a long enough horizon — it should be loud, not fatal-silent.
    """


class ZillowHTTPError(Exception):
    """The provider refused, failed, or answered with an error document."""


class PropertyDetail:
    """A `zillow_property` body, kept raw on purpose.

    The detail engine returns a deep, inconsistent document whose useful facts hide at
    different depths per property. Rather than model all of it, we keep the body and
    read it by path; the enrichment layer decides which paths matter.
    """

    def __init__(self, zpid: str, raw: dict):
        self.zpid = zpid
        self.raw = raw

    def get(self, *path: str, default=None):
        cur = self.raw
        for key in path:
            if not isinstance(cur, dict) or key not in cur:
                return default
            cur = cur[key]
        return cur

    def __repr__(self) -> str:  # pragma: no cover - debugging affordance
        return f"PropertyDetail(zpid={self.zpid!r}, keys={sorted(self.raw)})"


class ZillowAdapter:
    """The client. Give it a key and an httpx client; it gives back validated data."""

    def __init__(self, api_key: str, client: httpx.Client | None = None):
        if not api_key:
            raise ValueError(
                "no SearchApi key: set SEARCHAPI_API_KEY in .env before sweeping"
            )
        self._api_key = api_key
        self._client = client or httpx.Client(timeout=90)

    @classmethod
    def from_settings(
        cls, settings: Settings, client: httpx.Client | None = None
    ) -> "ZillowAdapter":
        """Build from the environment. The key lives in Settings and nowhere else."""
        return cls(settings.searchapi_api_key, client=client)

    # -- transport ----------------------------------------------------------------------

    def _request(self, params: dict) -> dict:
        resp = self._client.get(BASE_URL, params={"api_key": self._api_key, **params})
        if resp.status_code != 200:
            raise ZillowHTTPError(f"HTTP {resp.status_code}: {resp.text[:300]}")
        return resp.json()

    # -- the search engine --------------------------------------------------------------

    def search_page(
        self,
        query: str,
        listing_status: str = "for_sale",
        page: int = 1,
        extra: dict | None = None,
    ) -> tuple[list[Listing], dict]:
        """One page of results. Returns (listings, pagination block)."""
        body = self._request(
            {
                "engine": "zillow",
                "q": query,
                "listing_status": listing_status,
                "page": page,
                **(extra or {}),
            }
        )
        try:
            parsed = SearchResponse.model_validate(body)
        except ValidationError as exc:
            log.error("zillow search drifted (q=%r page=%s): %s", query, page, exc)
            raise SchemaDrift(f"zillow search failed validation: {exc}") from exc
        if parsed.error:
            # A 200 carrying an error document is a provider failure, not drift. Returning
            # zero homes here would read downstream as "an empty market", which is a lie.
            raise ZillowHTTPError(f"zillow search returned an error: {parsed.error}")
        # A row with no zpid has no identity and cannot be stored, diffed or found again.
        listings = [x for x in (to_listing(r, listing_status) for r in parsed.properties) if x]
        return listings, parsed.pagination

    def search(
        self,
        query: str,
        listing_status: str = "for_sale",
        max_pages: int = 10,
        extra: dict | None = None,
    ) -> list[Listing]:
        """Page through a query, stopping at the caller's ceiling or the feed's end."""
        results, pagination = self.search_page(query, listing_status, 1, extra)
        total = min(int(pagination.get("total_pages") or 1), max_pages, MAX_PAGES)
        for page in range(2, total + 1):
            more, _ = self.search_page(query, listing_status, page, extra)
            if not more:
                break  # the feed ran dry early; further pages would only cost calls
            results.extend(more)
        return results

    # -- the detail engine --------------------------------------------------------------

    def property(self, zpid: str) -> PropertyDetail:
        """Detail lookup for one home. One call each, so callers must budget it."""
        body = self._request({"engine": "zillow_property", "zpid": str(zpid)})
        if "property" not in body and "building" not in body:
            raise SchemaDrift(
                f"zillow_property {zpid}: body has neither 'property' nor 'building' "
                f"(keys: {sorted(body)[:8]})"
            )
        return PropertyDetail(zpid=str(zpid), raw=body)
