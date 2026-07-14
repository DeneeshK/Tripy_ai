"""
graph.py

The orchestrator. One LangGraph StateGraph coordinating four sub-agents:

  - plan_trip_node      (Trip Planning Agent) -- runs the existing
    search + itinerary engine (rag.search). Used both for the initial
    plan and for a replan.
  - critique_plan_node  (Plan Critic Agent) -- re-derives the finished plan
    against the user's own stated constraints (did a must-include/end place
    actually land, did every requested meal get a candidate, is the day
    unusually travel-heavy or does it end with a lot of idle time). Always
    runs right after planning, on both the initial plan and any replan.
  - check_weather_node  (Weather Monitoring Agent) -- checks Open-Meteo
    against the trip's still-upcoming stops and decides whether a
    warning is warranted.
  - check_schedule_node (Schedule Monitoring Agent) -- checks whether the
    traveller has overstayed the planned departure time at their current
    stop and, if so, what continuing as-is would cost downstream.

Routing: START branches on state["mode"] -- "plan" goes to plan_trip then
critique_plan then ends, "monitor" runs check_weather then check_schedule.
After both monitors have run, a conditional edge checks state["auto_replan"]:
if true AND the WEATHER agent raised a warning, it loops back into
plan_trip (which flows through critique_plan again) with prefer_indoor set;
otherwise it ends, leaving the decision to the user (the actual default --
see api/main.py's /replan endpoint, which is what fires when the person
clicks "Replan" in the UI). The schedule agent never auto-replans --
overstaying is always surfaced as a choice, never acted on silently.
"""

from __future__ import annotations
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Literal

from langgraph.graph import StateGraph, START, END

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.state import TripState
from agents.weather import check_weather_for_stops
from agents.schedule import detect_overstay
from agents.categories import apply_weather_bias
from rag.search import (
    smart_search, plan_itinerary, get_food_places, build_meal_suggestions, WEEKDAYS,
    resolve_place_coords, get_place_record, get_places_by_ids,
)
from engine.meals import (
    MEAL_ORDER, MEAL_LABELS, MEAL_DURATION_CAP_MIN, FOOD_CATEGORIES,
    resolve_meal_window, insert_meals_into_route,
)
from engine.hours import resolve_for_day, best_window_in_span
from engine.distance_matrix import time_matrix_with_fallback, haversine_km


TRACE_CAP = 25  # keep the log bounded across a long-lived trip's many checks/replans


def _log_trace(state: TripState, agent: str, summary: str, detail: list | None = None) -> None:
    """Append one entry to state['trace'] -- the human-readable 'what did the
    agent just do and why' log the frontend's agent-activity panel renders.
    Purely observational: no node ever reads this back."""
    trace = list(state.get("trace") or [])
    trace.append({
        "agent": agent,
        "summary": summary,
        "detail": detail or [],
        "at": datetime.now().isoformat(timespec="seconds"),
    })
    state["trace"] = trace[-TRACE_CAP:]


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
            requires_parking=state.get("requires_parking", False),
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

    kept, skipped_n = len(final_stops), len(state["skipped"])
    summary = f"Solved the route with OR-Tools: kept {kept} stop{'s' if kept != 1 else ''}"
    if skipped_n:
        summary += f", skipped {skipped_n}"
    if specs:
        summary += f", wove in {len(specs)} meal stop{'s' if len(specs) != 1 else ''}"
    if state.get("requires_parking"):
        summary += " (parking-only filter applied)"
    _log_trace(
        state, "Trip Planning Agent", summary,
        detail=[{"name": s["name"], "reason": s.get("skipped_reason", "")} for s in state["skipped"]],
    )
    return state


def critique_plan_node(state: TripState) -> TripState:
    """Plan Critic Agent: re-checks the just-finished plan against the user's
    OWN stated constraints -- not a vibe check, a fact re-derivation. Every
    finding here is computed from data plan_trip_node already produced (who
    landed in stops vs skipped, real travel/visit minutes, whether a meal got
    any candidates), the same "real computed facts, not invented narrative"
    rule the other three agents follow. Deliberately NOT another LLM call:
    self-critique from the same model class tends to rubber-stamp its own
    output, so this is plain re-derivation from the ground-truth solver
    output instead -- only the phrasing shown to the user (in the chat reply)
    goes through the LLM, same as every other stop description.

    Findings feed api/main.py's tool_content so the chat LLM is handed them
    directly instead of having to notice on its own, and show up in the
    Agent Activity trace either way."""
    stops   = state.get("stops", [])
    skipped = state.get("skipped", [])
    stop_ids    = {s.get("id") for s in stops}
    skipped_by_id = {s.get("id"): s for s in skipped}
    findings: List[dict] = []

    def to_mins(t: str) -> int:
        h, m = map(int, t.split(":"))
        return h * 60 + m

    weekday_index = _weekday_index(state.get("day"))
    day_name = WEEKDAYS[weekday_index]

    # 1. Every explicitly must-include place -- did it actually land?
    for nm in (state.get("include_places") or []):
        rec = get_place_record(nm, day_name, state["trip_start"], state["trip_end"])
        if not rec:
            findings.append({
                "severity": "medium", "issue": "include_unresolved",
                "name": nm, "reason": f"Couldn't find a place matching \"{nm}\" to include.",
            })
        elif rec["id"] not in stop_ids:
            reason = skipped_by_id.get(rec["id"], {}).get("skipped_reason", "didn't fit in your window")
            findings.append({
                "severity": "high", "issue": "include_missing",
                "name": rec["name"], "reason": f"You asked to include {rec['name']} but it didn't make it in — {reason}",
            })

    # 2. The requested end destination -- did the day actually end there?
    end_id = state.get("end_place_id")
    if end_id and end_id not in stop_ids:
        reason = skipped_by_id.get(end_id, {}).get("skipped_reason", "couldn't be scheduled as your final stop")
        findings.append({
            "severity": "high", "issue": "end_missing",
            "name": state.get("end_place") or end_id, "reason": f"You asked to end at {state.get('end_place')} but it didn't make it in — {reason}",
        })
    elif state.get("end_place") and not end_id:
        findings.append({
            "severity": "medium", "issue": "end_unresolved",
            "name": state["end_place"], "reason": f"Couldn't find a place matching \"{state['end_place']}\" to end your day there.",
        })

    # 3. Every requested meal -- did it get at least one candidate?
    for meal in (state.get("requested_meals") or []):
        if meal in (state.get("meal_selections") or {}):
            continue  # already fulfilled
        if not (state.get("meal_suggestions") or {}).get(meal):
            findings.append({
                "severity": "medium", "issue": "meal_unfulfilled",
                "name": MEAL_LABELS.get(meal, meal),
                "reason": f"Couldn't find a {MEAL_LABELS.get(meal, meal).lower()} spot that fits your route, window, or diet.",
            })

    # 4. Pacing -- is the day unusually travel-heavy relative to its own window?
    if len(stops) >= 2:
        total_travel = sum(s.get("travel_from_prev_min") or 0 for s in stops)
        span = to_mins(state["trip_end"]) - to_mins(state["trip_start"])
        if span > 0 and total_travel / span > 0.35:
            findings.append({
                "severity": "low", "issue": "travel_heavy",
                "name": "Pacing",
                "reason": f"~{total_travel} of your {span} minute window ({round(100 * total_travel / span)}%) is spent travelling between stops -- they're fairly spread out.",
            })

    # 5. Idle time -- does the day end well before the requested trip_end?
    if stops:
        last_end = to_mins(stops[-1]["visit_ends"])
        idle = to_mins(state["trip_end"]) - last_end
        if idle > 90:
            findings.append({
                "severity": "low", "issue": "idle_time",
                "name": "Free time",
                "reason": f"Your day wraps up at {stops[-1]['visit_ends']}, {idle} min before your {state['trip_end']} end time -- there's room to add another stop.",
            })

    state["plan_critique"] = findings
    if findings:
        headline = ", ".join(f"{f['issue']}" for f in findings[:3])
        summary = f"Reviewed the plan against your requests -- {len(findings)} thing{'s' if len(findings) != 1 else ''} worth flagging ({headline})."
    else:
        summary = "Reviewed the plan against your requests -- everything you asked for made it in, pacing looks reasonable."
    _log_trace(state, "Plan Critic Agent", summary, detail=findings)
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
        _log_trace(state, "Weather Monitoring Agent", "No upcoming stops left to check.")
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

    day_label = ref_day.strftime("%d %b")
    if failed:
        _log_trace(state, "Weather Monitoring Agent", f"Forecast check failed: {error}")
    elif warnings:
        names = ", ".join(w.stop_name for w in warnings)
        _log_trace(
            state, "Weather Monitoring Agent",
            f"Checked {len(upcoming)} upcoming stop{'s' if len(upcoming) != 1 else ''} for {day_label} — flagged {len(warnings)}: {names}.",
            detail=[{"name": w.stop_name, "reason": f"{w.description} ({round(w.precipitation_probability)}% chance) at ~{w.arrival_time}"} for w in warnings],
        )
    else:
        _log_trace(state, "Weather Monitoring Agent", f"Checked {len(upcoming)} upcoming stop{'s' if len(upcoming) != 1 else ''} for {day_label} — no rain risk.")
    return state


def check_schedule_node(state: TripState) -> TripState:
    """Schedule Monitoring Agent: the time-side counterpart to the weather
    agent above. Has the traveller overstayed the planned departure time at
    their current stop (loved the place, stayed an extra 40 min)? If so, work
    out the real consequence of continuing as-is: re-run the SAME OR-Tools
    feasibility check the original plan used, forcing every not-yet-visited
    stop to stay in (is_anchor), starting from here at the current time --
    whatever the solver is forced to drop is a stop that genuinely no longer
    fits (e.g. it'll be closed by the time you'd arrive), not a guess.
    The user decides what to do with this (see /api/trip/{id}/replan for the
    "replan flexibly" option) -- this agent only ever informs, never auto-acts."""
    now_str = state.get("simulated_now")
    now = datetime.strptime(now_str, "%H:%M") if now_str else datetime.now()
    now_min = now.hour * 60 + now.minute

    overstay = detect_overstay(
        state.get("stops", []), now_min,
        state.get("current_lat"), state.get("current_lng"),
    )
    if overstay is None:
        state["schedule_warning"] = None
        _log_trace(state, "Schedule Monitoring Agent", "On schedule — no overstay detected.")
        return state

    weekday_index = _weekday_index(state.get("day"))
    day_name = WEEKDAYS[weekday_index]
    now_hhmm = f"{now.hour:02d}:{now.minute:02d}"
    trip_end = state["trip_end"]

    by_id = {(s.get("id") or s["name"]): s for s in state.get("stops", [])}
    records = get_places_by_ids(overstay.remaining_stop_ids, day_name, now_hhmm, trip_end)
    rec_by_id = {r["id"]: r for r in records}

    # Keep the trip's chosen end destination (if any of the remaining stops is
    # it) as a real forced END node in the preview too, not just an ordinary
    # anchor -- the "ends at X" promise should hold in the preview as well.
    destination, anchors = None, []
    for sid in overstay.remaining_stop_ids:
        rec = rec_by_id.get(sid)
        if not rec:
            continue
        if by_id.get(sid, {}).get("is_destination"):
            destination = rec
        else:
            anchors.append(dict(rec, is_anchor=True))

    preview = plan_itinerary(
        anchors, overstay.stop_lat, overstay.stop_lng, now_hhmm, trip_end, destination=destination,
    )

    at_risk = [{"name": s["name"], "reason": s["skipped_reason"]} for s in preview["skipped"]]
    state["schedule_warning"] = {
        "stop_name":         overstay.stop_name,
        "planned_departure": overstay.planned_departure,
        "overstay_min":      overstay.overstay_min,
        "at_risk_stops":     at_risk,
    }

    base = f"Overstayed {overstay.stop_name} by {overstay.overstay_min} min"
    if at_risk:
        _log_trace(
            state, "Schedule Monitoring Agent",
            f"{base} — re-ran the solver from here: {len(at_risk)} stop{'s' if len(at_risk) != 1 else ''} no longer fit.",
            detail=at_risk,
        )
    else:
        _log_trace(state, "Schedule Monitoring Agent", f"{base}, but the rest of the day still fits — re-solved and confirmed.")
    return state


def _route_from_start(state: TripState) -> Literal["plan_trip", "check_weather"]:
    return "check_weather" if state.get("mode") == "monitor" else "plan_trip"


def _route_after_monitor(state: TripState) -> Literal["plan_trip", "__end__"]:
    # Only weather ever auto-replans (and only when the caller opted in via
    # auto_replan); a schedule overstay always surfaces to the user instead --
    # skipping a place or extending time somewhere is their call to make, not
    # something the agent silently decides.
    if state.get("auto_replan") and state.get("needs_replan"):
        return "plan_trip"
    return END


def build_orchestrator():
    graph = StateGraph(TripState)
    graph.add_node("plan_trip", plan_trip_node)
    graph.add_node("critique_plan", critique_plan_node)
    graph.add_node("check_weather", check_weather_node)
    graph.add_node("check_schedule", check_schedule_node)

    graph.add_conditional_edges(START, _route_from_start, {"plan_trip": "plan_trip", "check_weather": "check_weather"})
    graph.add_edge("plan_trip", "critique_plan")
    graph.add_edge("critique_plan", END)
    graph.add_edge("check_weather", "check_schedule")
    # An auto-replan loop (weather-triggered) also routes back through
    # plan_trip -> critique_plan, so a replanned day gets re-checked too.
    graph.add_conditional_edges("check_schedule", _route_after_monitor, {"plan_trip": "plan_trip", END: END})

    return graph.compile()


orchestrator = build_orchestrator()
