"""
api/main.py  --  FastAPI backend for Tripy.

Endpoints:
  POST /api/plan    -- run search + itinerary planning, return stops + map coords
  POST /api/chat    -- Groq streaming chat with plan_my_day tool
  GET  /api/route   -- OSRM route geometry for the map
"""

import json
import math
import os
import sys
import urllib.request
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from groq import Groq

load_dotenv(dotenv_path=Path(__file__).resolve().parents[1] / ".env")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rag.search import smart_search, plan_itinerary, WEEKDAYS
from agents.graph import orchestrator
from agents.state import trip_store, TripState
from datetime import datetime, timedelta

app = FastAPI(title="Tripy API", version="2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL   = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
OSRM_BASE    = "https://router.project-osrm.org"

groq_client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None

# ── Request / Response models ───────────────────────────────────────────────

class PlanRequest(BaseModel):
    query:      str
    lat:        float
    lng:        float
    trip_start: str          # "HH:MM"
    trip_end:   str          # "HH:MM"
    day:        Optional[str] = None   # "Monday" | "today" | "tomorrow" | None

class ChatMessage(BaseModel):
    role:    str
    content: str

class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    lat:      float
    lng:      float

class TripCheckRequest(BaseModel):
    current_lat: Optional[float] = None
    current_lng: Optional[float] = None
    simulated_now: Optional[str] = None  # "HH:MM" -- testing only, see agents/state.py
    auto_replan: bool = False

class TripReplanRequest(BaseModel):
    current_lat: Optional[float] = None
    current_lng: Optional[float] = None
    simulated_now: Optional[str] = None
    prefer_indoor: bool = True

# ── /api/plan ────────────────────────────────────────────────────────────────

def _resolve_day_name(day: Optional[str]) -> str:
    if day is None:
        return datetime.now().strftime("%A")
    if day.lower() == "today":
        return datetime.now().strftime("%A")
    if day.lower() == "tomorrow":
        return (datetime.now() + timedelta(days=1)).strftime("%A")
    return day.strip().capitalize()


def _weekday_index(day_name: str) -> int:
    try:
        return WEEKDAYS.index(day_name)
    except ValueError:
        return datetime.now().weekday()


@app.post("/api/plan")
def plan(req: PlanRequest):
    day_name = _resolve_day_name(req.day)
    state: TripState = {
        "mode": "plan",
        "query": req.query,
        "home_lat": req.lat,
        "home_lng": req.lng,
        "day": day_name,
        "weekday_index": _weekday_index(day_name),
        "trip_start": req.trip_start,
        "trip_end": req.trip_end,
    }
    result = orchestrator.invoke(state)

    trip_id = trip_store.create(result)

    stops, skipped = result["stops"], result["skipped"]
    coords = [[req.lat, req.lng]] + [[s["lat"], s["lng"]] for s in stops]

    return {
        "trip_id": trip_id,
        "stops": stops,
        "skipped": skipped,
        "coords": coords,
        "used_distance_fallback": result["used_distance_fallback"],
    }


# ── /api/trip/{id}/check, /replan -- the live monitoring + replanning agent ──

def _now_min(simulated_now: Optional[str]) -> int:
    if simulated_now:
        h, m = map(int, simulated_now.split(":"))
        return h * 60 + m
    now = datetime.now()
    return now.hour * 60 + now.minute


@app.post("/api/trip/{trip_id}/check")
def trip_check(trip_id: str, req: TripCheckRequest):
    """
    The Weather Monitoring Agent's entry point. Meant to be called by the
    frontend on an interval (every 30 min, per the original request) while a
    trip is live. Only flags a warning by default -- doesn't change the plan
    unless auto_replan is explicitly set, matching the "warn first, replan on
    request" flow that was actually asked for.
    """
    state = trip_store.get(trip_id)
    if state is None:
        raise HTTPException(404, f"No trip found with id {trip_id}")

    state["mode"] = "monitor"
    state["simulated_now"] = req.simulated_now
    state["auto_replan"] = req.auto_replan
    if req.current_lat is not None:
        state["current_lat"] = req.current_lat
    if req.current_lng is not None:
        state["current_lng"] = req.current_lng

    result = orchestrator.invoke(state)
    trip_store.save(trip_id, result)

    response = {
        "trip_id": trip_id,
        "needs_replan": result["needs_replan"],
        "weather_warnings": result.get("weather_warnings", []),
        "weather_check_failed": result.get("weather_check_failed", False),
        "weather_check_error": result.get("weather_check_error"),
        "checked_at": result.get("last_checked_at"),
    }
    if req.auto_replan:
        # The plan itself may have changed -- include it so the frontend can
        # update without a second round trip.
        response["stops"] = result["stops"]
        response["skipped"] = result["skipped"]
    return response


@app.post("/api/trip/{trip_id}/replan")
def trip_replan(trip_id: str, req: TripReplanRequest):
    """
    Fired when the person clicks "Replan" after seeing a weather warning.
    Re-runs the Trip Planning Agent for the remaining part of the day --
    already-departed stops are excluded, the starting point becomes the
    person's current location if given (otherwise the original home point),
    and prefer_indoor biases the search toward sheltered places.
    """
    state = trip_store.get(trip_id)
    if state is None:
        raise HTTPException(404, f"No trip found with id {trip_id}")

    now_min = _now_min(req.simulated_now)
    already_visited = {
        s["id"] for s in state.get("stops", [])
        if int(s["visit_ends"].split(":")[0]) * 60 + int(s["visit_ends"].split(":")[1]) <= now_min
    }

    state["mode"] = "plan"
    state["exclude_ids"] = list(already_visited)
    state["prefer_indoor"] = req.prefer_indoor
    state["trip_start"] = req.simulated_now or f"{now_min // 60:02d}:{now_min % 60:02d}"
    if req.current_lat is not None and req.current_lng is not None:
        state["home_lat"] = req.current_lat
        state["home_lng"] = req.current_lng

    result = orchestrator.invoke(state)
    trip_store.save(trip_id, result)

    coords = [[state["home_lat"], state["home_lng"]]] + [[s["lat"], s["lng"]] for s in result["stops"]]

    return {
        "trip_id": trip_id,
        "stops": result["stops"],
        "skipped": result["skipped"],
        "coords": coords,
        "used_distance_fallback": result["used_distance_fallback"],
    }


# ── /api/route ───────────────────────────────────────────────────────────────

@app.get("/api/route")
def get_route(coords: str):
    """
    coords: "lat1,lng1;lat2,lng2;..."
    Returns a GeoJSON LineString of the OSRM road route.
    """
    try:
        pairs = [c.split(",") for c in coords.split(";")]
        coord_str = ";".join(f"{p[1]},{p[0]}" for p in pairs)  # OSRM is lng,lat
        url = f"{OSRM_BASE}/route/v1/driving/{coord_str}?overview=full&geometries=geojson"
        with urllib.request.urlopen(url, timeout=8) as resp:
            data = json.loads(resp.read())
        if data.get("code") != "Ok":
            raise HTTPException(502, "OSRM returned an error")
        return data["routes"][0]["geometry"]
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"Route fetch failed: {e}")


# ── /api/chat  (Groq streaming + tool calling) ───────────────────────────────

SYSTEM_PROMPT = """You are Tripy, a friendly trip-planning assistant for Trivandrum, Kerala.

WHEN TO ASK VS WHEN TO BUILD:
Only ask a clarifying question if the request is genuinely ambiguous (e.g. they
gave no sense of duration or interest at all). If they've given you enough to
work with -- a time window (even rough) and some idea of what they want to see
-- call plan_my_day immediately. Don't ask about things you can reasonably
assume (e.g. default to "today" if no day is mentioned). At most one question,
only when truly needed -- never a checklist.

AFTER THE PLAN COMES BACK, the exact timing for each stop and the exact reason
each skipped place was left out are ALREADY shown to the user as structured
cards in the interface -- you don't need to restate those numbers or that
hour-by-hour reasoning in your own words, that would just repeat what they can
already see. Instead, use your reply for the part the cards can't do:
  - A brief, warm one- or two-line overview of the day.
  - For each stop, in order: what the place actually IS and why it's worth
    visiting, using the real visitor-review material given to you in its
    `insight` field -- paraphrase naturally, but don't invent details that
    aren't in there. Then connect it to what they asked for, using its `vibe`
    tags -- why does this specific place match their interest.
  - Keep timing to at most a passing word ("first stop", "to finish the day")
    -- never restate the literal opening/closing time or the reasoning behind
    it, the card below it already says that precisely.
  - For what didn't make the cut, a brief, warm sentence or two is enough --
    what kind of places they were and roughly why, without repeating the
    exact minute figures (the cards already show those).

NEVER mention internal field or variable names (e.g. "used_distance_fallback",
"skipped_reason", any JSON key, or other code-like terms) in your reply --
describe things in plain conversational English only. If travel times are
estimated rather than from live road data, just say so plainly, once.

FORMATTING: you can use markdown -- **bold** for place names, bullet points
for lists. Keep it warm and conversational, not a report.
"""

PLAN_TOOL = {
    "type": "function",
    "function": {
        "name": "plan_my_day",
        "description": "Plan a timed trip itinerary around Trivandrum based on what the traveller wants.",
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
        # Fails before any streaming response has started, so a normal
        # HTTP error is safe here -- the frontend will see it cleanly.
        raise HTTPException(502, f"Groq request failed: {e}")

    msg = first.choices[0].message
    tool_calls = msg.tool_calls or []

    # Built explicitly rather than via msg.model_dump(exclude_none=True),
    # which silently drops the "content" key when it's None -- Groq's API
    # then rejects the next call as malformed, and because that failure
    # happens mid-stream (after headers are already sent) it doesn't surface
    # as an error, it just hangs the connection open with nothing coming
    # through. This is the bug behind the chat going silent.
    assistant_msg = {"role": "assistant", "content": msg.content}
    if tool_calls:
        assistant_msg["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.function.name, "arguments": tc.function.arguments},
            }
            for tc in tool_calls
        ]
    messages.append(assistant_msg)

    for tc in tool_calls:
        try:
            args = json.loads(tc.function.arguments)
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
            trip_id = trip_store.create(plan_result)
            # Pass the structured result to the model so it can narrate it,
            # and include trip_id so the frontend can reference this trip.
            result_for_model = {
                "trip_id": trip_id,
                "stops":   plan_result["stops"],
                "skipped": plan_result["skipped"],
                "used_distance_fallback": plan_result["used_distance_fallback"],
            }
            tool_content = json.dumps(result_for_model)
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
                model=GROQ_MODEL,
                messages=messages,
                stream=True,
            )
            for chunk in stream:
                token = chunk.choices[0].delta.content
                if token:
                    yield token
        except Exception as e:
            # Headers are already sent by the time we're here, so this is
            # the only way left to tell the user something went wrong --
            # otherwise the connection just hangs with no data, which is
            # exactly what was happening before this fix.
            yield f"\n\n⚠️ I ran into a problem putting that into words: {e}"

    if tool_calls:
        return StreamingResponse(stream_gen(), media_type="text/plain")

    # No tool was called -- direct reply (e.g. a clarifying question), no streaming needed.
    return {"reply": msg.content or ""}
