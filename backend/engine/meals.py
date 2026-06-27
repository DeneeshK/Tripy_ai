"""
meals.py

Meal-awareness for the planner: meal time-slots, dietary eligibility, and a
near-route ranking used to suggest restaurants that fit the day WITHOUT
sending the user on a detour.

Design notes:
  - Meal slots are fixed defaults intersected with the user's trip window.
    A restaurant is eligible for a meal only if its real opening window
    (resolved via engine.hours) overlaps that slot.
  - "Near the route" = the marginal travel-time detour of slotting the place
    into the already-planned route at its cheapest position. This is the same
    idea already used to explain skipped stops in itinerary_engine.py; here it
    ranks restaurant suggestions instead.
  - A vegetarian must never be shown a pure non-veg place; "both" (mixed)
    places are shown WITH an explanation so the user decides. A non-veg user
    has no restriction (best fit near the route wins) -- per product decision.
"""
from __future__ import annotations
from typing import List, Tuple, Optional

from .hours import resolve_for_day, best_window_in_span

# Meal -> (slot_open_min, slot_close_min), minutes from midnight.
MEAL_SLOTS = {
    "breakfast": (8 * 60,  9 * 60 + 30),   # 08:00-09:30
    "lunch":     (12 * 60 + 30, 14 * 60),  # 12:30-14:00
    "dinner":    (19 * 60, 20 * 60 + 30),  # 19:00-20:30  (a.k.a. "supper")
}
# User-facing label (the user calls dinner "supper").
MEAL_LABELS = {"breakfast": "Breakfast", "lunch": "Lunch", "dinner": "Supper"}
MEAL_ORDER = ["breakfast", "lunch", "dinner"]

MEAL_DURATION_CAP_MIN = 60      # don't let a 1.5h fine-dining avg_duration blow up the day
N_SUGGESTIONS = 4               # restaurants suggested per meal

FOOD_CATEGORIES = {"Restaurant", "Cafe", "Lounge"}

DIET_LABEL = {"veg": "Pure veg", "nonveg": "Non-veg", "both": "Veg & Non-veg"}
DIET_NOTE = {
    "veg":    "Pure vegetarian kitchen.",
    "nonveg": "Serves non-vegetarian dishes.",
    "both":   "Serves both veg and non-veg — if you're comfortable with that, it's worth a look.",
}


def diet_eligible(place_diet: str, pref: Optional[str]) -> bool:
    """Is a place acceptable for the user's dietary preference?

    pref "veg"   -> only pure-veg or mixed (the user can order veg there).
    pref "nonveg" / None -> no restriction (best fit near the route wins).
    """
    if pref == "veg":
        return place_diet in ("veg", "both")
    return True


def meal_slot_in_trip(meal: str, trip_start_min: int, trip_end_min: int) -> Optional[Tuple[int, int]]:
    """The meal's slot clipped to the trip window, or None if it doesn't fit at all."""
    s, e = MEAL_SLOTS[meal]
    s2, e2 = max(s, trip_start_min), min(e, trip_end_min)
    return (s2, e2) if s2 < e2 else None


def meal_window_for_place(
    meal: str, closed_on: str, regular_hours: str, special_hours: str,
    weekday_index: int, trip_start_min: int, trip_end_min: int,
) -> Optional[Tuple[int, int]]:
    """The actual open window of a place during a meal slot, or None if it's
    shut / outside the slot. Reuses engine.hours so day-specific and split
    hours are honoured."""
    slot = meal_slot_in_trip(meal, trip_start_min, trip_end_min)
    if slot is None:
        return None
    resolved = resolve_for_day(closed_on, regular_hours, special_hours, weekday_index)
    return best_window_in_span(resolved, slot[0], slot[1])


def min_route_detour(route_indices: List[int], matrix, node: int) -> float:
    """Cheapest extra travel time (minutes) to slot `node` into the route at
    its best position -- between any consecutive pair, or appended at the end.
    Mirrors the marginal-insertion-cost logic in itinerary_engine.plan_itinerary."""
    best = None
    for k in range(len(route_indices) - 1):
        a, b = route_indices[k], route_indices[k + 1]
        d = matrix[a][node] + matrix[node][b] - matrix[a][b]
        best = d if best is None else min(best, d)
    last = route_indices[-1]
    d_end = matrix[last][node]      # appended after the final stop
    best = d_end if best is None else min(best, d_end)
    return best


def rank_by_route_proximity(route_points, candidates, matrix_fn):
    """Return [(detour_min, candidate), ...] sorted by least detour.

    route_points: [(lat,lng), ...] starting at home then each stop in order.
    candidates:   [{... "lat","lng" ...}, ...]
    matrix_fn:    distance_matrix.time_matrix_with_fallback-style callable.
    """
    if not candidates:
        return []
    points = list(route_points) + [(c["lat"], c["lng"]) for c in candidates]
    matrix, _used_fallback = matrix_fn(points)
    n_route = len(route_points)
    route_indices = list(range(n_route))
    ranked = []
    for i, c in enumerate(candidates):
        detour = min_route_detour(route_indices, matrix, n_route + i)
        ranked.append((round(detour, 1), c))
    ranked.sort(key=lambda x: x[0])
    return ranked


def suggestion_card(place: dict, detour_min: float, added: bool) -> dict:
    """Shape one restaurant into the card the frontend renders."""
    diet = place.get("diet", "na")
    return {
        "id":         place["id"],
        "name":       place["name"],
        "lat":        place["lat"],
        "lng":        place["lng"],
        "category":   place.get("category", ""),
        "diet":       diet,
        "diet_label": DIET_LABEL.get(diet, ""),
        "diet_note":  DIET_NOTE.get(diet, ""),
        "rating":     place.get("rating", 0.0),
        "vibe":       place.get("vibe", ""),
        "insight":    place.get("insight", ""),
        "detour_min": detour_min,
        "added":      added,
    }
