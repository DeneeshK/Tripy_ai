"""
api/main.py  --  FastAPI backend for Tripy v2.

Endpoints:
  GET  /api/config              -- serves tile API keys to the frontend
  POST /api/plan                -- full search + OR-Tools itinerary via orchestrator
  POST /api/chat                -- Groq streaming chat with plan_my_day tool
  GET  /api/route               -- OSRM road-route geometry for the map
  POST /api/trip/{id}/check     -- weather monitoring agent (every 30 min)
  POST /api/trip/{id}/replan    -- replan after user accepts a weather warning
"""

import json
import os
import sys
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from groq import Groq
from pydantic import BaseModel

load_dotenv(dotenv_path=Path(__file__).resolve().parents[1] / ".env")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rag.search import smart_search, plan_itinerary, WEEKDAYS
from agents.graph import orchestrator
from agents.state import trip_store, TripState

# ── Environment ───────────────────────────────────────────────────────────────

GROQ_API_KEY   = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL     = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
OWM_API_KEY    = os.getenv("OWM_API_KEY", "")    # openweathermap.org free tier -- weather overlay
STADIA_API_KEY = os.getenv("STADIA_API_KEY", "")  # optional; localhost works without it
OSRM_BASE      = "https://router.project-osrm.org"

# Delimiter the /api/chat stream uses to append the structured plan JSON after
# the narrative. The frontend splits on this, renders the text, and uses the
# JSON for the map + stop/skip/meal cards.
PLAN_TRAILER = "<<<TRIPY_PLAN>>>"

groq_client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None

# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(title="Tripy API", version="2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Request models ────────────────────────────────────────────────────────────

class PlanRequest(BaseModel):
    query:      str
    lat:        float
    lng:        float
    trip_start: str
    trip_end:   str
    day:        Optional[str]       = None
    meals:      Optional[list[str]] = None
    diet:       Optional[str]       = None
    meal_times: Optional[dict]      = None
    food_place: Optional[str]       = None

class TripMealsRequest(BaseModel):
    # meal -> chosen restaurant id, e.g. {"lunch": "41", "dinner": "17"}
    selections: dict = {}

class WeatherRequest(BaseModel):
    lat:   float
    lng:   float
    stops: list[dict] = []   # [{name, lat, lng, arrive_at}] for the per-stop forecast

class ChatMessage(BaseModel):
    role:    str
    content: str

class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    lat:      float
    lng:      float

class TripCheckRequest(BaseModel):
    current_lat:   Optional[float] = None
    current_lng:   Optional[float] = None
    simulated_now: Optional[str]   = None  # "HH:MM" -- for testing without a real clock
    auto_replan:   bool = False

class TripReplanRequest(BaseModel):
    current_lat:   Optional[float] = None
    current_lng:   Optional[float] = None
    simulated_now: Optional[str]   = None
    prefer_indoor: bool = True

# ── Helpers ───────────────────────────────────────────────────────────────────

def _resolve_day_name(day: Optional[str]) -> str:
    if day is None or day.lower() == "today":
        return datetime.now().strftime("%A")
    if day.lower() == "tomorrow":
        return (datetime.now() + timedelta(days=1)).strftime("%A")
    return day.strip().capitalize()


def _weekday_index(day_name: str) -> int:
    try:
        return WEEKDAYS.index(day_name)
    except ValueError:
        return datetime.now().weekday()


def _norm_meals(meals) -> list:
    """Canonicalise meal names from the model/UI. 'supper' -> 'dinner', dedupe,
    drop anything unrecognised, so downstream MEAL_SLOTS lookups always match."""
    alias = {"supper": "dinner", "brunch": "lunch"}
    valid = {"breakfast", "lunch", "dinner"}
    out = []
    for m in (meals or []):
        m2 = str(m).strip().lower()
        m2 = alias.get(m2, m2)
        if m2 in valid and m2 not in out:
            out.append(m2)
    return out


def _norm_meal_times(d) -> dict:
    """{'supper': '20:00'} -> {'dinner': '20:00'}. Drops empty/invalid entries."""
    alias = {"supper": "dinner", "brunch": "lunch"}
    valid = {"breakfast", "lunch", "dinner"}
    out = {}
    for k, v in (d or {}).items():
        if not v:
            continue
        k2 = alias.get(str(k).strip().lower(), str(k).strip().lower())
        if k2 in valid:
            out[k2] = str(v).strip()
    return out


def _now_min(simulated_now: Optional[str]) -> int:
    if simulated_now:
        h, m = map(int, simulated_now.split(":"))
        return h * 60 + m
    now = datetime.now()
    return now.hour * 60 + now.minute

# ── /api/config ───────────────────────────────────────────────────────────────

@app.get("/api/config")
def config():
    """
    Serves tile API keys to the frontend so they don't get baked into the
    JS bundle at build time. Still visible via DevTools -- fine for free-tier
    map tile keys, just worth knowing.
    """
    return {
        "owm_api_key":    OWM_API_KEY,
        "stadia_api_key": STADIA_API_KEY,
    }

# ── /api/plan ─────────────────────────────────────────────────────────────────

@app.post("/api/plan")
def plan(req: PlanRequest):
    day_name = _resolve_day_name(req.day)
    state: TripState = {
        "mode":            "plan",
        "query":           req.query,
        "home_lat":        req.lat,
        "home_lng":        req.lng,
        "day":             day_name,
        "weekday_index":   _weekday_index(day_name),
        "trip_start":      req.trip_start,
        "trip_end":        req.trip_end,
        "requested_meals": _norm_meals(req.meals),
        "diet":            req.diet,
        "meal_times":      _norm_meal_times(req.meal_times),
        "specific_food_place": req.food_place,
    }
    result   = orchestrator.invoke(state)
    trip_id  = trip_store.create(result)
    stops    = result["stops"]
    coords   = [[req.lat, req.lng]] + [[s["lat"], s["lng"]] for s in stops]
    return {
        "trip_id":               trip_id,
        "stops":                 stops,
        "skipped":               result["skipped"],
        "coords":                coords,
        "meal_suggestions":      result.get("meal_suggestions", {}),
        "used_distance_fallback": result["used_distance_fallback"],
    }

# ── /api/trip/{id}/meals ─────────────────────────────────────────────────────

@app.post("/api/trip/{trip_id}/meals")
def trip_meals(trip_id: str, req: TripMealsRequest):
    """User tapped 'Add' on a meal suggestion. Re-plans with the chosen
    restaurant(s) anchored at their meal slot; the rest of the day re-optimises
    around them. One restaurant per meal (the dict key)."""
    state = trip_store.get(trip_id)
    if state is None:
        raise HTTPException(404, f"No trip found with id {trip_id}")

    selections = {m: pid for m, pid in (req.selections or {}).items() if pid}
    state["mode"]            = "plan"
    state["meal_selections"] = selections
    state["requested_meals"] = sorted(
        set(state.get("requested_meals") or []) | set(selections.keys())
    )
    # Keep the already-planned sightseeing route exactly as-is; only (re)insert meals.
    state["reuse_base"] = True

    result = orchestrator.invoke(state)
    trip_store.save(trip_id, result)
    coords = [[state["home_lat"], state["home_lng"]]] + [[s["lat"], s["lng"]] for s in result["stops"]]
    return {
        "trip_id":               trip_id,
        "stops":                 result["stops"],
        "skipped":               result["skipped"],
        "coords":                coords,
        "meal_suggestions":      result.get("meal_suggestions", {}),
        "used_distance_fallback": result["used_distance_fallback"],
    }

# ── /api/trip/{id}/check and /replan ─────────────────────────────────────────

@app.post("/api/trip/{trip_id}/check")
def trip_check(trip_id: str, req: TripCheckRequest):
    """
    Weather Monitoring Agent entry point. Called by the frontend every 30 min
    while a trip is live. Default: flags a warning but does NOT change the plan.
    Set auto_replan=True to let the agent replan immediately on its own.
    """
    state = trip_store.get(trip_id)
    if state is None:
        raise HTTPException(404, f"No trip found with id {trip_id}")

    state["mode"]          = "monitor"
    state["simulated_now"] = req.simulated_now
    state["auto_replan"]   = req.auto_replan
    if req.current_lat is not None:
        state["current_lat"] = req.current_lat
    if req.current_lng is not None:
        state["current_lng"] = req.current_lng

    result = orchestrator.invoke(state)
    trip_store.save(trip_id, result)

    response = {
        "trip_id":              trip_id,
        "needs_replan":         result["needs_replan"],
        "weather_warnings":     result.get("weather_warnings", []),
        "weather_check_failed": result.get("weather_check_failed", False),
        "weather_check_error":  result.get("weather_check_error"),
        "checked_at":           result.get("last_checked_at"),
    }
    if req.auto_replan:
        response["stops"]   = result["stops"]
        response["skipped"] = result["skipped"]
    return response


@app.post("/api/trip/{trip_id}/replan")
def trip_replan(trip_id: str, req: TripReplanRequest):
    """
    Fired when the user clicks 'Replan' after a weather warning. Excludes
    already-completed stops, updates the start to current time/location,
    and biases toward indoor places if prefer_indoor is set.
    """
    state = trip_store.get(trip_id)
    if state is None:
        raise HTTPException(404, f"No trip found with id {trip_id}")

    now_min = _now_min(req.simulated_now)
    already_visited = {
        s["id"] for s in state.get("stops", [])
        if int(s["visit_ends"].split(":")[0]) * 60 + int(s["visit_ends"].split(":")[1]) <= now_min
    }

    state["mode"]         = "plan"
    state["exclude_ids"]  = list(already_visited)
    state["prefer_indoor"] = req.prefer_indoor
    state["trip_start"]   = req.simulated_now or f"{now_min // 60:02d}:{now_min % 60:02d}"
    if req.current_lat is not None and req.current_lng is not None:
        state["home_lat"] = req.current_lat
        state["home_lng"] = req.current_lng

    result  = orchestrator.invoke(state)
    trip_store.save(trip_id, result)
    coords  = [[state["home_lat"], state["home_lng"]]] + [[s["lat"], s["lng"]] for s in result["stops"]]
    return {
        "trip_id":               trip_id,
        "stops":                 result["stops"],
        "skipped":               result["skipped"],
        "coords":                coords,
        "used_distance_fallback": result["used_distance_fallback"],
    }

# ── /api/route ────────────────────────────────────────────────────────────────

@app.get("/api/route")
def get_route(coords: str):
    """coords: 'lat1,lng1;lat2,lng2;...' -- returns GeoJSON LineString from OSRM."""
    try:
        pairs     = [c.split(",") for c in coords.split(";")]
        coord_str = ";".join(f"{p[1]},{p[0]}" for p in pairs)  # OSRM wants lng,lat
        url       = f"{OSRM_BASE}/route/v1/driving/{coord_str}?overview=full&geometries=geojson"
        with urllib.request.urlopen(url, timeout=8) as resp:
            data = json.loads(resp.read())
        if data.get("code") != "Ok":
            raise HTTPException(502, "OSRM returned an error")
        return data["routes"][0]["geometry"]
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"Route fetch failed: {e}")

# ── /api/weather  (persistent widget: current + per-stop forecast) ────────────

@app.post("/api/weather")
def weather(req: WeatherRequest):
    """Powers the always-on weather box. Returns the current conditions at the
    user's location plus a forecast for every planned stop. Degrades gracefully
    (failed=True) if the weather service can't be reached."""
    from agents.weather import fetch_current, conditions_for_stops

    out = {"current": None, "stops": [], "needs_replan": False, "failed": False, "error": None}
    try:
        out["current"] = fetch_current(req.lat, req.lng)
    except Exception as e:
        out["failed"] = True
        out["error"]  = f"Couldn't reach the weather service: {e}"

    if req.stops:
        rows, failed, error = conditions_for_stops(req.stops, datetime.now())
        out["stops"]        = rows
        out["needs_replan"] = any(r["is_warning"] for r in rows)
        if failed:
            out["failed"] = True
            out["error"]  = error
    return out

# ── /api/chat  (Groq streaming + tool calling) ────────────────────────────────

def _build_system_prompt() -> str:
    """Dynamic -- called fresh per request so the model always has the real current time."""
    now = datetime.now()
    return f"""You are Tripy, a friendly trip-planning assistant for Trivandrum, Kerala.

CURRENT DATE AND TIME: {now.strftime('%A, %d %B %Y at %I:%M %p')} (IST)

━━ HOW YOU GATHER DETAILS — ASK ONE THING AT A TIME ━━
Collect what you need STEP BY STEP. Ask EXACTLY ONE question per message, then
STOP and WAIT for the user's reply before the next step. NEVER bundle two
questions into one message. NEVER call plan_my_day until every step is done.
Work through the steps in order, skipping any that are already answered.

STEP 1 — Timing. Compare the requested window to the current time above:
  • Whole window is still in the future → say nothing about time; go to STEP 2.
  • Start has already passed but the end is still ahead (e.g. they said 9am-6pm
    and it's now {now.strftime('%I:%M %p')}) → ask ONLY this, then STOP and wait:
      "It's already {now.strftime('%I:%M %p')}. Want me to plan from now until
       your end time, or schedule this trip for another day?"
    - If they say continue / from now → use the CURRENT time as trip_start, then
      go to STEP 2.
    - If they choose another day → do NOT guess the day. Ask ONLY this, then STOP
      and wait: "Sure — which day? Tomorrow, the day after, or a specific date?"
      When they answer, resolve it to a `date` (see DATE RESOLUTION), keep their
      original start time, then go to STEP 2.
  • Whole window has already passed → ask ONLY: "That time's already gone for
    today — which day would you like instead? Tomorrow, the day after, or a
    specific date?" STOP and wait, then resolve their answer to a `date`.

STEP 2 — Meals. First work out which meals are even POSSIBLE for the trip window,
  and offer ONLY those — never offer a meal that can't fit:
    - breakfast only if the trip starts at or before ~09:30,
    - lunch only if the window covers roughly 12:30–14:00,
    - supper only if the trip runs to ~19:00 or later.
  (So a trip from noon → don't offer breakfast; a trip ending at 5pm → don't
  offer supper.) Then ask ONLY this, listing just the possible meals, and STOP
  and wait:
   "Before I plan — want me to include any meals ({{possible meals}})? Or none?"

STEP 3 — Diet (SKIP this step entirely if they wanted no meals). Ask ONLY this,
  then STOP and wait:
   "Great — and are you vegetarian or non-vegetarian?"

STEP 4 — Meal timing (SKIP if they wanted no meals). Ask ONLY this, then STOP
  and wait:
   "One more thing — do you need any meal at a specific time (for example, if you
    take medication with food), or should I just fit them in naturally as we go?"
  - If they give specific times, pass them in `meal_times` as HH:MM, e.g.
    {{"lunch": "13:00"}}.
  - If they say no / it's flexible, omit meal_times.

STEP 5 — Plan. Only now call plan_my_day, passing everything you've gathered:
  query, trip_start, trip_end, date (if any), meals (array; [] if none), diet,
  meal_times (only for meals with a fixed time), and food_place if the user named
  a specific restaurant.

━━ DATE RESOLUTION ━━
If the user mentions any specific date, resolve it to YYYY-MM-DD and pass it
as the `date` parameter. Examples:
- "the 14th" or "14th" → assume CURRENT month and year → {now.strftime('%Y-%m')}-14
  (if that date is already past this month, use next month instead)
- "June 14" → {now.year}-06-14
- "next Friday" → compute from today ({now.strftime('%A %d %B')})
- "today" → {now.strftime('%Y-%m-%d')}
- "tomorrow" → {(datetime.now() + __import__('datetime').timedelta(days=1)).strftime('%Y-%m-%d')}
- "day after tomorrow" → {(datetime.now() + __import__('datetime').timedelta(days=2)).strftime('%Y-%m-%d')}
The backend extracts the correct weekday from this date automatically, so you
don't also need to pass `day` when `date` is provided.

━━ AFTER THE PLAN — MEAL SUGGESTIONS ━━
If meals were requested, the per-meal restaurant suggestions appear as cards
below your message. Tell the user, in one line, that they can tap "Add" on one
card per meal — or tap "Let Tripy choose" to have a good one picked for them. Do
NOT list the restaurant names yourself; the cards already show them with ratings
and reviews.

━━ CONVERSATION MEMORY ━━
You have the full conversation history. Never re-ask for something already given.
If the user has described their interests and time window earlier in the chat,
carry that forward -- don't start from scratch on each message.

━━ AFTER THE PLAN COMES BACK ━━
The exact timing cards and skip-reason cards are already shown in the UI.
Your reply should cover the part cards can't:
  - Brief warm overview (1-2 lines).
  - Per stop: what the place IS and why it fits what they asked for, drawing
    from its `insight` field (real visitor-review material, not invented).
  - Skipped places: brief sentence on what kind they were and roughly why.
  - If travel times are estimated, mention it once.

NEVER mention internal field names (used_distance_fallback, skipped_reason,
JSON keys, etc.) -- plain English only.
FORMATTING: **bold** place names, bullet lists are fine. Warm and conversational.
"""


PLAN_TOOL = {
    "type": "function",
    "function": {
        "name": "plan_my_day",
        "description": "Plan a timed trip itinerary around Trivandrum.",
        "parameters": {
            "type": "object",
            "properties": {
                "query":      {"type": "string",  "description": "What the traveller wants to see/do."},
                "trip_start": {"type": "string",  "description": "Start time HH:MM (24h). Use current time if the user's requested start has already passed."},
                "trip_end":   {"type": "string",  "description": "End time HH:MM (24h)."},
                "day":        {"type": "string",  "description": "Weekday name ('Monday'..'Sunday'), 'today', or 'tomorrow'. Omit if `date` is provided."},
                "date":       {"type": "string",  "description": "Specific date as YYYY-MM-DD (e.g. '2026-07-14'). Pass this whenever the user mentions a specific date, even a partial one like 'the 14th', 'tomorrow', or 'day after tomorrow'. The backend resolves the correct weekday from this."},
                "meals":      {"type": "array", "items": {"type": "string", "enum": ["breakfast", "lunch", "dinner", "supper"]}, "description": "Which meals to weave into the day. Empty if the user wants no food stops. 'supper' is treated as dinner."},
                "diet":       {"type": "string", "enum": ["veg", "nonveg"], "description": "Dietary preference, used to pick restaurants. Only needed if at least one meal is requested."},
                "meal_times": {"type": "object", "properties": {"breakfast": {"type": "string"}, "lunch": {"type": "string"}, "dinner": {"type": "string"}}, "description": "ONLY if the user must eat a meal at a specific clock time (e.g. for medication). Map that meal to HH:MM 24h, e.g. {\"lunch\": \"13:00\"}. Omit meals whose timing is flexible."},
                "food_place": {"type": "string", "description": "A specific restaurant the user explicitly asked to include by name (e.g. 'Villa Maya'). Force-included even if it's a small detour. Omit otherwise."},
            },
            "required": ["query", "trip_start", "trip_end"],
        },
    },
}


@app.post("/api/chat")
async def chat(req: ChatRequest):
    if groq_client is None:
        raise HTTPException(500, "GROQ_API_KEY not set in .env")

    messages = [{"role": "system", "content": _build_system_prompt()}]
    messages += [{"role": m.role, "content": m.content} for m in req.messages]

    try:
        first = groq_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=messages,
            tools=[PLAN_TOOL],
            tool_choice="auto",
            temperature=0.3,
        )
    except Exception as e:
        raise HTTPException(502, f"Groq request failed: {e}")

    msg        = first.choices[0].message
    tool_calls = msg.tool_calls or []

    # Build the assistant message explicitly -- model_dump(exclude_none=True)
    # silently drops 'content' when it's None, which makes Groq reject the
    # next request and causes the chat to hang silently.
    assistant_msg = {"role": "assistant", "content": msg.content}
    if tool_calls:
        assistant_msg["tool_calls"] = [
            {"id": tc.id, "type": "function",
             "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
            for tc in tool_calls
        ]
    messages.append(assistant_msg)

    structured_plan = None  # last successful plan -> streamed to the UI as a trailer

    for tc in tool_calls:
        try:
            args = json.loads(tc.function.arguments)

            # Resolve weekday from `date` (YYYY-MM-DD) if given, else from `day` name.
            # `date` takes priority because it's unambiguous -- the model passes it
            # when the user mentions any specific date, even a partial one like "the 14th".
            if args.get("date"):
                try:
                    from datetime import date as dt_date
                    d         = dt_date.fromisoformat(args["date"])
                    wi        = d.weekday()
                    day_name  = WEEKDAYS[wi]
                except ValueError:
                    day_name  = _resolve_day_name(args.get("day"))
            else:
                day_name = _resolve_day_name(args.get("day"))

            state: TripState = {
                "mode":            "plan",
                "query":           args["query"],
                "home_lat":        req.lat,
                "home_lng":        req.lng,
                "day":             day_name,
                "weekday_index":   _weekday_index(day_name),
                "trip_start":      args.get("trip_start", "09:00"),
                "trip_end":        args.get("trip_end",   "18:00"),
                "requested_meals": _norm_meals(args.get("meals")),
                "diet":            args.get("diet"),
                "meal_times":      _norm_meal_times(args.get("meal_times")),
                "specific_food_place": args.get("food_place"),
            }
            plan_result  = orchestrator.invoke(state)
            trip_id      = trip_store.create(plan_result)
            coords       = [[req.lat, req.lng]] + [[s["lat"], s["lng"]] for s in plan_result["stops"]]
            structured_plan = {
                "trip_id":               trip_id,
                "stops":                 plan_result["stops"],
                "skipped":               plan_result["skipped"],
                "coords":                coords,
                "meal_suggestions":      plan_result.get("meal_suggestions", {}),
                "used_distance_fallback": plan_result["used_distance_fallback"],
            }
            tool_content = json.dumps({
                "trip_id":               trip_id,
                "stops":                 plan_result["stops"],
                "skipped":               plan_result["skipped"],
                "meal_suggestions":      plan_result.get("meal_suggestions", {}),
                "used_distance_fallback": plan_result["used_distance_fallback"],
            })
        except Exception as e:
            tool_content = json.dumps({"error": f"Planning failed: {e}"})

        messages.append({
            "role":         "tool",
            "tool_call_id": tc.id,
            "name":         tc.function.name,
            "content":      tool_content,
        })

    def stream_gen():
        try:
            stream = groq_client.chat.completions.create(
                model=GROQ_MODEL, messages=messages, stream=True,
            )
            for chunk in stream:
                token = chunk.choices[0].delta.content
                if token:
                    yield token
        except Exception as e:
            yield f"\n\n⚠️ Something went wrong: {e}"
        # Trailer: the real structured plan, so the map + cards reflect exactly
        # what was planned (no separate, dumber /api/plan call needed).
        if structured_plan is not None:
            yield "\n" + PLAN_TRAILER + json.dumps(structured_plan)

    if tool_calls:
        return StreamingResponse(stream_gen(), media_type="text/plain")

    return {"reply": msg.content or ""}
