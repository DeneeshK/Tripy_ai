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
    day:        Optional[str] = None

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
        "mode":          "plan",
        "query":         req.query,
        "home_lat":      req.lat,
        "home_lng":      req.lng,
        "day":           day_name,
        "weekday_index": _weekday_index(day_name),
        "trip_start":    req.trip_start,
        "trip_end":      req.trip_end,
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

# ── /api/chat  (Groq streaming + tool calling) ────────────────────────────────

SYSTEM_PROMPT = """You are Tripy, a friendly trip-planning assistant for Trivandrum, Kerala.

WHEN TO ASK VS WHEN TO BUILD:
Only ask a clarifying question if the request is genuinely ambiguous (no time
window, no interests at all). If they've given you enough -- even a rough time
and a vibe -- call plan_my_day immediately. Default to "today" if no day is
mentioned. At most one question, only when truly needed.

AFTER THE PLAN COMES BACK, the exact timing for each stop and the exact skip
reasons are already shown as structured cards in the UI -- don't restate them.
Instead:
  - A brief warm overview of the day (one or two lines).
  - For each stop: what the place IS and why it matches what they asked for,
    drawing from its `insight` field (real visitor-review material). Paraphrase
    naturally -- don't invent details not in there.
  - For what didn't make the cut: a brief sentence or two covering what kind
    of places they were and roughly why, without repeating exact minute figures.
  - If travel times are estimated rather than from live road data, say so once.

NEVER mention internal field or variable names (used_distance_fallback,
skipped_reason, JSON keys, etc.) -- plain English only.
FORMATTING: markdown is fine -- **bold** for place names, bullet lists.
Keep it warm and conversational.
"""

PLAN_TOOL = {
    "type": "function",
    "function": {
        "name": "plan_my_day",
        "description": "Plan a timed trip itinerary around Trivandrum.",
        "parameters": {
            "type": "object",
            "properties": {
                "query":      {"type": "string", "description": "What the traveller wants to see/do."},
                "trip_start": {"type": "string", "description": "Start time HH:MM (24h)."},
                "trip_end":   {"type": "string", "description": "End time HH:MM (24h)."},
                "day":        {"type": "string", "description": "Weekday name, 'today', or 'tomorrow'."},
            },
            "required": ["query", "trip_start", "trip_end"],
        },
    },
}


@app.post("/api/chat")
async def chat(req: ChatRequest):
    if groq_client is None:
        raise HTTPException(500, "GROQ_API_KEY not set in .env")

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
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

    for tc in tool_calls:
        try:
            args     = json.loads(tc.function.arguments)
            day_name = _resolve_day_name(args.get("day"))
            state: TripState = {
                "mode":          "plan",
                "query":         args["query"],
                "home_lat":      req.lat,
                "home_lng":      req.lng,
                "day":           day_name,
                "weekday_index": _weekday_index(day_name),
                "trip_start":    args.get("trip_start", "09:00"),
                "trip_end":      args.get("trip_end",   "18:00"),
            }
            plan_result = orchestrator.invoke(state)
            trip_id     = trip_store.create(plan_result)
            tool_content = json.dumps({
                "trip_id": trip_id,
                "stops":   plan_result["stops"],
                "skipped": plan_result["skipped"],
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

    if tool_calls:
        return StreamingResponse(stream_gen(), media_type="text/plain")

    return {"reply": msg.content or ""}
