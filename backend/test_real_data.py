"""
Proves the new engine end-to-end against the real landmarks.csv dataset.

Scenario: a Wednesday, 09:00-18:00 trip, home base in Pattom. The
simulated "user request" is something like history/architecture/heritage
-- a tiny keyword heuristic stands in for the real vector-search
relevance score here (that part isn't being retested, only the solver
is). One place (the Zoo) is forced as an `is_anchor` stop to prove
anchor stops survive even when their simulated relevance is low.

Network note: this sandbox can't reach router.project-osrm.org (it's
not on the allowed egress list), so this run will exercise the
haversine FALLBACK path inside distance_matrix.time_matrix_with_fallback,
not the live OSRM path. The OSRM path itself follows OSRM's documented
Table API contract and should be tried against a real OSRM endpoint
(or a self-hosted one) before relying on it.
"""

import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine import hours, distance_matrix as dm
from engine.itinerary_engine import Place, plan_itinerary, _fmt

CSV_PATH = Path(__file__).resolve().parents[1] / "data" / "landmarks.csv"

HOME = (8.5150, 76.9450)  # Pattom, plausible home base
WEEKDAY_INDEX = 2  # Wednesday -> triggers Napier Museum / Sri Chitra special hours
TRIP_START, TRIP_END = 9 * 60, 18 * 60  # 09:00-18:00

HISTORY_KEYWORDS = {"history", "architecture", "heritage", "royal", "ancient", "tradition", "spiritual"}


def fake_relevance(category: str, vibe_tags: str) -> float:
    """Stand-in for a real vector-search relevance score, for this test only."""
    text = f"{category} {vibe_tags}".lower()
    hits = sum(1 for kw in HISTORY_KEYWORDS if kw in text)
    return min(1.0, 0.25 + 0.25 * hits)


def load_candidates():
    candidates = []
    with open(CSV_PATH) as f:
        for row in csv.DictReader(f):
            w = hours.resolve_for_day(row["closed_on"], row["regular_hours"], row["special_hours"], WEEKDAY_INDEX)
            window = hours.best_window_in_span(w, TRIP_START, TRIP_END)
            candidates.append(
                Place(
                    id=row["id"],
                    name=row["name"],
                    lat=float(row["lat"]),
                    lng=float(row["lng"]),
                    duration_min=round(float(row["avg_duration"]) * 60),
                    relevance=fake_relevance(row["category"], row["vibe_tags"]),
                    window=window,
                    is_anchor=(row["name"] == "Thiruvananthapuram Zoo"),
                )
            )
    return candidates


def main():
    candidates = load_candidates()
    result = plan_itinerary(HOME, TRIP_START, TRIP_END, candidates, time_matrix_fn=dm.time_matrix_with_fallback)

    print(f"Used distance fallback (no OSRM reachable here): {result.used_distance_fallback}")
    print(f"\n{'='*70}\nFINAL ROUTE ({len(result.stops)} stops)\n{'='*70}")
    for s in result.stops:
        tag = " [ANCHOR]" if s.place.is_anchor else ""
        print(f"{_fmt(s.arrival_min)}-{_fmt(s.departure_min)}  {s.place.name:35s} rel={s.place.relevance:.2f}{tag}")

    print(f"\n{'='*70}\nSKIPPED ({len(result.skipped)})\n{'='*70}")
    for p, reason in result.skipped:
        print(f"{p.name:35s} rel={p.relevance:.2f} -> {reason}")

    assert any(s.place.is_anchor for s in result.stops), "anchor stop must survive!"
    print("\nOK: anchor stop made it into the route as expected.")


if __name__ == "__main__":
    main()
