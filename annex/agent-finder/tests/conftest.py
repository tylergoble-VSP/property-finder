"""Offline fixtures for the annex suite. No test here touches the network (core's rule 10)."""
from __future__ import annotations

import httpx
import pytest

from propertyfinder.adapters.listing import Listing
from propertyfinder.config import Settings, build_engine
from propertyfinder.store import session_factory

from agentfinder import store


@pytest.fixture
def engine(tmp_path):
    """A throwaway database migrated by the annex runner — so every store test is also a
    test that core + annex migrations produce a working schema in one shared file."""
    eng = build_engine(Settings(_env_file=None, db_path=str(tmp_path / "af.db")))
    store.migrate(eng)
    return eng


@pytest.fixture
def sessions(engine):
    return session_factory(engine)


def make_listing(zpid="1001", price=2_500_000.0, **overrides) -> Listing:
    fields = {
        "address": f"{zpid} Indian Creek Dr, Fort Worth, TX 76107",
        "lat": 32.741913, "lon": -97.560241, "price": price, "beds": 5, "baths": 6,
        "sqft": 5400, "home_type": "SINGLE_FAMILY", "listing_status": "for_sale",
        "status_text": "House for sale", "days_on_zillow": 40,
        "link": f"https://www.zillow.com/homedetails/{zpid}_zpid/",
        "image_url": f"https://photos.zillowstatic.com/{zpid}.jpg",
    }
    fields.update(overrides)
    return Listing(zpid=zpid, **fields)


def organic(link, snippet, title=""):
    return {"link": link, "snippet": snippet, "title": title}


class FakeTrec(httpx.MockTransport):
    """A stand-in for data.texas.gov. Answers by licence number from a dict."""

    def __init__(self, by_num: dict[str, list[dict]]):
        self.by_num = by_num
        super().__init__(self._handle)

    def _handle(self, request):
        where = request.url.params.get("$where", "")
        for num, rows in self.by_num.items():
            if num in where:
                return httpx.Response(200, json=rows)
        return httpx.Response(200, json=[])


def trec_client(by_num) -> httpx.Client:
    return httpx.Client(transport=FakeTrec(by_num))
