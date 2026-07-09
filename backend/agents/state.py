"""
state.py

The shared state object the orchestrator graph reads and writes, plus a
trip store that holds it between requests (the user starts a trip, then the
monitoring agent gets invoked again minutes later against the same state).

PROTOTYPE LIMITATION, stated plainly: TripStore is a plain in-memory dict.
It works for one backend process during one run. It does NOT survive a
server restart, and won't work correctly if you ever run more than one
backend worker process (each would have its own separate copy). A real
deployment needs this backed by Redis or a database instead -- noted here
so it doesn't quietly become a production assumption.
"""

from __future__ import annotations
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, TypedDict


class TripState(TypedDict, total=False):
    trip_id: str
    mode: str  # "plan" | "monitor" -- which path the orchestrator should take

    query: str
    home_lat: float
    home_lng: float
    day: Optional[str]
    weekday_index: int
    trip_date: Optional[str]   # "YYYY-MM-DD" -- the actual day, for weather on the right date
    trip_start: str   # "HH:MM"
    trip_end: str      # "HH:MM"

    stops: List[dict]
    skipped: List[dict]
    used_distance_fallback: bool

    prefer_indoor: bool
    auto_replan: bool
    exclude_ids: List[str]
    current_lat: Optional[float]
    current_lng: Optional[float]
    simulated_now: Optional[str]  # "HH:MM" override, for testing without waiting on a real clock

    # Meal-aware planning. requested_meals e.g. ["breakfast","lunch"]; diet "veg"|"nonveg";
    # meal_selections maps a meal -> chosen restaurant id; meal_suggestions is the per-meal
    # list of suggestion cards the UI renders. specific_food_place = a restaurant the user
    # named explicitly (force-included). Declared here so LangGraph keeps them between nodes.
    requested_meals: List[str]
    diet: Optional[str]
    meal_selections: Dict[str, str]
    meal_times: Dict[str, str]          # meal -> "HH:MM" the user must eat at (e.g. medication)
    specific_food_place: Optional[str]
    meal_suggestions: Dict[str, list]

    # Named start / end. start_place overrides the GPS home (the journey begins
    # there); end_place is force-included as the LAST stop of the day ("from X to
    # Y"). end_place_id caches the resolved landmark id so a later remove/edit can
    # tell the destination apart from an ordinary stop.
    start_place: Optional[str]
    end_place: Optional[str]
    end_place_id: Optional[str]
    include_places: List[str]   # landmark names to force into the plan (anchors)

    # The LOCKED sightseeing route. Computed once and reused so adding/removing a
    # meal never reshuffles the day. reuse_base tells the planner to skip the
    # (non-deterministic) OR-Tools re-solve and just re-insert meals.
    base_stops: List[dict]
    base_skipped: List[dict]
    reuse_base: bool

    weather_warnings: List[dict]
    needs_replan: bool
    weather_check_failed: bool
    weather_check_error: Optional[str]

    # Schedule Monitoring Agent output: None if nothing's amiss, else
    # {stop_name, planned_departure, overstay_min, at_risk_stops: [{name, reason}]}
    # -- what continuing as-is would cost, computed by re-running the same
    # OR-Tools feasibility check against the not-yet-visited stops.
    schedule_warning: Optional[dict]

    last_planned_at: Optional[str]
    last_checked_at: Optional[str]

    # Agent trace: a running, capped log of what each of the three agents did
    # and why, e.g. {"agent": "Weather Monitoring Agent", "summary": "...",
    # "detail": [...], "at": iso timestamp}. Purely observational -- powers the
    # frontend's agent-activity panel, never read by the agents themselves.
    trace: List[dict]


class TripStore:
    def __init__(self):
        self._trips: Dict[str, TripState] = {}

    def create(self, state: TripState) -> str:
        trip_id = str(uuid.uuid4())[:8]
        state["trip_id"] = trip_id
        self._trips[trip_id] = state
        return trip_id

    def get(self, trip_id: str) -> Optional[TripState]:
        return self._trips.get(trip_id)

    def save(self, trip_id: str, state: TripState) -> None:
        self._trips[trip_id] = state


# Single process-wide instance. See the prototype-limitation note above.
trip_store = TripStore()
