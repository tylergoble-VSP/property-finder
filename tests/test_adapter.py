"""The adapter must parse what the feed sends, and refuse what it cannot trust.

Every response here is constructed in-process and served through httpx.MockTransport:
no test in this suite is allowed to touch the network or spend a call of quota.
"""
import httpx
import pytest

from propertyfinder.adapters import SchemaDrift, ZillowAdapter, ZillowHTTPError
from propertyfinder.config import Settings

SEARCH_BODY = {
    "search_information": {"total_results": 2, "region": {"name": "Aledo, TX"}},
    "pagination": {"current_page": 1, "total_pages": 1},
    "properties": [
        {
            "zpid": 29584711,
            "address": "1420 Tolleson Dr, Aledo, TX 76008",
            "price": "$674,900",
            "extracted_price": 674900,
            "beds": 4,
            "baths": 3,
            "sqft": 3012,
            "latitude": 32.7419,
            "longitude": -97.5602,
            "home_type": "SINGLE_FAMILY",
            "home_status": "FOR_SALE",
            "a_field_zillow_shipped_last_tuesday": {"nested": True},
        },
        {"zpid": 29584712, "address": "2 Sample Ct, Aledo, TX 76008"},
    ],
}

DETAIL_BODY = {
    "property": {"zpid": 29584711, "year_built": 2021, "property_tax_rate": 2.34},
}


def _adapter(handler) -> ZillowAdapter:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    settings = Settings(_env_file=None, searchapi_api_key="test-key")
    return ZillowAdapter.from_settings(settings, client=client)


def _serving(body, status: int = 200) -> ZillowAdapter:
    return _adapter(lambda request: httpx.Response(status, json=body))


def test_a_good_body_parses_and_unknown_fields_are_ignored():
    results = _serving(SEARCH_BODY).search("Aledo, TX 76008", max_pages=1)
    assert [r.zpid for r in results] == [29584711, 29584712]
    assert results[0].extracted_price == 674900 and results[0].sqft == 3012
    assert results[1].beds is None  # absent stays absent; it is not invented


def test_a_mangled_body_raises_schema_drift_and_yields_nothing():
    with pytest.raises(SchemaDrift):
        _serving({"properties": "we changed this to a string"}).search_page("Aledo, TX")


def test_a_wrongly_typed_field_raises_schema_drift():
    body = {"properties": [{"zpid": 1, "sqft": {"value": 3012}}], "pagination": {}}
    with pytest.raises(SchemaDrift):
        _serving(body).search_page("Aledo, TX")


def test_non_200_raises():
    with pytest.raises(ZillowHTTPError) as exc:
        _serving({"error": "rate limited"}, status=429).search_page("Aledo, TX")
    assert "429" in str(exc.value)


def test_a_200_carrying_an_error_document_is_not_an_empty_market():
    with pytest.raises(ZillowHTTPError):
        _serving({"error": "Your search returned no results", "properties": []}).search_page(
            "Aledo, TX"
        )


def test_search_pages_until_the_feed_or_the_ceiling_stops_it():
    seen = []

    def handler(request):
        page = int(request.url.params.get("page", 1))
        seen.append(page)
        return httpx.Response(
            200,
            json={
                "pagination": {"current_page": page, "total_pages": 5},
                "properties": [{"zpid": 100 + page}],
            },
        )

    results = _adapter(handler).search("Aledo, TX 76008", max_pages=3)
    assert seen == [1, 2, 3]  # the ceiling wins over the feed's five pages
    assert [r.zpid for r in results] == [101, 102, 103]


def test_search_stops_early_when_a_page_comes_back_empty():
    def handler(request):
        page = int(request.url.params.get("page", 1))
        return httpx.Response(
            200,
            json={
                "pagination": {"total_pages": 9},
                "properties": [{"zpid": 100 + page}] if page < 3 else [],
            },
        )

    results = _adapter(handler).search("Aledo, TX 76008", max_pages=9)
    assert [r.zpid for r in results] == [101, 102]


def test_the_query_and_status_reach_the_provider():
    captured = {}

    def handler(request):
        captured.update(dict(request.url.params))
        return httpx.Response(200, json={"properties": [], "pagination": {}})

    _adapter(handler).search_page("Aledo, TX 76008", "sold", page=2)
    assert captured["engine"] == "zillow"
    assert captured["q"] == "Aledo, TX 76008"
    assert captured["listing_status"] == "sold"
    assert captured["page"] == "2"
    assert captured["api_key"] == "test-key"


def test_property_detail_reads_by_path():
    detail = _serving(DETAIL_BODY).property("29584711")
    assert detail.zpid == "29584711"
    assert detail.get("property", "year_built") == 2021
    assert detail.get("property", "nothing_here", default="n/a") == "n/a"


def test_property_detail_without_a_property_key_is_drift():
    with pytest.raises(SchemaDrift):
        _serving({"search_metadata": {"status": "Success"}}).property("29584711")


def test_an_adapter_without_a_key_refuses_to_exist():
    with pytest.raises(ValueError):
        ZillowAdapter("")
