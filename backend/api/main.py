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
    result = plan_itinerary(
        candidates  = candidates,
        start_lat   = req.lat,
        start_lng   = req.lng,
        trip_start  = req.trip_start,
        trip_end    = req.trip_end,
    )
    stops, skipped = result["stops"], result["skipped"]

    # Build coordinate list for the map (home → stops → home)
    coords = [[req.lat, req.lng]] + [[s["lat"], s["lng"]] for s in stops]

    return {
        "stops": stops,
        "skipped": skipped,
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
            candidates = smart_search(
                user_query    = args["query"],
                user_lat      = req.lat,
                user_lng      = req.lng,
                target_time   = args.get("trip_start"),
                target_day    = args.get("day"),
                trip_end_time = args.get("trip_end"),
            )
            result = plan_itinerary(
                candidates = candidates,
                start_lat  = req.lat,
                start_lng  = req.lng,
                trip_start = args.get("trip_start", "09:00"),
                trip_end   = args.get("trip_end",   "18:00"),
            )
            tool_content = json.dumps(result)
        except Exception as e:
            # Surface the failure to the model instead of crashing the
            # request -- it can apologise and explain rather than the
            # connection just going dead.
            tool_content = json.dumps({"error": f"Planning failed: {e}"})

        messages.append({
            "role":         "tool",
            "tool_call_id": tc.id,
            "name":         tc.function.name,  # required by Groq's tool-call contract; was missing
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
