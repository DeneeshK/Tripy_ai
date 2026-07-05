"""
graph.py

The orchestrator. One LangGraph StateGraph coordinating two sub-agents:

  - plan_trip_node     (Trip Planning Agent) -- runs the existing
    search + itinerary engine (rag.search). Used both for the initial
    plan and for a replan.
  - check_weather_node (Weather Monitoring Agent) -- checks Open-Meteo
    against the trip's still-upcoming stops and decides whether a
    warning is warranted.

Routing: START branches on state["mode"] -- "plan" goes straight to the
planning agent, "monitor" goes to the weather agent. After the weather
agent runs, a conditional edge checks state["auto_replan"]: if true AND
a warning was raised, it loops back into the planning agent with
prefer_indoor set; otherwise it ends, leaving the decision to the user
(the actual default -- see api/main.py's /replan endpoint, which is what
fires when the person clicks "Replan" in the UI).
"""

from __future__ import annotations
import sys
from datetime import datetime
from pathlib import Path
from typing import Literal

from langgraph.graph import StateGraph, START, END

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.state import TripState
from agents.weather import check_weather_for_stops
from agents.categories import apply_weather_bias
from rag.search import (
    smart_search, plan_itinerary, get_food_places, build_meal_suggestions, WEEKDAYS,
    resolve_place_coords, get_place_record,
)
from engine.meals import (
    MEAL_ORDER, MEAL_LABELS, MEAL_DURATION_CAP_MIN, FOOD_CATEGORIES,
    resolve_meal_window, insert_meals_into_route,
)
from engine.hours import resolve_for_day, best_window_in_span
from engine.distance_matrix import time_matrix_with_fallback, haversine_km


def _weekday_index(day_name) -> int:
    try:
        return WEEKDAYS.index((day_name or "").capitalize())
    except ValueError:
        return datetime.now().weekday()


def _to_mins(t: str) -> int:
    h, m = map(int, t.split(":"))
    return h * 60 + m


def _resolve_meal_plan(state: TripState, weekday_index: int, ts_min: int, te_min: int):
    """Normalise the meal inputs. Returns (requested_meals, diet, selections,
    meal_times). Folds a user-named restaurant (`specific_food_place`) into the
    selections so it is always force-included."""
    requested_meals = list(state.get("requested_meals") or [])
    diet = state.get("diet")
    selections = dict(state.get("meal_selections") or {})
    meal_times = dict(state.get("meal_times") or {})

    named = (state.get("specific_food_place") or "").strip().lower()
    if named:
        match = next((p for p in get_food_places() if named in p["name"].lower()), None)
        if match and match["id"] not in selections.values():
            for meal in (requested_meals or MEAL_ORDER):
                if resolve_meal_window(meal, meal_times.get(meal), ts_min, te_min):
                    selections[meal] = match["id"]
                    if meal not in requested_meals:
                        requested_meals.append(meal)
                    break
    return requested_meals, diet, selections, meal_times


def _build_meal_specs(selections, meal_times, food_by_id, weekday_index, ts_min, te_min):
    """Turn the user's chosen restaurants into insertion specs, dropping (with a
    reason) any pick that can't actually be served at its meal time."""
    specs, skipped = [], []
    for meal, pid in selections.items():
        p = food_by_id.get(pid)
        if not p:
            continue
        win = resolve_meal_window(meal, meal_times.get(meal), ts_min, te_min)
        resolved = resolve_for_day(p["closed_on"], p["regular_hours"], p["special_hours"], weekday_index)
        if win is None or best_window_in_span(resolved, win["elig"][0], win["elig"][1]) is None:
            skipped.append({
                "order": None, "id": pid, "name": p["name"], "lat": p["lat"], "lng": p["lng"],
                "relevance": 1.0, "vibe": p.get("vibe", ""), "insight": p.get("insight", ""),
                "status": "Skipped", "is_meal": True, "meal": meal,
                "skipped_reason": f"{p['name']} can't be served at your {MEAL_LABELS.get(meal, meal).lower()} time today",
            })
            continue
        dur = min(int(round(float(p.get("avg_duration", 1.0)) * 60)), MEAL_DURATION_CAP_MIN)
        specs.append({"place": p, "meal": meal, "anchor": win["anchor"], "duration_min": dur})
    return specs, skipped


def _annotate_travel_legs(home, stops):
    """Attach the travel time (and rough distance) from the previous location to
    each stop, so the UI can draw a 'X min drive' connector between the itinerary
    cards. The first stop's 'previous' is the day's start (home / named start).
    One extra OSRM Table call over the FINAL ordered route (meals included), so
    the numbers match the map the user sees."""
    if not stops:
        return stops
    points = [home] + [(s["lat"], s["lng"]) for s in stops]
    matrix, _used = time_matrix_with_fallback(points)
    for i, s in enumerate(stops):
        leg = matrix[i][i + 1]
        s["travel_from_prev_min"] = (
            None if leg is None or leg == float("inf") else int(round(leg))
        )
        s["travel_from_prev_km"] = round(haversine_km(points[i], points[i + 1]), 1)
    return stops


def plan_trip_node(state: TripState) -> TripState:
    """Trip Planning Agent. Two phases so the sightseeing plan never moves when a
    meal is added:
      1. Plan the sightseeing route once with OR-Tools and LOCK it (cached in
         state['base_stops']). Re-used as-is when only meal picks change.
      2. Insert the chosen restaurants into that fixed route by time/location,
         shifting later stops a little but never changing the sightseeing order.
    """
    weekday_index = _weekday_index(state.get("day"))
    ts_min, te_min = _to_mins(state["trip_start"]), _to_mins(state["trip_end"])

    # A named START location overrides the GPS home -- the journey begins there
    # ("I want to travel FROM Vizhinjam..."). Falls back to GPS if unrecognised.
    start_name = (state.get("start_place") or "").strip()
    if start_name:
        coords = resolve_place_coords(start_name)
        if coords:
            state["home_lat"], state["home_lng"] = coords[0], coords[1]
    home = (state["home_lat"], state["home_lng"])

    # ── Phase 1: the locked sightseeing base ────────────────────────────────
    reuse = state.get("reuse_base") and state.get("base_stops") is not None
    if reuse:
        base_stops   = state["base_stops"]
        base_skipped = state.get("base_skipped", [])
    else:
        # A named END destination ("...to Napier Museum") is force-scheduled as
        # the day's final stop, with sightseeing optimised in between.
        end_name = (state.get("end_place") or "").strip()
        destination = None
        if end_name:
            destination = get_place_record(
                end_name, WEEKDAYS[weekday_index], state["trip_start"], state["trip_end"]
            )
        state["end_place_id"] = destination["id"] if destination else None

        candidates = smart_search(
            user_query=state["query"], user_lat=home[0], user_lng=home[1],
            target_time=state["trip_start"], target_day=state.get("day"),
            trip_end_time=state["trip_end"], exclude_ids=set(state.get("exclude_ids", []) or []),
        )
        candidates = apply_weather_bias(candidates, state.get("prefer_indoor", False))
        # Sightseeing only -- restaurants come exclusively through the meal flow.
        candidates = [c for c in candidates if c.get("category") not in FOOD_CATEGORIES]

        # Force-include any landmarks the user named ("include Kuthira Malika").
        # Already-present candidates are just flagged as anchors; new ones are
        # pulled in as anchor candidates so the solver keeps them in the route.
        by_id = {c["id"]: c for c in candidates}
        for nm in (state.get("include_places") or []):
            rec = get_place_record(nm, WEEKDAYS[weekday_index], state["trip_start"], state["trip_end"])
            if not rec or (destination and rec["id"] == destination["id"]):
                continue
            if rec["id"] in by_id:
                by_id[rec["id"]]["is_anchor"] = True
            else:
                rec = dict(rec, is_anchor=True)
                candidates.append(rec)
                by_id[rec["id"]] = rec

        base = plan_itinerary(
            candidates, home[0], home[1], state["trip_start"], state["trip_end"],
            destination=destination,
        )
        base_stops, base_skipped = base["stops"], base["skipped"]
        state["base_stops"] = base_stops
        state["base_skipped"] = base_skipped
        state["used_distance_fallback"] = base["used_distance_fallback"]

    # ── Phase 2: insert the chosen meals into that fixed route ───────────────
    requested_meals, diet, selections, meal_times = _resolve_meal_plan(state, weekday_index, ts_min, te_min)
    food_by_id = {p["id"]: p for p in get_food_places()}
    specs, meal_skipped = _build_meal_specs(selections, meal_times, food_by_id, weekday_index, ts_min, te_min)

    if specs:
        final_stops, _runs_late = insert_meals_into_route(home, base_stops, specs, ts_min)
    else:
        final_stops = base_stops

    final_stops = _annotate_travel_legs(home, final_stops)
    state["stops"] = final_stops
    state["skipped"] = list(base_skipped) + meal_skipped
    state["requested_meals"] = requested_meals
    state["meal_selections"] = selections
    state["meal_times"] = meal_times
    state["meal_suggestions"] = build_meal_suggestions(
        home=home, base_stops=base_stops, requested_meals=requested_meals, diet=diet,
        weekday_index=weekday_index, trip_start_min=ts_min, trip_end_min=te_min,
        selections=selections, meal_times=meal_times,
    ) if requested_meals else {}

    state["last_planned_at"] = datetime.now().isoformat(timespec="seconds")
    state["weather_warnings"] = []
    state["needs_replan"] = False
    state["reuse_base"] = False   # one-shot: a later replan must recompute the base
    return state


def check_weather_node(state: TripState) -> TripState:
    """Weather Monitoring Agent: checks the forecast against upcoming stops on the
    TRIP'S day (not today, when the trip is scheduled for a future date)."""
    # The reference day for the forecast is the planned trip date; only fall back
    # to 'now' if we somehow don't have one.
    trip_date_str = state.get("trip_date")
    try:
        ref_day = datetime.fromisoformat(trip_date_str) if trip_date_str else datetime.now()
    except ValueError:
        ref_day = datetime.now()
    is_today = ref_day.date() == datetime.now().date()

    now_str = state.get("simulated_now")
    now = datetime.strptime(now_str, "%H:%M") if now_str else datetime.now()
    now_min = now.hour * 60 + now.minute

    upcoming = []
    for s in state.get("stops", []):
        arrive_h, arrive_m = map(int, s["arrive_at"].split(":"))
        # For a future-dated trip nothing has been visited yet -> every stop is
        # upcoming. For a today trip, drop the ones already behind us.
        if not is_today or arrive_h * 60 + arrive_m >= now_min:
            upcoming.append({"name": s["name"], "lat": s["lat"], "lng": s["lng"], "arrive_at": s["arrive_at"]})

    if not upcoming:
        state["weather_warnings"] = []
        state["needs_replan"] = False
        state["weather_check_failed"] = False
        state["weather_check_error"] = None
        state["last_checked_at"] = datetime.now().isoformat(timespec="seconds")
        return state

    warnings, failed, error = check_weather_for_stops(upcoming, ref_day)

    state["weather_warnings"] = [
        {
            "stop_name": w.stop_name,
            "arrival_time": w.arrival_time,
            "description": w.description,
            "precipitation_probability": w.precipitation_probability,
            "is_thunderstorm": w.is_thunderstorm,
        }
        for w in warnings
    ]
    state["needs_replan"] = len(warnings) > 0
    state["weather_check_failed"] = failed
    state["weather_check_error"] = error
    state["last_checked_at"] = datetime.now().isoformat(timespec="seconds")
    # Set here, not in the router -- LangGraph conditional-edge functions are
    # meant to be pure reads; a mutation made inside one is silently dropped
    # rather than applied to the graph's actual state (found the hard way).
    if state.get("auto_replan") and state["needs_replan"]:
        state["prefer_indoor"] = True
    return state


def _route_from_start(state: TripState) -> Literal["plan_trip", "check_weather"]:
    return "check_weather" if state.get("mode") == "monitor" else "plan_trip"


def _route_after_weather(state: TripState) -> Literal["plan_trip", "__end__"]:
    if state.get("auto_replan") and state.get("needs_replan"):
        return "plan_trip"
    return END


def build_orchestrator():
    graph = StateGraph(TripState)
    graph.add_node("plan_trip", plan_trip_node)
    graph.add_node("check_weather", check_weather_node)

    graph.add_conditional_edges(START, _route_from_start, {"plan_trip": "plan_trip", "check_weather": "check_weather"})
    graph.add_edge("plan_trip", END)
    graph.add_conditional_edges("check_weather", _route_after_weather, {"plan_trip": "plan_trip", END: END})

    return graph.compile()


orchestrator = build_orchestrator()
