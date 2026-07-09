"""
schedule.py

Schedule Monitoring Agent -- the time-side counterpart to weather.py's Weather
Monitoring Agent. Detects when the traveller has overstayed the planned
departure time at their current stop (the classic case: the kids loved the
park and you stayed an extra 40 minutes) and, if so, works out what that costs
-- which of the still-to-visit stops would no longer physically fit if the
day continues as planned from here.

Pure time/geometry in this module -- no LLM. The "what happens if you keep
going" feasibility check is delegated to the SAME OR-Tools solver used for the
original plan (via rag.search.plan_itinerary in agents/graph.py's
check_schedule_node), so the answer is a real computed fact -- a place that's
genuinely closed by the time you'd reach it -- not an invented one. Same
principle already used everywhere else in this codebase for skip reasons.

Known v1 approximation, stated plainly: there's no check-in/check-out signal,
so "are they still at the stop" is inferred from GPS proximity (if given) or,
absent GPS, from the clock alone. A real deployment with continuous location
tracking could detect this more precisely.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import List, Optional

from engine.distance_matrix import haversine_km

# How many minutes past the planned departure time before this counts as an
# "overstay" rather than ordinary schedule slack (traffic, a slightly long
# checkout, etc.).
OVERSTAY_GRACE_MIN = 20

# How close (km) the traveller's GPS needs to be to the stop for the overstay
# to be treated as "confirmed still there". Without GPS we fall back to a
# time-only estimate.
NEAR_STOP_RADIUS_KM = 0.6


def _to_min(hhmm: str) -> int:
    h, m = map(int, hhmm.split(":"))
    return h * 60 + m


@dataclass
class Overstay:
    stop_id: str
    stop_name: str
    stop_lat: float
    stop_lng: float
    planned_departure: str        # "HH:MM"
    overstay_min: int
    remaining_stop_ids: List[str]  # stops after this one, not yet reached


def detect_overstay(
    stops: List[dict], now_min: int,
    current_lat: Optional[float] = None, current_lng: Optional[float] = None,
) -> Optional[Overstay]:
    """Is the traveller still (or plausibly still) at a stop well past when the
    plan said they'd leave? None if nothing's amiss, if the trip hasn't started,
    or if this is the last stop (nothing downstream to put at risk).

    "Current stop" is whichever already-started stop the traveller is actually
    closest to right now, when GPS is available -- NOT simply the last stop
    whose scheduled start time has passed. That distinction matters once the
    overstay is long enough that even a LATER stop's start time has technically
    elapsed too (e.g. 45 min late means the next stop's 12:04 start has also
    gone by) -- without GPS-first matching, a late-enough traveller would look
    like they're "at" whatever stop the schedule says is last-started, even
    though they're still physically at an earlier one. Falls back to the
    last-started stop only when no GPS is given (a known, fuzzier estimate)."""
    if not stops:
        return None

    started = [(i, s) for i, s in enumerate(stops) if _to_min(s["visit_starts"]) <= now_min]
    if not started:
        return None   # trip hasn't started yet

    if current_lat is not None and current_lng is not None:
        idx, current = min(
            started,
            key=lambda t: haversine_km((current_lat, current_lng), (t[1]["lat"], t[1]["lng"])),
        )
        if haversine_km((current_lat, current_lng), (current["lat"], current["lng"])) > NEAR_STOP_RADIUS_KM:
            return None   # not confidently at any already-started stop
    else:
        idx, current = started[-1]

    departure_min = _to_min(current["visit_ends"])
    overstay = now_min - departure_min
    if overstay <= OVERSTAY_GRACE_MIN:
        return None

    remaining = stops[idx + 1:]
    if not remaining:
        return None   # last stop of the day -- nothing left to protect

    return Overstay(
        stop_id=current.get("id") or current["name"],
        stop_name=current["name"],
        stop_lat=current["lat"], stop_lng=current["lng"],
        planned_departure=current["visit_ends"],
        overstay_min=overstay,
        remaining_stop_ids=[s.get("id") or s["name"] for s in remaining],
    )
