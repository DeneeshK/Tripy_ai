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

from rag.search import smart_search, plan_itinerary, resolve_place_ids, WEEKDAYS
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
    query:       str
    lat:         float
    lng:         float
    trip_start:  str
    trip_end:    str
    day:            Optional[str]       = None
    meals:          Optional[list[str]] = None
    diet:           Optional[str]       = None
    meal_times:     Optional[dict]      = None
    food_place:     Optional[str]       = None
    start_place:    Optional[str]       = None
    end_place:      Optional[str]       = None
    exclude_places: Optional[list[str]] = None
    include_places: Optional[list[str]] = None

class TripRemoveRequest(BaseModel):
    id: str   # the stop id to drop from the plan

class TripMealsRequest(BaseModel):
    # meal -> chosen restaurant id, e.g. {"lunch": "41", "dinner": "17"}
    selections: dict = {}

class WeatherRequest(BaseModel):
    lat:   float
    lng:   float
    stops: list[dict] = []   # [{name, lat, lng, arrive_at}] for the per-stop forecast
    date:  Optional[str] = None   # "YYYY-MM-DD" trip day; forecast is pinned to it

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


def _resolve_trip_date(day: Optional[str], explicit_date: Optional[str] = None) -> str:
    """The trip's ABSOLUTE calendar date as 'YYYY-MM-DD', so weather is checked
    for the right day. Priority: an explicit YYYY-MM-DD, then today/tomorrow, then
    the next occurrence of a named weekday, else today."""
    if explicit_date:
        try:
            return datetime.fromisoformat(explicit_date).strftime("%Y-%m-%d")
        except ValueError:
            pass
    now = datetime.now()
    d = (day or "").strip().lower()
    if d in ("", "today"):
        return now.strftime("%Y-%m-%d")
    if d == "tomorrow":
        return (now + timedelta(days=1)).strftime("%Y-%m-%d")
    cap = d.capitalize()
    if cap in WEEKDAYS:
        ahead = (WEEKDAYS.index(cap) - now.weekday()) % 7
        ahead = ahead or 7   # "Monday" when today is Monday means next Monday
        return (now + timedelta(days=ahead)).strftime("%Y-%m-%d")
    return now.strftime("%Y-%m-%d")


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


def _norm_diet(d) -> Optional[str]:
    """Canonicalise the diet field into 'veg', 'nonveg', 'any', or None.
      • 'any'  = the user explicitly has no preference ("either", "both", "mix")
                 -> plan with no dietary filter.
      • None   = we simply don't know yet -> the caller should ASK the user.
    Keeping these two apart is what lets us ask once and never nag again (an
    'either' answer resolves to 'any', so the question won't repeat)."""
    d2 = str(d or "").strip().lower()
    veg    = {"vegetarian", "pure veg", "veg", "veg only", "veggie"}
    nonveg = {"non-vegetarian", "non vegetarian", "non-veg", "non veg", "nonveg", "non"}
    anypref = {"any", "either", "both", "mix", "mixed", "no preference", "doesn't matter",
               "does not matter", "whatever", "anything", "no pref", "dont care", "don't care"}
    if d2 in veg:     return "veg"
    if d2 in nonveg:  return "nonveg"
    if d2 in anypref: return "any"
    return None


def _already_asked(messages, needle: str) -> bool:
    """Did we already put this question to the user? Prevents re-asking (and any
    ask loop when the user's answer stays ambiguous)."""
    return any(getattr(m, "role", None) == "assistant" and needle in (m.content or "").lower()
               for m in messages)


# A distinctive phrase embedded in each question so we can tell we already asked
# it -- kept stable across the friendly wording variants below.
_Q_INTEREST = "kind of day"
_Q_TRIPTIME = "start and wrap up"
_Q_PASTTIME = "won't fit today"
_Q_FOOD     = "work in any meals"
_Q_DIET     = "for the meals"

# A start time this many minutes or less in the past is treated as "now" (the
# model rounding "start now" to the current minute, request latency, etc.) --
# not flagged as stale.
PAST_START_GRACE_MIN = 10

_FOOD_WORDS = ("breakfast", "lunch", "dinner", "supper", "brunch", "meal",
               "food", "restaurant", "hungry", "veg")


def _mentions_food(messages) -> bool:
    """Has the user themselves brought food up (wanting it OR declining it)? If so
    we don't ask about meals -- they've already addressed it."""
    return any(getattr(m, "role", None) == "user"
               and any(w in (m.content or "").lower() for w in _FOOD_WORDS)
               for m in messages)


def _stale_start_today(a: dict) -> bool:
    """True if the plan's date resolves to TODAY and the given trip_start is
    already more than PAST_START_GRACE_MIN minutes behind the real clock -- i.e.
    the requested window has already gone by and handing it to the planner as-is
    would silently produce a plan for a time slot that's already over."""
    trip_date = _resolve_trip_date(a.get("day"), a.get("date"))
    if trip_date != datetime.now().strftime("%Y-%m-%d"):
        return False
    try:
        sh, sm = map(int, str(a.get("trip_start", "")).split(":"))
    except (ValueError, AttributeError):
        return False
    now = datetime.now()
    start_min = sh * 60 + sm
    now_min = now.hour * 60 + now.minute
    return (now_min - start_min) > PAST_START_GRACE_MIN


def _variant(options, messages):
    """Pick one friendly phrasing -- stable within a conversation, but varied
    across trips, so the questions don't read like the same rigid template every
    single time."""
    seed = next((m.content or "" for m in messages if getattr(m, "role", None) == "user"), "")
    return options[sum(map(ord, seed)) % len(options)]


def _essential_info_question(calls, messages) -> Optional[str]:
    """If a plan call is missing something essential, return the ONE question to
    ask (asked at most once). The APP is the single voice that asks (the model is
    told not to interrogate) so the flow stays coherent. Order: interests → time
    window → food (which meals + veg/non-veg + any fixed time, all at once) →
    veg/non-veg follow-up."""
    for c in calls:
        if c.get("name") != "plan_my_day":
            continue
        try:
            a = json.loads(c["arguments"])
        except Exception:
            return None

        has_focus = bool((a.get("query") or "").strip() or a.get("include_places")
                         or a.get("start_place") or a.get("end_place"))
        if not has_focus and not _already_asked(messages, _Q_INTEREST):
            return _variant([
                "Love to help you plan this! **What kind of day** are you in the mood for — temples, "
                "beaches, museums, markets, a bit of nature? A vibe or one must-see is plenty to start.",
                "Let's build you a good one. **What kind of day** are you after — history and old "
                "architecture, seaside and sun, green and quiet, buzzing markets? Even a rough vibe helps.",
            ], messages)

        # Time window -- both ends needed. The model omits them when the user
        # never said when (trip_start/trip_end aren't required), so ask here
        # rather than let a fabricated 09:00-18:00 slip through.
        has_times = bool((a.get("trip_start") or "").strip()) and bool((a.get("trip_end") or "").strip())
        if not has_times and not _already_asked(messages, _Q_TRIPTIME):
            return _variant([
                "Nice pick. And **when do you want to start and wrap up**? A rough start and end works "
                "(say, “9 in the morning till 6”), or just tell me how many hours you've got.",
                "Good choice. **When do you want to start and wrap up** the day? Give me a start and end "
                "(like “10 to 5”), or how long you're free and I'll shape the day around it.",
            ], messages)

        # The window is present but has already gone by TODAY (e.g. it's 9:48 PM
        # and they asked for an 11:00 start) -- don't silently hand the planner a
        # stale time slot. Ask once whether to start now or plan another day.
        if has_times and _stale_start_today(a) and not _already_asked(messages, _Q_PASTTIME):
            now_label = datetime.now().strftime("%I:%M %p").lstrip("0")
            start_label = a["trip_start"]
            return _variant([
                f"Quick catch — it's already **{now_label}**, so a **{start_label}** start **won't fit today** "
                "anymore. Want me to **start from now** instead, or plan this for **another day**?",
                f"Heads up: it's **{now_label}** already, so **{start_label} today** has come and gone. "
                "Should I plan **starting from now**, or would you rather pick **a different day**?",
            ], messages)

        # ONE warm, grouped food question -- which meals + diet + any fixed time.
        meals = _norm_meals(a.get("meals"))
        if not meals and not _mentions_food(messages) and not _already_asked(messages, _Q_FOOD):
            return _variant([
                "Want me to **work in any meals** while we're out — breakfast, lunch, or supper? "
                "If so, tell me which, whether you'd like **veg or non-veg**, and if any needs to land "
                "at a set time (like medication with food). Or just say “no food” and I'll keep it to the sights.",
                "Should I **work in any meals** — breakfast, lunch, supper? Let me know which ones, veg "
                "or non-veg, and whether any has to be at a fixed time (say, meds with food). Happy to "
                "skip food entirely too — totally your call.",
            ], messages)

        # Veg/non-veg follow-up: they chose meals but didn't say which.
        if meals and _norm_diet(a.get("diet")) is None and not _already_asked(messages, _Q_DIET):
            return _variant([
                "Quick one so I pick the right places — **veg or non-veg for the meals**? "
                "(or say “either” and I'll mix it up).",
                "Got it. **Veg or non-veg for the meals** — or “either” for a bit of both?",
            ], messages)

        return None   # this plan call has everything it needs
    return None


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
        "trip_date":       _resolve_trip_date(req.day),
        "trip_start":      req.trip_start,
        "trip_end":        req.trip_end,
        "requested_meals": _norm_meals(req.meals),
        "diet":            _norm_diet(req.diet),
        "meal_times":      _norm_meal_times(req.meal_times),
        "specific_food_place": req.food_place,
        "start_place":     req.start_place,
        "end_place":       req.end_place,
        "exclude_ids":     resolve_place_ids(req.exclude_places),
        "include_places":  req.include_places or [],
    }
    result   = orchestrator.invoke(state)
    trip_id  = trip_store.create(result)
    stops    = result["stops"]
    home0    = [result.get("home_lat", req.lat), result.get("home_lng", req.lng)]
    coords   = [home0] + [[s["lat"], s["lng"]] for s in stops]
    return {
        "trip_id":               trip_id,
        "stops":                 stops,
        "skipped":               result["skipped"],
        "coords":                coords,
        "meal_suggestions":      result.get("meal_suggestions", {}),
        "trip_date":             result.get("trip_date"),
        "used_distance_fallback": result["used_distance_fallback"],
        "trace":                 result.get("trace", []),
        "plan_critique":         result.get("plan_critique", []),
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
        "trip_date":             result.get("trip_date"),
        "used_distance_fallback": result["used_distance_fallback"],
        "trace":                 result.get("trace", []),
        "plan_critique":         result.get("plan_critique", []),
    }

# ── /api/trip/{id}/remove  (edit: drop a stop the user doesn't want) ─────────

@app.post("/api/trip/{trip_id}/remove")
def trip_remove(trip_id: str, req: TripRemoveRequest):
    """User tapped ✕ on a stop. Drops it and re-plans. A removed MEAL just
    un-picks that restaurant (the sightseeing route is untouched). A removed
    sightseeing stop / destination is excluded and the day re-optimises around
    what's left, keeping any chosen meals."""
    state = trip_store.get(trip_id)
    if state is None:
        raise HTTPException(404, f"No trip found with id {trip_id}")

    remove_id = str(req.id)
    state["mode"] = "plan"

    selections = dict(state.get("meal_selections") or {})
    meal_of_removed = next((m for m, pid in selections.items() if pid == remove_id), None)

    if meal_of_removed is not None:
        # Un-pick this meal; keep the locked sightseeing route as-is.
        selections.pop(meal_of_removed, None)
        state["meal_selections"] = selections
        state["reuse_base"] = True
    else:
        # A sightseeing stop or the end destination: exclude it and re-solve.
        state["exclude_ids"] = sorted(set(state.get("exclude_ids", []) or []) | {remove_id})
        if state.get("end_place_id") == remove_id:
            state["end_place"] = None
            state["end_place_id"] = None
        state["reuse_base"] = False

    result = orchestrator.invoke(state)
    trip_store.save(trip_id, result)
    home0  = [result.get("home_lat"), result.get("home_lng")]
    coords = [home0] + [[s["lat"], s["lng"]] for s in result["stops"]]
    return {
        "trip_id":               trip_id,
        "stops":                 result["stops"],
        "skipped":               result["skipped"],
        "coords":                coords,
        "meal_suggestions":      result.get("meal_suggestions", {}),
        "trip_date":             result.get("trip_date"),
        "used_distance_fallback": result.get("used_distance_fallback", False),
        "trace":                 result.get("trace", []),
        "plan_critique":         result.get("plan_critique", []),
    }

# ── /api/trip/{id}/check and /replan ─────────────────────────────────────────

@app.post("/api/trip/{trip_id}/check")
def trip_check(trip_id: str, req: TripCheckRequest):
    """
    Weather + Schedule Monitoring Agents entry point. Called by the frontend
    periodically while a trip is live. Default: flags warnings but does NOT
    change the plan. Set auto_replan=True to let the WEATHER agent replan
    immediately on its own (the schedule agent never auto-replans -- an
    overstay is always surfaced as a choice, see schedule_warning below).
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
        "schedule_warning":     result.get("schedule_warning"),
        "checked_at":           result.get("last_checked_at"),
        "trace":                result.get("trace", []),
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
        "trace":                 result.get("trace", []),
        "plan_critique":         result.get("plan_critique", []),
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
        # Pin the per-stop forecast to the trip's day (falls back to today).
        ref_day = datetime.now()
        if req.date:
            try:
                ref_day = datetime.fromisoformat(req.date)
            except ValueError:
                pass
        rows, failed, error = conditions_for_stops(req.stops, ref_day)
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
    import datetime as _dt
    today    = now.strftime('%Y-%m-%d')
    tomorrow = (now + _dt.timedelta(days=1)).strftime('%Y-%m-%d')
    dayafter = (now + _dt.timedelta(days=2)).strftime('%Y-%m-%d')
    now_time = now.strftime('%I:%M %p')
    return f"""You are Tripy, a sharp, friendly trip planner for Trivandrum, Kerala.
CURRENT DATE & TIME: {now.strftime('%A, %d %B %Y')} at {now_time} (IST).

Be AGENTIC. Read the ENTIRE conversation, extract every detail the user has
already given, and act on it. Do NOT re-ask for anything they've stated, even
once, even in passing. Prefer doing over interrogating.

━━ TO PLAN OR RE-PLAN: call plan_my_day ━━
Fill as many parameters as you can from what the user said:
 • query        — their interests / what they want to see.
 • trip_start, trip_end — "HH:MM" 24h. Set them ONLY if the user gave a start/
                  end time or a duration. If they never said when, OMIT both —
                  do NOT invent 09:00-18:00; the app will ask for the window.
                  If the app already asked "it's already X, start from now or
                  another day?" and the user said to start now, set trip_start
                  to the CURRENT TIME given above, converted to 24h HH:MM —
                  do not reuse their original stale time.
 • date         — if they named any day/date (see DATES). Naming "tomorrow"
                  COUNTS as giving the day — do not question the timing then.
 • start_place  — where the trip begins ("from Azhimala", "starting at Kovalam").
 • end_place    — where the day must end ("to Napier Museum", "ending at Kovalam").
                  "from A to B" ⇒ start_place=A AND end_place=B. Use these fields,
                  never bury a start/end inside `query`.
 • meals        — any of ["breakfast","lunch","dinner"] they want ([] if none).
 • diet         — "veg" or "nonveg".
 • meal_times   — only a meal with a FIXED clock time (e.g. medication), HH:MM.
 • food_place   — a restaurant they named to include.
 • exclude_places — names of places to leave out ("remove X", "no X", "skip X").
 • include_places — landmarks the user insists on ("include Kuthira Malika and
                  Azhimala temple"). Force-included as must-visit stops. Not for
                  restaurants (food_place) or the start/end (start_place/end_place).

━━ DON'T INTERROGATE — LET THE APP ASK ━━
When the user wants a trip planned or changed, ALWAYS call plan_my_day with
whatever they've given so far. Do NOT ask the user clarifying questions yourself.
The app checks what's still missing (interests, time window, meals, veg/non-veg)
and asks for it in ONE friendly voice — if you also ask, the two voices clash and
repeat, which is exactly what we're fixing. So: extract what's there and call the
tool. Fill only fields the user actually stated; omit the rest (never invent
times, diet, or meals). If their start time already passed today and they named
no other day, just start from now. For genuine non-planning chit-chat, reply
normally.

━━ EDITS ARE RE-PLANS ━━
When the user changes an existing trip ("start from Azhimala instead", "remove
the museum", "add a beach", "make it end at Kovalam", "swap in temples"), call
plan_my_day AGAIN and RE-PASS EVERYTHING from before (query, times, date, meals,
diet, start/end) PLUS the change. Never drop details the user gave earlier.
For removals, put the place name in exclude_places (typos are fine — the backend
matches fuzzily) and keep any earlier exclusions too.

━━ DATES ━━ Resolve any named day/date to YYYY-MM-DD in `date`:
 today={today}, tomorrow={tomorrow}, day after tomorrow={dayafter}.
 "the 14th" → {now.strftime('%Y-%m')}-14 (next month if already past). The backend
 derives the weekday from `date`, so you don't also pass `day`.

━━ AFTER A PLAN ━━ The itinerary, skip reasons, and (if meals were requested)
restaurant cards render automatically below your message. In 1–2 warm lines,
summarise the day and briefly say what each main stop is, using its real
`insight` text (never invent facts). If meals were requested, add one line:
they can tap "Add" on a card — or "Let Tripy choose". Don't list restaurant
names yourself. **Bold** place names. Never mention internal field names.

━━ PLAN CRITIC FINDINGS — plan_critique ━━ The tool response includes a
`plan_critique` list: real, computed findings from re-checking the finished
plan against what the user actually asked for (not your judgment — a separate
deterministic check). If it's non-empty, you MUST address EVERY entry in your
reply, not just the ones you'd have noticed yourself:
 • severity "high" (a requested include or end-place didn't make it in) —
   lead with this. Say plainly what didn't fit and why (`reason` already has
   the real cause), then offer a concrete choice: extend the time window, or
   drop a lower-priority stop to make room.
 • severity "medium" (a requested meal got no candidates, or a named place
   couldn't be matched at all) — mention it in a line, suggest a fix (widen
   the window, relax the diet filter, check the spelling).
 • severity "low" (travel-heavy pacing, a lot of idle time at the end) — a
   brief, optional mention, not a big deal — one line, offer to adjust only
   if it seems worth it.
If `plan_critique` is empty, don't invent a caveat — say nothing about it.
Never mention the field name itself or that a "critic agent" exists; just
speak plainly, the way you already do for skip reasons.
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
                "trip_start": {"type": "string",  "description": "Start time HH:MM (24h). ONLY set it if the user gave a start time or a duration to derive one from. If they never said when the day starts, OMIT this field entirely — do NOT invent 09:00; the app will ask. If the app already asked about a stale start time and the user said to start now, use the CURRENT TIME from the system prompt (converted to 24h HH:MM), not their original stale time."},
                "trip_end":   {"type": "string",  "description": "End time HH:MM (24h). ONLY set it if the user gave an end time or a duration. If they never said when the day ends, OMIT this field — do NOT invent 18:00; the app will ask."},
                "day":        {"type": "string",  "description": "Weekday name ('Monday'..'Sunday'), 'today', or 'tomorrow'. Omit if `date` is provided."},
                "date":       {"type": "string",  "description": "Specific date as YYYY-MM-DD (e.g. '2026-07-14'). Pass this whenever the user mentions a specific date, even a partial one like 'the 14th', 'tomorrow', or 'day after tomorrow'. The backend resolves the correct weekday from this."},
                "meals":      {"type": "array", "items": {"type": "string"}, "description": "Which meals to weave into the day, each one of: breakfast, lunch, dinner (supper = dinner). Empty array if the user wants no food stops."},
                "diet":       {"type": "string", "description": "Dietary preference, either 'veg' or 'nonveg'. Only when a meal is requested AND the user stated it. Omit the field entirely if unknown — do NOT pass an empty string."},
                "meal_times": {"type": "object", "properties": {"breakfast": {"type": "string"}, "lunch": {"type": "string"}, "dinner": {"type": "string"}}, "description": "ONLY if the user must eat a meal at a specific clock time (e.g. for medication). Map that meal to HH:MM 24h, e.g. {\"lunch\": \"13:00\"}. Omit meals whose timing is flexible."},
                "food_place": {"type": "string", "description": "A specific restaurant the user explicitly asked to include by name (e.g. 'Villa Maya'). Force-included even if it's a small detour. Omit otherwise."},
                "start_place": {"type": "string", "description": "Where the trip STARTS, if the user names a place (e.g. 'from Vizhinjam', 'starting at Kovalam'). The whole route is built outward from here instead of the user's GPS location. Omit if they didn't say."},
                "end_place":   {"type": "string", "description": "Where the trip must END, if the user names a destination (e.g. 'to Napier Museum', 'ending at Kovalam beach'). This place is guaranteed to be the final stop of the day. Omit if they didn't say."},
                "exclude_places": {"type": "array", "items": {"type": "string"}, "description": "Names of places the user wants LEFT OUT of the plan (e.g. they said 'remove the museum', 'no Kuthira Malika', 'skip temples'). Pass the place names as spoken; the backend matches them even with typos. Carry earlier exclusions forward on re-plans."},
                "include_places": {"type": "array", "items": {"type": "string"}, "description": "Specific landmarks the user wants GUARANTEED in the plan (e.g. 'include Kuthira Malika and Azhimala temple', 'make sure to add the zoo'). These are force-included as must-visit stops. Names as spoken; typos are matched. Not for restaurants (use food_place) or the start/end (use start_place/end_place)."},
            },
            "required": ["query"],
        },
    },
}


def _recover_tool_calls(err) -> list:
    """Groq strict-validates tool calls and 400s the ENTIRE request if the model
    produces one that doesn't fit the schema (drops a required field, or emits an
    empty string for an enum). The rejected response still carries the model's
    intended call in `failed_generation` -- parse it so one sloppy generation
    degrades into a best-effort plan (bad/missing fields get normalised or
    defaulted downstream) instead of crashing the chat with a raw 400.

    Handles the two shapes different Groq models use for failed_generation:
      A) JSON: [{"name": ..., "parameters": {...}}]  (e.g. llama-4)
      B) llama tool syntax: <function=NAME>{...}</function>

    Returns a normalised [{id, name, arguments(str)}] list, or [] if nothing
    usable can be recovered (caller then surfaces the original error)."""
    body = getattr(err, "body", None)
    fg = (body or {}).get("error", {}).get("failed_generation") if isinstance(body, dict) else None
    if not fg or not str(fg).strip():
        return []
    fg = str(fg).strip()

    # Shape A -- a JSON array/object of {name, parameters}.
    try:
        parsed = json.loads(fg)
        items = parsed if isinstance(parsed, list) else [parsed]
        calls = []
        for i, c in enumerate(items):
            if isinstance(c, dict) and (c.get("name") or c.get("parameters") or c.get("arguments")):
                params = c.get("parameters") or c.get("arguments") or {}
                calls.append({"id": f"recovered_{i}", "name": c.get("name", "plan_my_day"),
                              "arguments": json.dumps(params)})
        if calls:
            return calls
    except Exception:
        pass

    # Shape B -- <function=NAME>{...json...}</function>  (one or more).
    import re
    calls = []
    for i, m in enumerate(re.finditer(r"<function=([^>]+)>(.*?)(?:</function>|$)", fg, re.DOTALL)):
        name, payload = m.group(1).strip(), m.group(2).strip()
        try:
            params = json.loads(payload)
        except Exception:
            continue
        calls.append({"id": f"recovered_{i}", "name": name, "arguments": json.dumps(params)})
    return calls


@app.post("/api/chat")
async def chat(req: ChatRequest):
    if groq_client is None:
        raise HTTPException(500, "GROQ_API_KEY not set in .env")

    messages = [{"role": "system", "content": _build_system_prompt()}]
    messages += [{"role": m.role, "content": m.content} for m in req.messages]

    assistant_content = None
    try:
        first = groq_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=messages,
            tools=[PLAN_TOOL],
            tool_choice="auto",
            temperature=0.3,
        )
        msg   = first.choices[0].message
        calls = [{"id": tc.id, "name": tc.function.name, "arguments": tc.function.arguments}
                 for tc in (msg.tool_calls or [])]
        assistant_content = msg.content
    except Exception as e:
        # A schema-rejected tool call (tool_use_failed) is recoverable -- don't
        # crash the chat over one malformed generation.
        calls = _recover_tool_calls(e)
        if not calls:
            raise HTTPException(502, f"Groq request failed: {e}")

    # ── Essential-info gate ─────────────────────────────────────────────────
    # Be a real agent: if the model tries to plan while an ESSENTIAL detail is
    # still unknown, ask the user for it instead of planning with a blank (that
    # blank diet is exactly what broke before). This is deterministic -- it does
    # not depend on the model choosing to ask -- and each thing is asked at most
    # once (we check the history), so the user is never nagged for what they've
    # already answered.
    gate = _essential_info_question(calls, req.messages)
    if gate:
        return {"reply": gate}

    # Build the assistant message explicitly -- model_dump(exclude_none=True)
    # silently drops 'content' when it's None, which makes Groq reject the
    # next request and causes the chat to hang silently.
    assistant_msg = {"role": "assistant", "content": assistant_content}
    if calls:
        assistant_msg["tool_calls"] = [
            {"id": c["id"], "type": "function",
             "function": {"name": c["name"], "arguments": c["arguments"]}}
            for c in calls
        ]
    messages.append(assistant_msg)

    structured_plan = None  # last successful plan -> streamed to the UI as a trailer

    for c in calls:
        try:
            args = json.loads(c["arguments"])

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
                "query":           args.get("query") or "",
                "home_lat":        req.lat,
                "home_lng":        req.lng,
                "day":             day_name,
                "weekday_index":   _weekday_index(day_name),
                "trip_date":       _resolve_trip_date(args.get("day"), args.get("date")),
                "trip_start":      args.get("trip_start", "09:00"),
                "trip_end":        args.get("trip_end",   "18:00"),
                "requested_meals": _norm_meals(args.get("meals")),
                "diet":            _norm_diet(args.get("diet")),
                "meal_times":      _norm_meal_times(args.get("meal_times")),
                "specific_food_place": args.get("food_place"),
                "start_place":     args.get("start_place"),
                "end_place":       args.get("end_place"),
                "exclude_ids":     resolve_place_ids(args.get("exclude_places")),
                "include_places":  args.get("include_places") or [],
            }
            plan_result  = orchestrator.invoke(state)
            trip_id      = trip_store.create(plan_result)
            # Route starts from the resolved home (a named start_place overrides GPS).
            home0        = [plan_result.get("home_lat", req.lat), plan_result.get("home_lng", req.lng)]
            coords       = [home0] + [[s["lat"], s["lng"]] for s in plan_result["stops"]]
            structured_plan = {
                "trip_id":               trip_id,
                "stops":                 plan_result["stops"],
                "skipped":               plan_result["skipped"],
                "coords":                coords,
                "meal_suggestions":      plan_result.get("meal_suggestions", {}),
                "trip_date":             plan_result.get("trip_date"),
                "used_distance_fallback": plan_result["used_distance_fallback"],
                "trace":                 plan_result.get("trace", []),
                "plan_critique":         plan_result.get("plan_critique", []),
            }
            tool_content = json.dumps({
                "trip_id":               trip_id,
                "stops":                 plan_result["stops"],
                "skipped":               plan_result["skipped"],
                "meal_suggestions":      plan_result.get("meal_suggestions", {}),
                "used_distance_fallback": plan_result["used_distance_fallback"],
                # Real, computed findings from the Plan Critic Agent (unfulfilled
                # includes/end place, unmet meals, pacing) -- see the system
                # prompt's instruction to address every one of these, not just
                # the ones the model happens to notice from the raw stop list.
                "plan_critique":         plan_result.get("plan_critique", []),
            })
        except Exception as e:
            tool_content = json.dumps({"error": f"Planning failed: {e}"})

        messages.append({
            "role":         "tool",
            "tool_call_id": c["id"],
            "name":         c["name"],
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

    if calls:
        return StreamingResponse(stream_gen(), media_type="text/plain")

    return {"reply": assistant_content or ""}
