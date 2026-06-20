"""
itinerary_engine.py

Replaces the old `plan_itinerary` (greedy nearest-feasible-neighbor).

What changed and why:

  - Old: picked the next stop by `travel_time + 0.5*wait_time` only.
    Never knew how well a place matched what the user asked for, so a
    mediocre-but-close match could beat a great-but-slightly-further one.
    New: every candidate carries a `relevance` score (0..1) from the
    vector search, and the optimizer is allowed to DROP a candidate
    rather than visit it -- but dropping a highly relevant place costs
    a real penalty in the objective, dropping a low-relevance one costs
    almost nothing. So relevance now actually competes with travel time
    instead of being thrown away after the vector search.

  - Old: a single hand-rolled "is this even possible" greedy loop.
    New: Google OR-Tools' routing solver, which can look at the whole
    set of candidates at once instead of only the next step -- the
    classic weakness of nearest-neighbor (locally cheap now can be
    globally expensive later) goes away.

  - Old: no way to say "I have a movie ticket at 9pm, build around it."
    New: `is_anchor=True` on a Place makes it effectively mandatory
    (very high drop-penalty) while keeping the model always solvable.

  - Old: travel time = straight-line distance / flat 25kmh, while the
    map separately drew a real OSRM road route. Two different numbers
    for the same trip. New: the time matrix passed in here should come
    from distance_matrix.time_matrix_with_fallback(), i.e. real road
    time, so the schedule and the map agree.

Known v1 simplification (carried over, now explicit): a place that
reopens later the same day (e.g. a temple open 6-11am and 5-8pm) is
only given ONE window per day -- whichever overlaps your trip span
better. See hours.best_window_in_span().
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from ortools.constraint_solver import routing_enums_pb2, pywrapcp

from . import distance_matrix as dm

# How many "minutes of equivalent travel time" a place's drop-penalty is
# worth at relevance == 1.0. Tunable: raise it to make the planner more
# reluctant to drop good matches even at a big time cost; lower it to
# make the planner more willing to trade relevance for a tighter route.
MAX_DROP_PENALTY_MIN = 600

# Drop-penalty used for anchor stops -- large enough that the solver will
# essentially never drop one unless every single route is geometrically
# impossible (in which case it still returns *something* rather than
# failing outright).
ANCHOR_DROP_PENALTY_MIN = 100_000


@dataclass
class Place:
    id: str
    name: str
    lat: float
    lng: float
    duration_min: int
    relevance: float = 1.0          # 0..1, from the vector search ranking
    window: Optional[Tuple[int, int]] = None  # (open_min, close_min) today, already resolved
    is_anchor: bool = False         # must-visit, e.g. a booked ticket
    hours_source: str = "regular"   # "regular" | "special" | "closed" -- for explaining scheduling to the user


@dataclass
class Stop:
    place: Place
    arrival_min: int
    departure_min: int


@dataclass
class PlanResult:
    stops: List[Stop] = field(default_factory=list)
    skipped: List[Tuple[Place, str]] = field(default_factory=list)
    used_distance_fallback: bool = False


def _fmt(mins: int) -> str:
    return f"{mins // 60:02d}:{mins % 60:02d}"


def plan_itinerary(
    home: Tuple[float, float],
    trip_start_min: int,
    trip_end_min: int,
    candidates: List[Place],
    time_matrix_fn=dm.time_matrix_with_fallback,
) -> PlanResult:
    result = PlanResult()
    feasible: List[Place] = []

    # --- Pass 1: throw out anything that literally cannot work, with a reason.
    for p in candidates:
        if p.window is None:
            result.skipped.append((p, "closed for the whole time you're travelling today"))
            continue
        open_m, close_m = p.window
        if close_m - p.duration_min < open_m:
            result.skipped.append(
                (p, f"only open {_fmt(open_m)}-{_fmt(close_m)}, not enough time to fit a full visit")
            )
            continue
        feasible.append(p)

    if not feasible:
        return result

    # --- Build the travel-time matrix: node 0 = home, 1..n = places.
    points = [home] + [(p.lat, p.lng) for p in feasible]
    matrix, used_fallback = time_matrix_fn(points)
    result.used_distance_fallback = used_fallback

    n_places = len(feasible)
    sink = n_places + 1
    total_nodes = n_places + 2  # home + places + sink

    service_min = [0] + [p.duration_min for p in feasible] + [0]

    def travel(i: int, j: int) -> float:
        if i == sink or j == sink:
            return 0.0
        return matrix[i][j]

    manager = pywrapcp.RoutingIndexManager(total_nodes, 1, [0], [sink])
    routing = pywrapcp.RoutingModel(manager)

    def transit_callback(from_idx, to_idx):
        i, j = manager.IndexToNode(from_idx), manager.IndexToNode(to_idx)
        return int(round(travel(i, j) + service_min[i]))

    transit_idx = routing.RegisterTransitCallback(transit_callback)
    routing.SetArcCostEvaluatorOfAllVehicles(transit_idx)

    span = max(trip_end_min - trip_start_min, 1)
    routing.AddDimension(
        transit_idx,
        slack_max=span,           # allowed waiting (e.g. arriving before opening time)
        capacity=trip_end_min,
        fix_start_cumul_to_zero=False,
        name="Time",
    )
    time_dim = routing.GetDimensionOrDie("Time")

    # Home: fixed departure at trip start. Sink: anytime by trip end.
    # NOTE: a vehicle's start/end nodes are NOT reachable via manager.NodeToIndex()
    # once they're declared as starts/ends -- that returns -1 and silently
    # segfaults the C++ solver on first use. routing.Start()/routing.End() are
    # the only correct way to get their internal index.
    time_dim.CumulVar(routing.Start(0)).SetRange(trip_start_min, trip_start_min)
    time_dim.CumulVar(routing.End(0)).SetRange(trip_start_min, trip_end_min)

    for node, p in enumerate(feasible, start=1):
        open_m, close_m = p.window
        idx = manager.NodeToIndex(node)
        # Arrival must leave enough room before closing to finish the visit.
        time_dim.CumulVar(idx).SetRange(open_m, close_m - p.duration_min)
        penalty = ANCHOR_DROP_PENALTY_MIN if p.is_anchor else round(MAX_DROP_PENALTY_MIN * p.relevance)
        routing.AddDisjunction([idx], penalty)

    search_params = pywrapcp.DefaultRoutingSearchParameters()
    search_params.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    search_params.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    search_params.time_limit.FromSeconds(3)

    solution = routing.SolveWithParameters(search_params)
    if solution is None:
        # Should be rare since every node is droppable, but stay honest if it happens.
        for p in feasible:
            result.skipped.append((p, "optimizer could not fit this into any route today"))
        return result

    visited_nodes = set()
    index = routing.Start(0)
    route_indices = [0]  # matrix node indices in visit order, starting from home
    while not routing.IsEnd(index):
        node = manager.IndexToNode(index)
        if 1 <= node <= n_places:
            p = feasible[node - 1]
            arrival = solution.Value(time_dim.CumulVar(index))
            result.stops.append(Stop(place=p, arrival_min=arrival, departure_min=arrival + p.duration_min))
            visited_nodes.add(node)
            route_indices.append(node)
        index = solution.Value(routing.NextVar(index))

    avg_chosen_relevance = (
        sum(s.place.relevance for s in result.stops) / len(result.stops) if result.stops else 0.0
    )

    for node, p in enumerate(feasible, start=1):
        if node in visited_nodes:
            continue

        # Real marginal cost: the cheapest extra travel time it would take to
        # slot this place into the route that was actually chosen, tried at
        # every possible position (between each pair of consecutive stops,
        # and appended after the last one). This replaces a single boilerplate
        # string with a number computed from the same matrix the solver used.
        candidates_cost = []
        for k in range(len(route_indices) - 1):
            a, b = route_indices[k], route_indices[k + 1]
            cost = matrix[a][node] + service_min[node] + matrix[node][b] - matrix[a][b]
            candidates_cost.append(cost)
        last = route_indices[-1]
        candidates_cost.append(matrix[last][node] + service_min[node])  # appended at the end
        insertion_min = round(min(candidates_cost))

        if insertion_min <= 15:
            reason = (
                f"barely would have added any travel time (about {insertion_min} min), "
                f"but it wasn't as strong a match as the places you're actually visiting"
            )
        elif p.relevance >= avg_chosen_relevance:
            reason = (
                f"just as good a match as what made the cut, but fitting it in would have added "
                f"about {insertion_min} extra minutes of travel -- not enough room without "
                f"shortening another stop"
            )
        else:
            reason = (
                f"a lower-priority match than your other picks, and fitting it in would have added "
                f"about {insertion_min} minutes of travel that was better spent on your higher-relevance stops"
            )

        result.skipped.append((p, reason))

    return result
