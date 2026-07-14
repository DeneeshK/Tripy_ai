"""
state.py

The shared state object the orchestrator graph reads and writes, plus a
trip store that holds it between requests (the user starts a trip, then the
monitoring agent gets invoked again minutes later against the same state).

TripStore is SQLite-backed (tripy_trips.db, alongside this file's package) so
a trip survives a backend restart -- it used to be a plain in-memory dict,
which meant editing a saved trip after a restart 404'd because the trip_id
the frontend held no longer existed anywhere. Still a single-file store, so
it won't scale past one machine; a real multi-instance deployment needs this
behind Postgres/Redis instead, but "survives a restart" was the actual gap.
"""

from __future__ import annotations
import json
import sqlite3
import uuid
from pathlib import Path
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

    # Parking-aware planning: when true, smart_search excludes candidates with
    # no OSM-mapped parking within ~250m (see rag/enrich_parking.py). Off by
    # default -- only applied when the user explicitly asks (chat phrasing or
    # the frontend's "Parking-friendly" toggle), never silently.
    requires_parking: bool

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

    # Agent trace: a running, capped log of what each of the four agents did
    # and why, e.g. {"agent": "Weather Monitoring Agent", "summary": "...",
    # "detail": [...], "at": iso timestamp}. Purely observational -- powers the
    # frontend's agent-activity panel, never read by the agents themselves.
    trace: List[dict]

    # Plan Critic Agent output: a list of {severity, issue, detail} findings
    # computed by re-checking the finished plan against the user's own stated
    # constraints (did a forced include/end actually land? is the day
    # unusually travel-heavy? did a requested meal get zero candidates?).
    # Every finding is a fact re-derived from state, not an LLM opinion -- see
    # critique_plan_node in graph.py. Read by api/main.py to force the chat
    # LLM to address high-severity findings instead of silently dropping them.
    plan_critique: List[dict]


DB_PATH = Path(__file__).resolve().parent.parent / "tripy_trips.db"


class TripStore:
    """A trip's state, JSON-serialized into a single SQLite table. Every
    TripState value is already plain JSON-safe data (str/int/float/bool/list/
    dict/None -- confirmed by inspection: timestamps are stored as
    .isoformat() strings, not datetime objects, everywhere they're written),
    so no custom encoder is needed.

    Opens a fresh connection per call rather than holding one open: sqlite3
    connections aren't thread-safe, and FastAPI can dispatch requests onto
    different threads, so sharing one risks a cross-thread access error. At
    this scale (per-user trip counts, not a high-throughput table) a
    short-lived connection per operation costs nothing meaningful.
    """

    def __init__(self, db_path: Path = DB_PATH):
        self._db_path = db_path
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS trips ("
                "  id TEXT PRIMARY KEY,"
                "  state TEXT NOT NULL,"
                "  updated_at TEXT NOT NULL DEFAULT (datetime('now'))"
                ")"
            )

    def create(self, state: TripState) -> str:
        trip_id = str(uuid.uuid4())[:8]
        state["trip_id"] = trip_id
        self.save(trip_id, state)
        return trip_id

    def get(self, trip_id: str) -> Optional[TripState]:
        with sqlite3.connect(self._db_path) as conn:
            row = conn.execute("SELECT state FROM trips WHERE id = ?", (trip_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def save(self, trip_id: str, state: TripState) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                "INSERT INTO trips (id, state, updated_at) VALUES (?, ?, datetime('now')) "
                "ON CONFLICT(id) DO UPDATE SET state = excluded.state, updated_at = excluded.updated_at",
                (trip_id, json.dumps(state)),
            )


# Single process-wide instance, safe across worker threads (see TripStore's
# docstring) but NOT across multiple separate backend processes -- each
# process would need to point at the same tripy_trips.db file, which works
# for SQLite up to modest concurrency but isn't a real multi-instance story.
trip_store = TripStore()
