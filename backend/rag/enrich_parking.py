"""
enrich_parking.py -- one-off/rerunnable data enrichment: finds the nearest
real parking amenity to every landmark in data/landmarks.csv and writes
data/parking.csv (merged into Chroma by ingest.py, same pattern as
vibe_tags.csv).

Data source: OpenStreetMap's Overpass API -- queried for real
amenity=parking nodes within PARKING_RADIUS_M of each landmark's own
coordinates. Deliberately NOT a web-search-and-guess approach: fabricating
precise GPS coordinates for a "nearby parking lot" and presenting them as
fact is exactly the kind of invented-not-computed output this codebase
avoids everywhere else (see rag/search.py's insight-grounding comment, or
agents/graph.py's schedule agent re-solving instead of guessing). Overpass
returns real, mapped, attributed locations instead.

Honest limitation, stated plainly: OpenStreetMap coverage isn't complete.
"No parking found within {PARKING_RADIUS_M}m" means "none mapped in OSM,"
not "definitely no parking exists" -- some real parking may just not be
tagged yet. The `parking_source` column exists so this provenance is never
silently lost.

Usage:
    python -m rag.enrich_parking
"""

import csv
import math
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
# ONLY overpass-api.de is trusted for a "confirmed" answer -- empirically
# checked during dev: the community mirror overpass.osm.ch returned ZERO
# parking nodes within 2km of a landmark that overpass-api.de found 3 real
# ones for (incl. a named municipal parking lot). That means a mirror's
# "zero results" can't be trusted as equivalent to the primary's "zero
# results": early in dev this script fell back to weaker mirrors whenever
# the primary got rate-limited, which meant some places would have recorded
# a false has_parking=False purely because the GOOD source was busy, not
# because there's confirmably no parking. So: retry the primary with
# backoff on failure; if it never answers, leave the row blank
# (query_failed) rather than accept a weaker source's possibly-wrong zero.
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
PARKING_RADIUS_M = 250          # confirmed with the user: a ~3 minute walk
ONSITE_THRESHOLD_M = 60         # closer than this reads as "on-site", not "nearby"
REQUEST_TIMEOUT_S = 25
SLEEP_BETWEEN_CALLS_S = 5        # baseline gap; free public instance, seen 429s even at 4s
RETRY_BACKOFFS_S = [10, 30]      # on failure (incl. 429), wait then retry the SAME primary


def haversine_m(lat1, lng1, lat2, lng2) -> float:
    R = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def query_nearby_parking(lat: float, lng: float, radius_m: int):
    """Real amenity=parking nodes within radius_m of (lat, lng), via the
    ONE trusted Overpass instance (see module-level note on why no
    fallback mirror). Returns a list of {lat, lng, name} dicts (name is
    None if OSM has no tag for it), or None if every attempt failed --
    callers must NOT interpret None as "confirmed no parking", see module
    docstring. Retries with backoff (RETRY_BACKOFFS_S) on ANY failure,
    including 429/504 -- those mean "slow down", not "this data is bad",
    so the right response is to wait and ask the same trusted source
    again, not to accept a different source's answer instead."""
    query = f'[out:json][timeout:15];node(around:{radius_m},{lat},{lng})["amenity"="parking"];out body;'
    url = OVERPASS_URL + "?" + urllib.parse.urlencode({"data": query})

    delays = [0] + RETRY_BACKOFFS_S
    for attempt, delay in enumerate(delays, start=1):
        if delay:
            print(f"    retrying in {delay}s (attempt {attempt}/{len(delays)}) ...")
            time.sleep(delay)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Tripy-parking-enrichment/1.0"})
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_S) as resp:
                import json
                data = json.loads(resp.read())
            return [
                {"lat": el["lat"], "lng": el["lon"], "name": el.get("tags", {}).get("name")}
                for el in data.get("elements", [])
            ]
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
            print(f"    attempt {attempt}/{len(delays)} failed: {e}")
    return None  # every attempt failed


def run():
    import pandas as pd
    df = pd.read_csv(DATA_DIR / "landmarks.csv")

    rows = []
    failures = []
    for _, row in df.iterrows():
        pid, name, lat, lng = str(row["id"]), row["name"], float(row["lat"]), float(row["lng"])
        print(f"[{pid}] {name} ({lat}, {lng}) ...")
        results = query_nearby_parking(lat, lng, PARKING_RADIUS_M)

        if results is None:
            failures.append((pid, name))
            rows.append({
                "id": pid, "has_parking": "", "parking_lat": "", "parking_lng": "",
                "parking_distance_m": "", "parking_name": "", "parking_source": "query_failed",
            })
            print("    ! query failed after retries -- leaving blank, not guessing")
            time.sleep(SLEEP_BETWEEN_CALLS_S)
            continue

        if not results:
            rows.append({
                "id": pid, "has_parking": False, "parking_lat": "", "parking_lng": "",
                "parking_distance_m": "", "parking_name": "", "parking_source": "osm_overpass",
            })
            print(f"    no parking mapped within {PARKING_RADIUS_M}m")
        else:
            nearest = min(results, key=lambda r: haversine_m(lat, lng, r["lat"], r["lng"]))
            dist = round(haversine_m(lat, lng, nearest["lat"], nearest["lng"]))
            rows.append({
                "id": pid, "has_parking": True,
                "parking_lat": nearest["lat"], "parking_lng": nearest["lng"],
                "parking_distance_m": dist, "parking_name": nearest["name"] or "",
                "parking_source": "osm_overpass",
            })
            label = "on-site" if dist <= ONSITE_THRESHOLD_M else f"{dist}m away"
            print(f"    found: {nearest['name'] or '(unnamed lot)'} — {label}")

        time.sleep(SLEEP_BETWEEN_CALLS_S)

    out_path = DATA_DIR / "parking.csv"
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "id", "has_parking", "parking_lat", "parking_lng",
            "parking_distance_m", "parking_name", "parking_source",
        ])
        writer.writeheader()
        writer.writerows(rows)

    found = sum(1 for r in rows if r["has_parking"] is True)
    print(f"\nWrote {out_path}: {found}/{len(rows)} places have parking mapped within {PARKING_RADIUS_M}m.")
    if failures:
        print(f"WARNING: {len(failures)} places failed to query and were left blank (not guessed): {failures}")
        print("Rerun this script to retry just those -- it's idempotent, safe to run again.")


if __name__ == "__main__":
    run()
