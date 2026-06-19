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

from rag.search import smart_search, plan_itinerary

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

# ── /api/plan ────────────────────────────────────────────────────────────────

@app.post("/api/plan")
def plan(req: PlanRequest):
    candidates = smart_search(
        user_query   = req.query,
        user_lat     = req.lat,
        user_lng     = req.lng,
        target_time  = req.trip_start,
        target_day   = req.day,
        trip_end_time= req.trip_end,
    )
    itinerary = plan_itinerary(
        candidates  = candidates,
        start_lat   = req.lat,
        start_lng   = req.lng,
        trip_start  = req.trip_start,
        trip_end    = req.trip_end,
    )
    stops   = [s for s in itinerary if s["skipped_reason"] is None]
    skipped = [s for s in itinerary if s["skipped_reason"] is not None]

    # Build coordinate list for the map (home → stops → home)
    coords = [[req.lat, req.lng]] + [[s["lat"], s["lng"]] for s in stops]

    return {"stops": stops, "skipped": skipped, "coords": coords}


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

When the user describes what they want to see or do, call the plan_my_day tool.
After getting the result, explain the plan warmly -- walk through each stop with timing,
say why skipped places didn't make it, and mention if travel times are estimated.
Keep it conversational, like a local friend helping plan the day."""

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

    # First call -- may trigger tool use. Not streamed: we need the full
    # tool_calls list before we can run plan_my_day and continue the turn.
    first = groq_client.chat.completions.create(
        model=GROQ_MODEL,
        messages=messages,
        tools=[PLAN_TOOL],
        tool_choice="auto",
        temperature=0.3,
    )
    msg = first.choices[0].message
    messages.append(msg.model_dump(exclude_none=True))

    tool_calls = msg.tool_calls or []
    for tc in tool_calls:
        args = json.loads(tc.function.arguments)

        candidates = smart_search(
            user_query    = args["query"],
            user_lat      = req.lat,
            user_lng      = req.lng,
            target_time   = args.get("trip_start"),
            target_day    = args.get("day"),
            trip_end_time = args.get("trip_end"),
        )
        itinerary = plan_itinerary(
            candidates = candidates,
            start_lat  = req.lat,
            start_lng  = req.lng,
            trip_start = args.get("trip_start", "09:00"),
            trip_end   = args.get("trip_end",   "18:00"),
        )

        messages.append({
            "role":         "tool",
            "tool_call_id": tc.id,
            "content":      json.dumps(itinerary),
        })

    # Second call -- narrate the plan, streamed back to the client.
    def stream_gen():
        stream = groq_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=messages,
            stream=True,
        )
        for chunk in stream:
            token = chunk.choices[0].delta.content
            if token:
                yield token

    if tool_calls:
        return StreamingResponse(stream_gen(), media_type="text/plain")

    # No tool was called -- direct reply, no streaming needed.
    return {"reply": msg.content or ""}
