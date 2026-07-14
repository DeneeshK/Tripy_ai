"""
distance_matrix.py

Builds an N x N travel-time matrix (in minutes) for a list of
(lat, lng) points. Two strategies:

1. osrm_time_matrix()   -- real road travel time, via OSRM's Table
   service. One HTTP call returns the whole matrix at once, instead of
   one call per pair. This is what should back the planner so the
   schedule the planner produces matches the road route drawn on the
   map (in the old code these were two unrelated calculations).

2. haversine_time_matrix() -- straight-line distance / a flat assumed
   speed. Kept only as an explicit, clearly-labelled fallback for when
   OSRM can't be reached (offline dev, OSRM rate limit, etc.), not as
   the primary source of truth.

Note on the public OSRM demo server (router.project-osrm.org): it is a
free demo instance, not meant for production traffic. For the AWS
deployment, self-host OSRM in a small Docker container (the official
osrm-backend image) or use a paid provider -- don't point a real
deployed app at the public demo server.
"""

from __future__ import annotations
import math
import os
from typing import List, Tuple
import urllib.request
import json

Point = Tuple[float, float]  # (lat, lng)

AVG_FALLBACK_SPEED_KMH = 25.0
# Self-hosted by default (see docker-compose.yml's `osrm` service). Overridable
# via env for local dev without Docker; falls back to the public demo server
# only if OSRM_BASE_URL is unset, since that server has no uptime guarantee.
OSRM_BASE_URL = os.environ.get("OSRM_BASE_URL", "https://router.project-osrm.org")


def haversine_km(p1: Point, p2: Point) -> float:
    lat1, lon1 = p1
    lat2, lon2 = p2
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def haversine_time_matrix(points: List[Point], speed_kmh: float = AVG_FALLBACK_SPEED_KMH) -> List[List[float]]:
    n = len(points)
    matrix = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            km = haversine_km(points[i], points[j])
            matrix[i][j] = (km / speed_kmh) * 60.0  # minutes
    return matrix


def osrm_time_matrix(points: List[Point], base_url: str = OSRM_BASE_URL, timeout: float = 8.0) -> List[List[float]]:
    """
    Real road travel times in minutes, via OSRM's Table API.
    Raises on any network/parse failure -- callers should catch and
    fall back to haversine_time_matrix(), see time_matrix_with_fallback().
    """
    coord_str = ";".join(f"{lng:.6f},{lat:.6f}" for lat, lng in points)
    url = f"{base_url}/table/v1/driving/{coord_str}?annotations=duration"
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if data.get("code") != "Ok":
        raise RuntimeError(f"OSRM table request failed: {data.get('code')}")
    durations_sec = data["durations"]
    return [[(d / 60.0 if d is not None else float("inf")) for d in row] for row in durations_sec]


def time_matrix_with_fallback(points: List[Point], base_url: str = OSRM_BASE_URL):
    """
    Try real road times first; fall back to straight-line + flat speed
    if OSRM can't be reached. Returns (matrix, used_fallback: bool) so
    the caller can surface "estimated, not exact roads" to the user if
    needed.
    """
    try:
        return osrm_time_matrix(points, base_url=base_url), False
    except Exception:
        return haversine_time_matrix(points), True
