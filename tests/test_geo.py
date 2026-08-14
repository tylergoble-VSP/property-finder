"""The circle, and the one row that must never be let into it.

Real coordinates, from the watch this tool was built for: the centre is 2112 Eastus Ln
in Aledo, Texas, and the far point is downtown Fort Worth about thirteen miles east.
"""
from propertyfinder.geo import haversine_miles, within_radius

CENTER_LAT, CENTER_LON = 32.73665, -97.55626  # 2112 Eastus Ln, Aledo, TX
DOWNTOWN_FORT_WORTH = (32.7555, -97.3308)
A_HOME_NEARBY = (32.741913, -97.560241)  # 1420 Tolleson Dr, a few streets over


def test_a_known_distance_comes_out_right():
    miles = haversine_miles(CENTER_LAT, CENTER_LON, *DOWNTOWN_FORT_WORTH)
    assert 12 < miles < 15


def test_a_point_is_no_distance_from_itself():
    assert haversine_miles(CENTER_LAT, CENTER_LON, CENTER_LAT, CENTER_LON) == 0


def test_distance_does_not_care_which_point_you_start_from():
    there = haversine_miles(CENTER_LAT, CENTER_LON, *DOWNTOWN_FORT_WORTH)
    back = haversine_miles(*DOWNTOWN_FORT_WORTH, CENTER_LAT, CENTER_LON)
    assert there == back


def test_a_home_a_few_streets_away_is_inside_a_two_mile_watch():
    inside, distance = within_radius(*A_HOME_NEARBY, CENTER_LAT, CENTER_LON, 2.0)
    assert inside is True and distance < 1


def test_a_home_thirteen_miles_out_is_outside_and_says_how_far():
    inside, distance = within_radius(*DOWNTOWN_FORT_WORTH, CENTER_LAT, CENTER_LON, 5.0)
    assert inside is False and distance > 5


def test_the_boundary_belongs_to_the_circle():
    """A radius of exactly N miles includes a home exactly N miles out. Arbitrary, but
    decided once and here, rather than differently in each caller."""
    edge = haversine_miles(CENTER_LAT, CENTER_LON, *DOWNTOWN_FORT_WORTH)
    inside, _ = within_radius(*DOWNTOWN_FORT_WORTH, CENTER_LAT, CENTER_LON, edge)
    just_outside, _ = within_radius(
        *DOWNTOWN_FORT_WORTH, CENTER_LAT, CENTER_LON, edge - 0.0001
    )
    assert inside is True and just_outside is False


def test_a_home_with_no_coordinates_is_outside_however_large_the_radius():
    """The rule with teeth. The feed drops coordinates on a share of rows, and a home
    admitted on faith would join the comp set of a neighbourhood it may not be in."""
    assert within_radius(None, None, CENTER_LAT, CENTER_LON, 2.0) == (False, None)
    assert within_radius(None, None, CENTER_LAT, CENTER_LON, 10_000) == (False, None)
    assert within_radius(32.74, None, CENTER_LAT, CENTER_LON, 2.0) == (False, None)
    assert within_radius(None, -97.56, CENTER_LAT, CENTER_LON, 2.0) == (False, None)


def test_a_coordinate_of_zero_is_a_real_place_and_is_treated_as_one():
    """Null island, in the Gulf of Guinea. It is *outside* because it is six thousand
    miles away, not because it looks like missing data — which is exactly why the seam
    keeps absence as None instead of filling it in with a zero."""
    inside, distance = within_radius(0.0, 0.0, CENTER_LAT, CENTER_LON, 2.0)
    assert inside is False and distance > 6_000
