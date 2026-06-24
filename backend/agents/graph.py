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
from rag.search import smart_search, plan_itinerary


def plan_trip_node(state: TripState) -> TripState:
    """Trip Planning Agent: runs search + the OR-Tools itinerary engine."""
    exclude_ids = set(state.get("exclude_ids", []) or [])

    candidates = smart_search(
        user_query    = state["query"],
        user_lat      = state["home_lat"],
        user_lng      = state["home_lng"],
        target_time   = state["trip_start"],
        target_day    = state.get("day"),
        trip_end_time = state["trip_end"],
        exclude_ids   = exclude_ids,
    )
    candidates = apply_weather_bias(candidates, state.get("prefer_indoor", False))

    result = plan_itinerary(
        candidates  = candidates,
        start_lat   = state["home_lat"],
        start_lng   = state["home_lng"],
        trip_start  = state["trip_start"],
        trip_end    = state["trip_end"],
    )

    state["stops"] = result["stops"]
    state["skipped"] = result["skipped"]
    state["used_distance_fallback"] = result["used_distance_fallback"]
    state["last_planned_at"] = datetime.now().isoformat(timespec="seconds")
    # Replanning clears any old warning -- it was about the plan that just changed.
    state["weather_warnings"] = []
    state["needs_replan"] = False
    return state


def check_weather_node(state: TripState) -> TripState:
    """Weather Monitoring Agent: checks the forecast against upcoming stops."""
    now_str = state.get("simulated_now")
    now = datetime.strptime(now_str, "%H:%M") if now_str else datetime.now()
    now_min = now.hour * 60 + now.minute

    upcoming = []
    for s in state.get("stops", []):
        arrive_h, arrive_m = map(int, s["arrive_at"].split(":"))
        if arrive_h * 60 + arrive_m >= now_min:
            upcoming.append({"name": s["name"], "lat": s["lat"], "lng": s["lng"], "arrive_at": s["arrive_at"]})

    if not upcoming:
        state["weather_warnings"] = []
        state["needs_replan"] = False
        state["weather_check_failed"] = False
        state["weather_check_error"] = None
        state["last_checked_at"] = datetime.now().isoformat(timespec="seconds")
        return state

    warnings, failed, error = check_weather_for_stops(upcoming, datetime.now())

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
