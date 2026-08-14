"""Great-circle distance, and the filter that makes a watch mean something.

A watch is a centre point and a radius, and **the radius is authoritative**: whichever
query surfaced a listing, if it falls outside the circle it is discarded. That is what
keeps every later comparison honest — a median, a price-per-foot, a comp set are all
statements about *a place*, and a place defined by which search string happened to return
a home is not a place at all.

The rule that earns its own line: **a listing with no coordinates is outside.** Never
guessed in, never assumed near, never given the centre's position because it was found by
a query about the centre. The feed omits coordinates on a small share of rows, and a home
admitted on faith would sit in the comp set of a neighbourhood it may not be in.
"""
from __future__ import annotations

import math

# Mean radius in statute miles. Good to a few feet over the distances a watch spans.
EARTH_RADIUS_MI = 3958.7613


def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance in statute miles between two points on the globe."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return EARTH_RADIUS_MI * 2 * math.asin(min(1.0, math.sqrt(a)))


def within_radius(
    lat: float | None,
    lon: float | None,
    center_lat: float,
    center_lon: float,
    miles: float,
) -> tuple[bool, float | None]:
    """Is this home inside the watch, and how far from the centre is it?

    Returns `(inside, distance_miles)`. The distance comes back with the verdict because
    every caller wants both — the sweep keeps the nearest copy of a duplicated home, and
    the report shows how far out a listing sits — and computing the great circle twice to
    learn one number each time is silly.

    A home with no coordinates is `(False, None)`: unplaceable is outside.
    """
    if lat is None or lon is None:
        return False, None
    distance = haversine_miles(center_lat, center_lon, lat, lon)
    return distance <= miles, distance
