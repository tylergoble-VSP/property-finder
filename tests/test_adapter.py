"""The adapter must parse what the feed sends, and refuse what it cannot trust.

Every response here comes from `tests/fixtures/` through a fake transport: no test in
this suite touches the network or spends a call of quota. Bodies that are *supposed* to
be broken are golden files deliberately damaged in-process, so the damage is visible in
the test that depends on it.
"""
import httpx
import pytest

from conftest import FakeSearchApi, load_fixture

from propertyfinder.adapters import SchemaDrift, ZillowAdapter, ZillowHTTPError
from propertyfinder.config import Settings


def _serving(body, status: int = 200) -> httpx.MockTransport:
    return httpx.MockTransport(lambda request: httpx.Response(status, json=body))


# -- the happy path -------------------------------------------------------------------


def test_the_golden_page_parses_and_unfamiliar_fields_are_ignored(make_adapter):
    results, pagination = make_adapter().search_page("Aledo, TX 76008", "for_sale", 1)
    assert [r.zpid for r in results] == [29584711, 2075294181, 88291043]
    top = results[0]
    assert top.extracted_price == 674900 and top.sqft == 3012
    assert top.thumbnail.startswith("https://photos.zillowstatic.com/")
    assert pagination["total_pages"] == 2
    # broker_name, carousel_photos and listing_sub_type are in the fixture and simply
    # do not exist on the model — the feed may grow without breaking ingest.
    assert not hasattr(top, "broker_name")


def test_paging_walks_the_feed_to_its_end(fake_transport, make_adapter):
    results = make_adapter().search("Aledo, TX 76008", "for_sale", max_pages=10)
    assert len(results) == 5
    assert fake_transport.pages_walked == [1, 2]  # not a page more than the feed has


def test_the_callers_ceiling_wins_over_the_feed(fake_transport, make_adapter):
    results = make_adapter().search("Aledo, TX 76008", "for_sale", max_pages=1)
    assert len(results) == 3 and fake_transport.pages_walked == [1]


def test_paging_stops_when_a_page_comes_back_empty(make_adapter):
    transport = FakeSearchApi(pages={("for_sale", 1): "search_for_sale_page1"})
    results = make_adapter(transport).search("Aledo, TX 76008", max_pages=9)
    assert len(results) == 3
    assert transport.pages_walked == [1, 2]  # it stopped at the first dry page


def test_the_request_carries_query_status_and_key(fake_transport, make_adapter):
    make_adapter().search_page("Aledo, TX 76008", "sold", page=1)
    params = fake_transport.requests[-1].url.params
    assert params["engine"] == "zillow"
    assert params["q"] == "Aledo, TX 76008"
    assert params["listing_status"] == "sold"
    assert params["api_key"] == "test-key"


def test_extra_filters_are_passed_through(fake_transport, make_adapter):
    make_adapter().search_page("Aledo, TX 76008", extra={"home_type": "Houses"})
    assert fake_transport.requests[-1].url.params["home_type"] == "Houses"


# -- the awkward truths the fixtures encode -------------------------------------------


def test_a_listing_without_coordinates_stays_without_them(make_adapter):
    results, _ = make_adapter().search_page("Aledo, TX 76008")
    no_coords = next(r for r in results if r.zpid == 88291043)
    assert no_coords.latitude is None and no_coords.longitude is None


def test_the_builder_plan_sheet_arrives_as_an_ordinary_row(make_adapter):
    results, _ = make_adapter().search_page("Aledo, TX 76008")
    plan = next(r for r in results if "Plan," in (r.address or ""))
    assert plan.status_text == "New construction"
    # The seam does not judge: excluding ask-curves from comps is a later layer's job.
    assert plan.extracted_price == 589990


def test_sold_results_carry_undisclosed_prices(make_adapter):
    results = make_adapter().search("Aledo, TX 76008", "sold", max_pages=1)
    assert len(results) == 3
    undisclosed = [r for r in results if r.extracted_price is None]
    assert len(undisclosed) == 2  # Texas discloses nothing; only the estimate survives
    assert all(r.zestimate and r.date_sold for r in undisclosed)


def test_a_misresolved_query_parses_like_any_other(make_adapter):
    """A bare ZIP answered with Minerva, Ohio is valid JSON and wrong data.

    The adapter cannot know that, and must not pretend to: it parses, and the radius
    filter downstream is what throws Ohio away.
    """
    body = load_fixture("search_misresolved")
    results, _ = make_adapter(_serving(body)).search_page("76008")
    assert [r.state for r in results] == ["OH", "OH"]


# -- refusing what cannot be trusted --------------------------------------------------


def test_a_mangled_body_raises_schema_drift_and_yields_nothing(make_adapter):
    body = load_fixture("search_for_sale_page1")
    body["properties"] = "the feed now returns this as a string"
    with pytest.raises(SchemaDrift):
        make_adapter(_serving(body)).search_page("Aledo, TX 76008")


def test_a_field_that_changes_shape_raises_schema_drift(make_adapter):
    body = load_fixture("search_for_sale_page1")
    body["properties"][0]["sqft"] = {"value": 3012, "units": "sqft"}
    with pytest.raises(SchemaDrift):
        make_adapter(_serving(body)).search_page("Aledo, TX 76008")


def test_a_non_200_raises(make_adapter):
    with pytest.raises(ZillowHTTPError) as exc:
        make_adapter(FakeSearchApi(status_code=429)).search_page("Aledo, TX 76008")
    assert "429" in str(exc.value)


def test_a_200_carrying_an_error_document_is_not_an_empty_market(make_adapter):
    body = {"error": "Your search could not be completed", "properties": []}
    with pytest.raises(ZillowHTTPError):
        make_adapter(_serving(body)).search_page("Aledo, TX 76008")


# -- the detail engine ----------------------------------------------------------------


def test_property_detail_reads_by_path(make_adapter):
    detail = make_adapter().property("29584711")
    assert detail.zpid == "29584711"
    assert detail.get("property", "year_built") == 2021
    assert detail.get("property", "facts_and_features", "hoa_fee") == "$92 monthly"
    assert detail.get("property", "price_history")[0]["price"] == 674900
    assert detail.get("property", "not_a_field", default="n/a") == "n/a"


def test_a_detail_pull_with_no_data_is_refused_not_half_read(make_adapter):
    """One detail pull in five comes back successful and empty. It must not parse."""
    with pytest.raises(SchemaDrift):
        make_adapter().property("88291043")


# -- construction ---------------------------------------------------------------------


def test_from_settings_takes_the_key_from_the_environment(fake_transport):
    settings = Settings(_env_file=None, searchapi_api_key="from-settings")
    adapter = ZillowAdapter.from_settings(
        settings, client=httpx.Client(transport=fake_transport)
    )
    adapter.search_page("Aledo, TX 76008")
    assert fake_transport.requests[-1].url.params["api_key"] == "from-settings"


def test_an_adapter_without_a_key_refuses_to_exist():
    with pytest.raises(ValueError):
        ZillowAdapter("")
