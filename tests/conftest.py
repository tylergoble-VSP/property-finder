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

from propertyfinder.adapters import Listing, ZillowAdapter
from propertyfinder.config import Settings, build_engine

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


NO_LISTINGS = {"properties": [], "pagination": {"current_page": 1, "total_pages": 1}}


def one_page(body: dict, properties: list | None = None) -> dict:
    """A response body that claims to be the only page, optionally with new rows.

    Paging is the adapter's business and is tested there; a test about *fanning out
    across queries* should not also be walking pages, so this trims a fixture to one.
    """
    return {
        **body,
        "properties": body["properties"] if properties is None else properties,
        "pagination": {"current_page": 1, "total_pages": 1},
    }


class RoutedSearchApi(httpx.MockTransport):
    """A provider that answers by *query string* rather than by page.

    The default transport is keyed on listing status and page, which is what adapter
    tests need. A watch, though, asks several different place strings and has to
    reconcile the answers — so this one routes on `q`, and a query it does not recognise
    gets the empty answer the real feed returns for a place with nothing for sale.
    """

    def __init__(self, by_query: dict[str, dict]):
        self.by_query = by_query
        self.requests: list[httpx.Request] = []
        super().__init__(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        query = request.url.params.get("q")
        return httpx.Response(200, json=self.by_query.get(query, NO_LISTINGS))

    @property
    def queries_asked(self) -> list[str]:
        return [r.url.params.get("q") for r in self.requests]


@pytest.fixture
def fake_transport() -> FakeSearchApi:
    """The default internet: the golden fixtures, served by engine."""
    return FakeSearchApi()


class RecordingSleeper:
    """Stands in for the politeness delay: remembers the nap instead of taking it."""

    def __init__(self):
        self.naps: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.naps.append(seconds)


@pytest.fixture
def make_adapter(fake_transport):
    """Build an adapter wired to a fake transport, with a key that is not a key.

    The politeness delay is replaced by default — it is real behaviour worth having in
    production and pure waiting in a test — but any keyword argument may be overridden,
    including `sleep` and `budget`.
    """

    def _make(transport: httpx.BaseTransport | None = None, **kwargs) -> ZillowAdapter:
        kwargs.setdefault("sleep", RecordingSleeper())
        client = httpx.Client(transport=transport or fake_transport)
        settings = Settings(_env_file=None, searchapi_api_key="test-key")
        return ZillowAdapter(settings.searchapi_api_key, client=client, **kwargs)

    return _make


# -- the database ---------------------------------------------------------------------
#
# A real SQLite file rather than an in-memory one, built by the same `build_engine` the
# tool uses in production. That matters more than it looks: foreign-key enforcement is a
# per-connection pragma, and a test on an engine without it would prove that orphan rows
# are rejected while production quietly accepted them.


@pytest.fixture
def engine(tmp_path):
    """A throwaway database with the production pragmas switched on.

    Built by the migration runner rather than by `create_all`, so every store test is
    also, quietly, a test that the migrations produce a schema the tool can work in.
    """
    from propertyfinder.store import run_migrations

    eng = build_engine(Settings(_env_file=None, db_path=str(tmp_path / "finder.db")))
    run_migrations(eng)
    return eng


@pytest.fixture
def sessions(engine):
    """A session factory over that database."""
    from propertyfinder.store import session_factory

    return session_factory(engine)


def make_listing(zpid: str = "29584711", **overrides) -> Listing:
    """A plausible home, for tests about storage rather than about parsing.

    Defaults describe an ordinary Aledo resale near the watch centre; override the one
    field the test is actually about and leave the rest alone.
    """
    fields = {
        "address": f"{zpid} Tolleson Dr, Aledo, TX 76008",
        "lat": 32.741913,
        "lon": -97.560241,
        "price": 674900.0,
        "beds": 4,
        "baths": 3,
        "sqft": 3012,
        "home_type": "SINGLE_FAMILY",
        "listing_status": "for_sale",
        "status_text": "House for sale",
        "days_on_zillow": 27,
        "link": f"https://www.zillow.com/homedetails/{zpid}_zpid/",
    }
    fields.update(overrides)
    return Listing(zpid=zpid, **fields)
