"""Fanning out across a watch's queries, and reconciling what comes back.

Three behaviours are load-bearing here: the radius overrules whichever query found a
home, a home returned twice is stored once at its nearest reported position, and a query
whose answers are all somewhere else entirely says so out loud.
"""
import copy
import logging

from conftest import RoutedSearchApi, load_fixture, one_page

from propertyfinder.config import Watch
from propertyfinder.sweep import collect_in_radius

CENTER = {"lat": 32.73665, "lon": -97.55626}  # 2112 Eastus Ln, Aledo, TX
ALEDO = "Aledo, TX 76008"
FORT_WORTH = "Fort Worth, TX 76126"

# The config validator now catches bare ZIPs, but it cannot catch a well-formed query
# that names the wrong place — and Aledo, Illinois is a real town eight hundred miles
# from Aledo, Texas. A watch can still be pointed at the wrong state by a plausible
# string, which is why the sweep keeps its own alarm.
WRONG_ALEDO = "Aledo, IL 61231"


def _watch(queries: list[str], radius_miles: float = 2.0) -> Watch:
    return Watch(
        name="walsh-aledo",
        center_address="2112 Eastus Ln, Aledo, TX 76008",
        radius_miles=radius_miles,
        listing_status="for_sale",
        queries=queries,
        **CENTER,
    )


def _moved(body: dict, zpid: int, lat: float, lon: float) -> dict:
    """The same response with one home reported at a different position."""
    out = copy.deepcopy(body)
    for row in out["properties"]:
        if row["zpid"] == zpid:
            row["latitude"], row["longitude"] = lat, lon
    return out


PAGE1 = one_page(load_fixture("search_for_sale_page1"))
PAGE2 = one_page(load_fixture("search_for_sale_page2"))
MINERVA = one_page(load_fixture("search_misresolved"))


def test_every_query_a_watch_names_is_asked(make_adapter):
    transport = RoutedSearchApi({ALEDO: PAGE1, FORT_WORTH: PAGE2})
    collect_in_radius(make_adapter(transport), _watch([ALEDO, FORT_WORTH]))
    assert transport.queries_asked == [ALEDO, FORT_WORTH]


def test_the_homes_that_come_back_are_the_ones_inside_the_circle(make_adapter):
    transport = RoutedSearchApi({ALEDO: PAGE1, FORT_WORTH: PAGE2})
    found = collect_in_radius(make_adapter(transport), _watch([ALEDO, FORT_WORTH]))

    # 88291043 is on the first page and has no coordinates at all: unplaceable is
    # outside, however plainly the address says Texas.
    assert set(found) == {"29584711", "2075294181", "29584799", "2064118820"}
    listing, distance = found["29584711"]
    assert listing.address.startswith("1420 Tolleson Dr") and distance < 1


def test_a_builder_plan_sheet_is_collected_like_any_other_row(make_adapter):
    """It is an ask-curve rather than a home, and later stages exclude it from comps —
    but geometry is not the place to decide that. The sweep collects what is in the
    circle; judging what a row *is* happens where the judgement can be explained."""
    transport = RoutedSearchApi({ALEDO: PAGE1})
    found = collect_in_radius(make_adapter(transport), _watch([ALEDO]))
    assert "Plan," in found["2075294181"][0].address


def test_a_home_two_queries_both_return_is_kept_once(make_adapter):
    """Overlapping place strings are the normal case: a circle spills across ZIP lines
    and every query covering it returns the homes in the middle."""
    transport = RoutedSearchApi({ALEDO: PAGE1, FORT_WORTH: PAGE1})
    found = collect_in_radius(make_adapter(transport), _watch([ALEDO, FORT_WORTH]))
    assert len(found) == 2  # not four
    assert sorted(found) == ["2075294181", "29584711"]


def test_the_copy_nearest_the_centre_wins(make_adapter):
    """The feed sometimes gives one home two positions. Whichever is right, the nearer
    one is what decided the home was in the circle, so it is the one kept."""
    far = _moved(PAGE1, 29584711, lat=32.7200, lon=-97.5800)  # still inside, further out
    transport = RoutedSearchApi({ALEDO: far, FORT_WORTH: PAGE1})

    near_first = collect_in_radius(make_adapter(transport), _watch([FORT_WORTH, ALEDO]))
    far_first = collect_in_radius(make_adapter(transport), _watch([ALEDO, FORT_WORTH]))

    for found in (near_first, far_first):
        listing, distance = found["29584711"]
        assert listing.lat == 32.741913 and distance < 0.5


def test_a_home_beyond_the_radius_is_dropped_however_it_was_found(make_adapter):
    """The radius is authoritative — the query that surfaced a home grants it nothing.
    Tightening the circle to a third of a mile keeps only the home closest to the
    centre, and drops the rest of the same query's answers with it."""
    transport = RoutedSearchApi({ALEDO: PAGE1, FORT_WORTH: PAGE2})
    tight = collect_in_radius(make_adapter(transport), _watch([ALEDO, FORT_WORTH], 0.35))
    assert set(tight) == {"2075294181"}  # 0.31 mi out; the next nearest is 0.43


def test_a_query_that_answers_with_the_wrong_town_warns_loudly(make_adapter, caplog):
    """The incident this warning exists for: the provider answered a Texas question
    with an Ohio town, and the sweep dutifully stored nothing while reporting perfect
    health. Full slate of listings, none of them anywhere near the centre."""
    transport = RoutedSearchApi({WRONG_ALEDO: MINERVA})
    with caplog.at_level(logging.WARNING):
        found = collect_in_radius(make_adapter(transport), _watch([WRONG_ALEDO]))

    assert found == {}
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    message = warnings[0].getMessage()
    assert "2 listings and NONE within" in message
    assert WRONG_ALEDO in message and "Minerva" in message


def test_a_genuinely_empty_market_does_not_cry_wolf(make_adapter, caplog):
    """Zero listings is zero listings. The warning means 'the wrong place answered',
    and a warning that fires on ordinary quiet weeks is a warning nobody reads."""
    transport = RoutedSearchApi({})
    with caplog.at_level(logging.WARNING):
        assert collect_in_radius(make_adapter(transport), _watch([ALEDO])) == {}
    assert [r for r in caplog.records if r.levelno == logging.WARNING] == []


def test_one_bad_query_does_not_stop_the_good_one(make_adapter, caplog):
    transport = RoutedSearchApi({ALEDO: PAGE1, WRONG_ALEDO: MINERVA})
    with caplog.at_level(logging.WARNING):
        found = collect_in_radius(make_adapter(transport), _watch([WRONG_ALEDO, ALEDO]))
    assert set(found) == {"29584711", "2075294181"}
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 1


def test_a_subdivision_watch_drops_in_radius_non_members_and_logs_the_count(
    make_adapter, caplog
):
    """1420 Tolleson Dr and the Jasmine Plan are Walsh; 612 Bearpaw Trl and the Kestrel
    Way townhouse are not — even though the radius alone would keep all four. Membership
    runs after geometry, per docs/EXPERT-PLAN.md."""
    transport = RoutedSearchApi({ALEDO: PAGE1, FORT_WORTH: PAGE2})
    watch = _watch([ALEDO, FORT_WORTH]).model_copy(update={"subdivision": "walsh"})

    with caplog.at_level(logging.INFO):
        found = collect_in_radius(make_adapter(transport), watch)

    assert set(found) == {"29584711", "2075294181"}
    messages = [r.getMessage() for r in caplog.records]
    assert any("2 in-radius listing(s) dropped by the walsh filter" in m for m in messages)


def test_a_watch_without_a_subdivision_is_unaffected_by_membership(make_adapter, caplog):
    """The same fixtures, no `subdivision` set: geometry alone decides, exactly as
    before this filter existed."""
    transport = RoutedSearchApi({ALEDO: PAGE1, FORT_WORTH: PAGE2})
    with caplog.at_level(logging.INFO):
        found = collect_in_radius(make_adapter(transport), _watch([ALEDO, FORT_WORTH]))

    assert set(found) == {"29584711", "2075294181", "29584799", "2064118820"}
    assert not any("dropped by the" in r.getMessage() for r in caplog.records)


def test_a_subdivision_with_nothing_to_drop_logs_nothing_extra(make_adapter, caplog):
    """Every in-radius listing is a member: the filter has nothing to report, and stays
    quiet rather than logging a zero nobody needs to see."""
    transport = RoutedSearchApi({ALEDO: PAGE1})
    watch = _watch([ALEDO]).model_copy(update={"subdivision": "walsh"})
    with caplog.at_level(logging.INFO):
        collect_in_radius(make_adapter(transport), watch)
    assert not any("dropped by the" in r.getMessage() for r in caplog.records)


def test_a_sold_watch_asks_the_provider_for_sold_homes(make_adapter):
    """The same seam carries the sold side unchanged — only the status asked for
    differs, which is what makes a sold companion watch a config entry and not code."""
    transport = RoutedSearchApi({ALEDO: PAGE1})
    watch = _watch([ALEDO])
    sold = watch.model_copy(update={"name": "walsh-aledo-sold", "listing_status": "sold"})
    collect_in_radius(make_adapter(transport), sold)
    assert transport.requests[0].url.params.get("listing_status") == "sold"
