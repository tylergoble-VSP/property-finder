"""The test suite's internet.

Nothing in this repository is allowed to make a real network call: a live call spends
quota that belongs to a household's monthly budget, and a suite that needs the internet
is not a suite, it is a bill. So the provider is replaced by golden JSON files under
`tests/fixtures/`, captured in the shape SearchApi.io actually returns, and served
through `httpx.MockTransport` by the same `engine` parameter the real service switches
on.

The fixtures are deliberately awkward, because the real feed is:

- `search_for_sale_page1` holds a plain resale, a **builder plan-sheet** row
  ("Jasmine Plan, Walsh" — an ask-curve, not a home), and a listing with **no
  coordinates at all**, which every geographic filter must treat as outside.
- `search_sold_page1` is Texas: two of three sold homes disclose **no price**, leaving
  only the post-sale estimate as a proxy — the fact that shapes the whole valuation layer.
- `property_detail_no_data` is the detail engine's ordinary failure: HTTP 200, status
  "Success", and nothing in it. Roughly one pull in five looks like this.
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from propertyfinder.adapters import ZillowAdapter
from propertyfinder.config import Settings

FIXTURES = Path(__file__).parent / "fixtures"

# (listing_status, page) -> fixture file. A page outside this table is served as an
# empty result set, which is how the real feed answers when you page past the end.
SEARCH_PAGES = {
    ("for_sale", 1): "search_for_sale_page1",
    ("for_sale", 2): "search_for_sale_page2",
    ("sold", 1): "search_sold_page1",
}

# zpid -> fixture file for the detail engine.
PROPERTY_DETAILS = {
    "29584711": "property_detail",
    "88291043": "property_detail_no_data",
}

EMPTY_PAGE = {"properties": [], "pagination": {"current_page": 99, "total_pages": 2}}


def load_fixture(name: str) -> dict:
    """Read one golden file by stem, e.g. load_fixture("search_sold_page1")."""
    return json.loads((FIXTURES / f"{name}.json").read_text())


class FakeSearchApi(httpx.MockTransport):
    """A stand-in for SearchApi.io that answers from disk and remembers being asked.

    It records every request, so a test can assert *how many* calls a routine spends
    and which pages it walked — quota discipline is a behaviour worth testing.
    """

    def __init__(
        self,
        pages: dict | None = None,
        details: dict | None = None,
        status_code: int = 200,
    ):
        self.pages = SEARCH_PAGES if pages is None else pages
        self.details = PROPERTY_DETAILS if details is None else details
        self.status_code = status_code
        self.requests: list[httpx.Request] = []
        super().__init__(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        params = request.url.params
        engine = params.get("engine")
        if self.status_code != 200:
            return httpx.Response(self.status_code, json={"error": "quota exceeded"})
        if engine == "zillow":
            key = (params.get("listing_status", "for_sale"), int(params.get("page", 1)))
            name = self.pages.get(key)
            return httpx.Response(200, json=load_fixture(name) if name else EMPTY_PAGE)
        if engine == "zillow_property":
            name = self.details.get(params.get("zpid"), "property_detail_no_data")
            return httpx.Response(200, json=load_fixture(name))
        raise AssertionError(f"a test asked for an unknown engine: {engine!r}")

    @property
    def pages_walked(self) -> list[int]:
        return [
            int(r.url.params.get("page", 1))
            for r in self.requests
            if r.url.params.get("engine") == "zillow"
        ]


@pytest.fixture
def fake_transport() -> FakeSearchApi:
    """The default internet: the golden fixtures, served by engine."""
    return FakeSearchApi()


@pytest.fixture
def make_adapter(fake_transport):
    """Build an adapter wired to a fake transport, with a key that is not a key."""

    def _make(transport: httpx.BaseTransport | None = None, **kwargs) -> ZillowAdapter:
        client = httpx.Client(transport=transport or fake_transport)
        settings = Settings(_env_file=None, searchapi_api_key="test-key")
        return ZillowAdapter(settings.searchapi_api_key, client=client, **kwargs)

    return _make
