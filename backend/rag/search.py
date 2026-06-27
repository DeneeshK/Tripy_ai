"""
search.py  --  semantic search + itinerary planning.

Keeps original logic from search_agent.py:
  - check_availability(): unchanged
  - smart_search(): reads all fields from Chroma metadata, same post-processing
  - plan_itinerary(): same structure but travel time comes from OSRM (real roads)
    instead of haversine + flat 25 km/h, and uses OR-Tools instead of greedy loop

Bug fixed vs original: Chroma query now includes "distances" so relevance
scores are returned and used -- the original only asked for "metadatas" and
"documents", silently discarding the similarity ranking.
"""

import math
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Optional, Tuple

import chromadb
from sentence_transformers import SentenceTransformer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine.distance_matrix import time_matrix_with_fallback
from engine.itinerary_engine import Place, plan_itinerary as ortools_plan
from engine.hours import resolve_for_day, best_window_in_span
from engine.meals import (
    MEAL_LABELS, MEAL_DURATION_CAP_MIN, N_SUGGESTIONS, FOOD_CATEGORIES,
    diet_eligible, meal_slot_in_trip, meal_window_for_place,
    rank_by_route_proximity, suggestion_card,
)

VDB_PATH   = Path(__file__).resolve().parent.parent / "trivandrum_vdb"
COLLECTION = "landmark_repository"
MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

_model: Optional[SentenceTransformer] = None


def _get_model() -> SentenceTransformer:
    global _model
    if _model is None:
        _model = SentenceTransformer(MODEL_NAME)
    return _model


def _get_collection():
    client = chromadb.PersistentClient(path=str(VDB_PATH))
    try:
        return client.get_collection(name=COLLECTION)
    except Exception:
        raise RuntimeError(
            f"Vector store not found at {VDB_PATH}. "
            "Run `python -m rag.ingest` first."
        )


# ---------------------------------------------------------------------------
# Availability check (identical to original check_availability)
# ---------------------------------------------------------------------------

def check_availability(
    open_hours_str: str,
    closed_day: str,
    day_name: str,
    window_start_str: str,
    window_end_str: str,
) -> dict:
    if closed_day and closed_day != "None" and day_name == closed_day:
        return {"status": "Closed Today", "opens_at": None,
                "note": f"Closed every {closed_day}"}

    if not open_hours_str or open_hours_str in ("Unknown", "None"):
        return {"status": "Unknown", "opens_at": None, "note": "Hours not available"}

    try:
        win_start = datetime.strptime(window_start_str, "%H:%M")
        win_end   = datetime.strptime(window_end_str,   "%H:%M")
    except ValueError:
        return {"status": "Unknown", "opens_at": None, "note": "Invalid window"}

    open_now       = False
    opens_later_at = None

    for period in open_hours_str.split(";"):
        period = period.strip()
        try:
            start_str, end_str = period.split("-")
            p_start = datetime.strptime(start_str.strip(), "%H:%M")
            p_end   = datetime.strptime(end_str.strip(),   "%H:%M")
        except Exception:
            continue

        overlap_start = max(p_start, win_start)
        overlap_end   = min(p_end, win_end)
        if overlap_start >= overlap_end:
            continue

        if p_start <= win_start <= p_end:
            open_now = True
            break

        if p_start > win_start:
            if opens_later_at is None or p_start < opens_later_at:
                opens_later_at = p_start

    if open_now:
        return {"status": "Open Now", "opens_at": None,
                "note": "Open during your trip window"}
    if opens_later_at:
        t = opens_later_at.strftime("%I:%M %p")
        return {"status": "Opens Later", "opens_at": opens_later_at.strftime("%H:%M"),
                "note": f"Opens at {t} — plan to visit after that"}
    return {"status": "Closed", "opens_at": None,
            "note": f"Not open during your trip window ({window_start_str}–{window_end_str})"}


# ---------------------------------------------------------------------------
# Smart search (same as original but includes distances + relevance)
# ---------------------------------------------------------------------------

def smart_search(
    user_query: str,
    user_lat: float,
    user_lng: float,
    target_time: str = None,
    target_day: str = None,
    trip_end_time: str = None,
    n_results: int = 10,
    exclude_ids: set = None,
) -> List[Dict]:
    current_time = target_time or datetime.now().strftime("%H:%M")
    end_time     = trip_end_time or "23:59"

    if target_day is None:
        day_name = datetime.now().strftime("%A")
    elif target_day.lower() == "tomorrow":
        day_name = (datetime.now() + timedelta(days=1)).strftime("%A")
    else:
        day_name = target_day.strip().capitalize()

    model  = _get_model()
    vector = model.encode(user_query).tolist()

    collection = _get_collection()
    results = collection.query(
        query_embeddings=[vector],
        n_results=n_results + len(exclude_ids or []),  # pad so excluded ids don't shrink the pool
        include=["metadatas", "documents", "distances"],   # distances was missing in original
    )

    recommendations = []
    for i in range(len(results["ids"][0])):
        place_id = results["ids"][0][i]
        if exclude_ids and place_id in exclude_ids:
            continue
        meta     = results["metadatas"][0][i]
        distance = results["distances"][0][i]
        doc      = results["documents"][0][i] if results.get("documents") else ""
        # Convert Chroma L2 distance to a 0-1 relevance score
        relevance = round(1.0 / (1.0 + distance), 3)

        dist_km = math.sqrt(
            (user_lat - meta["lat"]) ** 2 + (user_lng - meta["lng"]) ** 2
        ) * 111  # rough km, OSRM handles real routing

        avail = check_availability(
            meta.get("regular_hours", "Unknown"),
            meta.get("closed_on", "None"),
            day_name,
            current_time,
            end_time,
        )

        # The document is "Place: {name}. Vibe: {tags}. Insight: {review_text}" --
        # pull just the review portion so the LLM has real visitor-review material
        # to describe the place with, instead of inventing details from its own
        # general knowledge (which happened to be accurate last time, but that's
        # luck, not grounding -- same blind spot as the missing distances field).
        insight = doc.split("Insight: ", 1)[-1].strip() if "Insight: " in doc else ""

        recommendations.append({
            "id":               place_id,
            "name":             meta["name"],
            "lat":              meta["lat"],
            "lng":              meta["lng"],
            "category":         meta.get("category", ""),
            "relevance":        relevance,
            "distance_km":      round(dist_km, 2),
            "status":           avail["status"],
            "opens_at":         avail["opens_at"],
            "availability_note":avail["note"],
            "vibe":             meta.get("vibe_tags", ""),
            "insight":          insight,
            "regular_hours":    meta.get("regular_hours", "Unknown"),
            "special_hours":    meta.get("special_hours", "None"),
            "closed_on":        meta.get("closed_on", "None"),
            "avg_duration":     meta.get("avg_duration", 1.0),
            "diet":             meta.get("diet", "na"),
            "rating":           meta.get("rating", 0.0),
            "trip_window":      f"{day_name} {current_time}–{end_time}",
        })

    return recommendations


# ---------------------------------------------------------------------------
# Food places + per-meal suggestions
# ---------------------------------------------------------------------------

def get_food_places(exclude_ids: set = None) -> List[Dict]:
    """Every restaurant/cafe/lounge in the store, with diet + rating + the real
    review insight. Only ~22 rows, so we fetch all and filter in Python rather
    than relying on a metadata `where` query (keeps it Chroma-version agnostic)."""
    exclude_ids = exclude_ids or set()
    collection = _get_collection()
    res = collection.get(include=["metadatas", "documents"])
    out = []
    for i, pid in enumerate(res["ids"]):
        if pid in exclude_ids:
            continue
        meta = res["metadatas"][i]
        if meta.get("category") not in FOOD_CATEGORIES:
            continue
        doc = res["documents"][i] if res.get("documents") else ""
        insight = doc.split("Insight: ", 1)[-1].strip() if "Insight: " in doc else ""
        out.append({
            "id": pid, "name": meta["name"], "lat": meta["lat"], "lng": meta["lng"],
            "category": meta.get("category", ""), "diet": meta.get("diet", "na"),
            "rating": meta.get("rating", 0.0), "vibe": meta.get("vibe_tags", ""),
            "insight": insight, "regular_hours": meta.get("regular_hours", "Unknown"),
            "special_hours": meta.get("special_hours", "None"),
            "closed_on": meta.get("closed_on", "None"),
            "avg_duration": meta.get("avg_duration", 1.0),
        })
    return out


def build_meal_suggestions(
    home: Tuple[float, float],
    base_stops: List[Dict],
    requested_meals: List[str],
    diet: Optional[str],
    weekday_index: int,
    trip_start_min: int,
    trip_end_min: int,
    selections: Dict = None,
) -> Dict[str, List[Dict]]:
    """For each requested meal, a short list of diet-appropriate restaurants that
    are open during the meal slot, ranked by least detour from the planned route.
    The user's already-chosen place (if any) is flagged `added` and pinned on top;
    a place chosen for another meal is excluded so each spot is used once."""
    selections = selections or {}
    food = get_food_places()
    by_id = {p["id"]: p for p in food}
    route_points = [home] + [(s["lat"], s["lng"]) for s in base_stops]

    suggestions: Dict[str, List[Dict]] = {}
    for meal in requested_meals:
        sel_id = selections.get(meal)
        other_selected = {v for k, v in selections.items() if k != meal and v}

        eligible = []
        for p in food:
            if p["id"] in other_selected:
                continue
            if not diet_eligible(p["diet"], diet):
                continue
            win = meal_window_for_place(
                meal, p["closed_on"], p["regular_hours"], p["special_hours"],
                weekday_index, trip_start_min, trip_end_min,
            )
            if win is None:
                continue
            eligible.append(p)

        ranked = rank_by_route_proximity(route_points, eligible, time_matrix_with_fallback)
        cards = [suggestion_card(p, detour, added=(p["id"] == sel_id))
                 for detour, p in ranked[:N_SUGGESTIONS]]

        # Pin the user's current pick on top even if it isn't in the nearest N.
        if sel_id and sel_id in by_id and not any(c["id"] == sel_id for c in cards):
            detour = next((d for d, p in ranked if p["id"] == sel_id), 0.0)
            cards.insert(0, suggestion_card(by_id[sel_id], detour, added=True))

        suggestions[meal] = cards
    return suggestions


# ---------------------------------------------------------------------------
# Itinerary planner  (OR-Tools with OSRM travel times)
# ---------------------------------------------------------------------------

def _resolve_weekday_index(day_name: str) -> int:
    try:
        return WEEKDAYS.index(day_name.capitalize())
    except ValueError:
        return datetime.now().weekday()


def _fmt(mins: int) -> str:
    mins = int(mins)
    return f"{mins // 60:02d}:{mins % 60:02d}"


def _explain_timing(c: dict, place: Place, stop, weekday_name: str, trip_start_mins: int, trip_end_mins: int) -> str:
    """
    Real, computed explanation for why a stop is scheduled at this specific
    time, grounded in its actual resolved opening window -- not invented
    narrative. Same fix as the one applied to skip reasons: give the model
    real facts to relay instead of letting it improvise something plausible-
    sounding from a generic status field.
    """
    if place.window is None:
        return ""
    open_m, close_m = place.window

    # Hours fully cover the whole trip window -- they genuinely didn't
    # constrain this stop's timing at all. Say so honestly rather than
    # implying a constraint that wasn't real.
    if open_m <= trip_start_mins and close_m >= trip_end_mins:
        return (
            f"Open right through your whole day today ({_fmt(open_m)}-{_fmt(close_m)}), "
            f"so the timing here came down to your route, not its hours."
        )

    if place.hours_source == "special":
        regular = c.get("regular_hours", "")
        contrast = f" -- its usual hours are {regular}" if regular and regular != "Unknown" else ""
        return (
            f"{weekday_name}s, {place.name} keeps special hours, {_fmt(open_m)}-{_fmt(close_m)}{contrast}, "
            f"so the visit is scheduled inside that window."
        )

    arrived_at_open  = (stop.arrival_min - open_m) <= 10
    left_near_close  = (close_m - stop.departure_min) <= 15
    opens_after_trip_start = open_m > trip_start_mins
    closes_before_trip_end = close_m < trip_end_mins

    if closes_before_trip_end and left_near_close:
        return f"Closes at {_fmt(close_m)} today, earlier than the rest of your day, so the visit is timed to finish before then."
    if opens_after_trip_start and arrived_at_open:
        return f"Doesn't open until {_fmt(open_m)}, so this is the earliest it could be scheduled today."
    if closes_before_trip_end or opens_after_trip_start:
        return f"Open {_fmt(open_m)}-{_fmt(close_m)} today, so the visit is scheduled inside that window."
    return f"Open {_fmt(open_m)}-{_fmt(close_m)} today -- plenty of flexibility, so timing here was mostly about your route."


def plan_itinerary(
    candidates: List[Dict],
    start_lat: float,
    start_lng: float,
    trip_start: str,
    trip_end: str,
    meal_anchors: List[Dict] = None,
) -> Dict:
    """
    Wraps OR-Tools solver. Returns {"stops": [...], "skipped": [...],
    "used_distance_fallback": bool}.

    meal_anchors: optional [{"place": <food dict>, "meal": "lunch"}, ...]. Each
    is forced into the route (is_anchor) with a time window clamped to its meal
    slot, so a restaurant the user picked is scheduled at the right time and the
    rest of the day re-optimises around it. Meal stops are flagged is_meal in the
    output.

    Bug fixed vs the previous version of this wrapper: candidates with
    status "Closed Today"/"Closed" used to be filtered out before ever
    reaching the solver, which meant they never appeared in the output
    at all -- not as a stop, not as a skip reason, just silently gone.
    The chat layer had no way to tell the user "X was closed today" because
    it never even knew X existed. Those candidates are now surfaced as
    skipped entries directly, with the real reason from check_availability.
    """
    def to_mins(t):
        h, m = map(int, t.split(":"))
        return h * 60 + m

    def to_hhmm(mins):
        mins = int(mins)
        return f"{mins // 60:02d}:{mins % 60:02d}"

    trip_start_mins = to_mins(trip_start)
    trip_end_mins   = to_mins(trip_end)

    # Determine weekday from trip_window field on first candidate, fallback today
    weekday_index = datetime.now().weekday()
    if candidates:
        tw = candidates[0].get("trip_window", "")
        day_part = tw.split(" ")[0] if tw else ""
        if day_part in WEEKDAYS:
            weekday_index = WEEKDAYS.index(day_part)

    skipped_output: List[Dict] = []

    # Candidates that are closed for the whole trip window never get a
    # window to give the solver -- surface them as skipped right here
    # instead of silently dropping them.
    usable = []
    for c in candidates:
        if c["status"] in ("Closed Today", "Closed"):
            skipped_output.append({
                "order":          None,
                "id":             c["id"],
                "name":           c["name"],
                "lat":            c["lat"],
                "lng":            c["lng"],
                "relevance":      c.get("relevance", 1.0),
                "vibe":           c.get("vibe", ""),
                "insight":        c.get("insight", ""),
                "status":         c["status"],
                "skipped_reason": c.get("availability_note", c["status"]),
            })
        else:
            usable.append(c)

    places: List[Place] = []
    for c in usable:
        # Re-resolve opening window using the engine's hours module
        # (handles special_hours that original greedy loop didn't check)
        resolved = resolve_for_day(
            c.get("closed_on", "None"),
            c.get("regular_hours", "Unknown"),
            c.get("special_hours", "None"),
            weekday_index,
        )
        window = best_window_in_span(resolved, trip_start_mins, trip_end_mins)

        places.append(Place(
            id=c["id"],
            name=c["name"],
            lat=c["lat"],
            lng=c["lng"],
            duration_min=round(float(c.get("avg_duration", 1.0)) * 60),
            relevance=c.get("relevance", 1.0),
            window=window,
            hours_source=resolved.source,
        ))

    # Selected meal restaurants: forced into the route at their meal slot.
    meal_label_by_id: Dict[str, str] = {}
    meal_place_by_id: Dict[str, Dict] = {}
    for ma in (meal_anchors or []):
        fp, meal = ma["place"], ma["meal"]
        win = meal_window_for_place(
            meal, fp.get("closed_on", "None"), fp.get("regular_hours", "Unknown"),
            fp.get("special_hours", "None"), weekday_index, trip_start_mins, trip_end_mins,
        )
        if win is None:
            # The pick can't be fitted at its meal time today -- say so plainly.
            skipped_output.append({
                "order": None, "id": fp["id"], "name": fp["name"],
                "lat": fp["lat"], "lng": fp["lng"], "relevance": 1.0,
                "vibe": fp.get("vibe", ""), "insight": fp.get("insight", ""),
                "status": "Closed", "is_meal": True, "meal": meal,
                "skipped_reason": f"can't be fitted for {MEAL_LABELS.get(meal, meal).lower()} today — it isn't open during that window",
            })
            continue
        meal_label_by_id[fp["id"]] = meal
        meal_place_by_id[fp["id"]] = fp
        places.append(Place(
            id=fp["id"], name=fp["name"], lat=fp["lat"], lng=fp["lng"],
            duration_min=min(round(float(fp.get("avg_duration", 1.0)) * 60), MEAL_DURATION_CAP_MIN),
            relevance=1.0, window=win, is_anchor=True, hours_source="regular",
        ))

    result = ortools_plan(
        home=(start_lat, start_lng),
        trip_start_min=trip_start_mins,
        trip_end_min=trip_end_mins,
        candidates=places,
        time_matrix_fn=time_matrix_with_fallback,
    )

    # Build candidate lookup by id for skipped reasons (include meal places).
    cand_by_id = {c["id"]: c for c in candidates}
    cand_by_id.update(meal_place_by_id)

    weekday_name = WEEKDAYS[weekday_index]

    stops_output = []
    for i, stop in enumerate(result.stops):
        c = cand_by_id.get(stop.place.id, {})
        meal = meal_label_by_id.get(stop.place.id)
        if meal:
            timing = f"{MEAL_LABELS[meal]} stop — slotted into your route around {to_hhmm(stop.arrival_min)} with the least detour."
        else:
            timing = _explain_timing(c, stop.place, stop, weekday_name, trip_start_mins, trip_end_mins)
        stops_output.append({
            "order":             i + 1,
            "id":                stop.place.id,
            "name":              stop.place.name,
            "lat":               stop.place.lat,
            "lng":               stop.place.lng,
            "arrive_at":         to_hhmm(stop.arrival_min),
            "visit_starts":      to_hhmm(stop.arrival_min),
            "visit_ends":        to_hhmm(stop.departure_min),
            "avg_duration_hrs":  stop.place.duration_min / 60,
            "relevance":         stop.place.relevance,
            "vibe":              c.get("vibe", ""),
            "insight":           c.get("insight", ""),
            "status":            c.get("status", ""),
            "availability_note": c.get("availability_note", ""),
            "is_meal":           bool(meal),
            "meal":              meal,
            "rating":            c.get("rating", 0.0),
            "timing_reason":     timing,
        })

    for place, reason in result.skipped:
        c = cand_by_id.get(place.id, {})
        skipped_output.append({
            "order":          None,
            "id":             place.id,
            "name":           place.name,
            "lat":            place.lat,
            "lng":            place.lng,
            "relevance":      place.relevance,
            "vibe":           c.get("vibe", ""),
            "insight":        c.get("insight", ""),
            "status":         c.get("status", "Skipped"),
            "skipped_reason": reason,
        })

    return {
        "stops": stops_output,
        "skipped": skipped_output,
        "used_distance_fallback": result.used_distance_fallback,
    }
