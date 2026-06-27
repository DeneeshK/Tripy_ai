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
    specific_food_place: Optional[str]
    meal_suggestions: Dict[str, list]

    weather_warnings: List[dict]
    needs_replan: bool
    weather_check_failed: bool
    weather_check_error: Optional[str]

    last_planned_at: Optional[str]
    last_checked_at: Optional[str]


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
