"""
weather.py

The Weather Monitoring Agent's tool. Calls Open-Meteo (free, no API key:
https://open-meteo.com/en/docs) and checks whether rain is forecast during
any of the trip's still-upcoming stops.

NOT LIVE-TESTED: this sandbox has no network route to api.open-meteo.com
(not on its egress allowlist), same situation as OSRM and Groq earlier in
this project. Written directly against Open-Meteo's documented parameter
names and response shape (verified via their docs site just before writing
this, not from memory) -- run fetch_hourly_forecast() on a machine with
real internet access to confirm the live call before relying on it.
"""

from __future__ import annotations
import json
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Tuple

OPEN_METEO_BASE = "https://api.open-meteo.com/v1/forecast"

# WMO weather codes that mean "rain or worse is actually happening" --
# straight from Open-Meteo's docs table, not guessed.
RAIN_CODES = {51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82, 95, 96, 99}
THUNDERSTORM_CODES = {95, 96, 99}

WMO_DESCRIPTIONS = {
    0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast",
    45: "fog", 48: "depositing rime fog",
    51: "light drizzle", 53: "moderate drizzle", 55: "dense drizzle",
    56: "light freezing drizzle", 57: "dense freezing drizzle",
    61: "slight rain", 63: "moderate rain", 65: "heavy rain",
    66: "light freezing rain", 67: "heavy freezing rain",
    71: "slight snow", 73: "moderate snow", 75: "heavy snow", 77: "snow grains",
    80: "slight rain showers", 81: "moderate rain showers", 82: "violent rain showers",
    85: "slight snow showers", 86: "heavy snow showers",
    95: "thunderstorm", 96: "thunderstorm with slight hail", 99: "thunderstorm with heavy hail",
}

PRECIP_PROBABILITY_THRESHOLD = 55  # percent; above this, flag a warning


def describe_weather_code(code: int) -> str:
    return WMO_DESCRIPTIONS.get(code, f"weather code {code}")


def fetch_hourly_forecast(lat: float, lng: float, target_date=None, timeout: float = 8.0) -> dict:
    """
    Real Open-Meteo call. Raises on any network/parse failure -- callers
    should catch and degrade gracefully (see check_weather_for_stops).

    target_date (a date, datetime, or 'YYYY-MM-DD' string) pins the forecast to
    the trip's actual day -- so a trip planned for tomorrow (or a date up to ~16
    days out) is checked against THAT day's forecast, not today's. When omitted,
    it falls back to a 2-day window (today + tomorrow).
    """
    if target_date is not None:
        ds = target_date if isinstance(target_date, str) else target_date.strftime("%Y-%m-%d")
        window = f"&start_date={ds}&end_date={ds}"
    else:
        window = "&forecast_days=2"
    params = (
        f"latitude={lat:.6f}&longitude={lng:.6f}"
        f"&hourly=precipitation_probability,weather_code,temperature_2m"
        f"{window}&timezone=auto"
    )
    url = f"{OPEN_METEO_BASE}?{params}"
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _nearest_hour_index(times: List[str], target_dt: datetime) -> Optional[int]:
    """Find the forecast hour closest to target_dt. times are ISO8601 local strings."""
    best_idx, best_diff = None, None
    for i, t in enumerate(times):
        try:
            ts = datetime.fromisoformat(t)
        except ValueError:
            continue
        diff = abs((ts - target_dt).total_seconds())
        if best_diff is None or diff < best_diff:
            best_idx, best_diff = i, diff
    return best_idx


@dataclass
class WeatherWarning:
    stop_name: str
    arrival_time: str       # "HH:MM"
    weather_code: int
    description: str
    precipitation_probability: float
    is_thunderstorm: bool


def check_weather_for_stops(
    upcoming_stops: List[dict],   # each: {"name": str, "lat": float, "lng": float, "arrive_at": "HH:MM"}
    trip_date: datetime,
) -> Tuple[List[WeatherWarning], bool, Optional[str]]:
    """
    Returns (warnings, fetch_failed, error_message).

    Checks the forecast at each upcoming stop's own coordinates around its
    scheduled arrival time -- real per-stop, per-time granularity, which is
    meaningful (different hours can have different rain risk even in one
    city). What this does NOT claim to do is route-level "this specific road
    is rainy" precision -- Open-Meteo's free tier doesn't support that, and
    no honest implementation of this feature should pretend otherwise.
    """
    warnings: List[WeatherWarning] = []

    for stop in upcoming_stops:
        try:
            h, m = map(int, stop["arrive_at"].split(":"))
            target_dt = trip_date.replace(hour=h, minute=m, second=0, microsecond=0)
            data = fetch_hourly_forecast(stop["lat"], stop["lng"], target_date=trip_date.date())
        except Exception as e:
            # One stop's forecast failing shouldn't take down the whole check --
            # but the caller needs to know at least one lookup didn't work.
            return warnings, True, f"Couldn't reach the weather service: {e}"

        hourly = data.get("hourly", {})
        times = hourly.get("time", [])
        idx = _nearest_hour_index(times, target_dt)
        if idx is None:
            continue

        code = hourly.get("weather_code", [None])[idx] if idx < len(hourly.get("weather_code", [])) else None
        prob = hourly.get("precipitation_probability", [0])[idx] if idx < len(hourly.get("precipitation_probability", [])) else 0

        if code in RAIN_CODES or (prob is not None and prob >= PRECIP_PROBABILITY_THRESHOLD):
            warnings.append(WeatherWarning(
                stop_name=stop["name"],
                arrival_time=stop["arrive_at"],
                weather_code=code or 0,
                description=describe_weather_code(code or 0),
                precipitation_probability=prob or 0,
                is_thunderstorm=(code in THUNDERSTORM_CODES),
            ))

    return warnings, False, None


# ---------------------------------------------------------------------------
# Always-on widget data: current conditions + EVERY stop's forecast (not just
# the rainy ones). Powers the persistent weather box on the map.
# ---------------------------------------------------------------------------

def fetch_current(lat: float, lng: float, timeout: float = 8.0) -> dict:
    """Current conditions at a point (the user's live location)."""
    params = (
        f"latitude={lat:.6f}&longitude={lng:.6f}"
        f"&current=temperature_2m,weather_code,precipitation&timezone=auto"
    )
    with urllib.request.urlopen(f"{OPEN_METEO_BASE}?{params}", timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    cur = data.get("current", {})
    code = cur.get("weather_code")
    return {
        "temperature":  cur.get("temperature_2m"),
        "weather_code": code,
        "description":  describe_weather_code(code) if code is not None else "unknown",
        "precipitation": cur.get("precipitation"),
        "is_wet":       bool(code in RAIN_CODES) if code is not None else False,
        "is_thunderstorm": bool(code in THUNDERSTORM_CODES) if code is not None else False,
    }


def conditions_for_stops(stops: List[dict], trip_date: datetime) -> Tuple[List[dict], bool, Optional[str]]:
    """Per-stop forecast for ALL stops (the widget shows the full day, not only
    the warnings). Returns (rows, fetch_failed, error_message)."""
    rows: List[dict] = []
    for stop in stops:
        try:
            h, m = map(int, stop["arrive_at"].split(":"))
            target_dt = trip_date.replace(hour=h, minute=m, second=0, microsecond=0)
            data = fetch_hourly_forecast(stop["lat"], stop["lng"], target_date=trip_date.date())
        except Exception as e:
            return rows, True, f"Couldn't reach the weather service: {e}"

        hourly = data.get("hourly", {})
        times = hourly.get("time", [])
        idx = _nearest_hour_index(times, target_dt)
        if idx is None:
            continue
        code = hourly.get("weather_code", [None])[idx] if idx < len(hourly.get("weather_code", [])) else None
        prob = hourly.get("precipitation_probability", [0])[idx] if idx < len(hourly.get("precipitation_probability", [])) else 0
        temp = hourly.get("temperature_2m", [None])[idx] if idx < len(hourly.get("temperature_2m", [])) else None
        rows.append({
            "stop_name": stop["name"],
            "arrival_time": stop["arrive_at"],
            "weather_code": code or 0,
            "description": describe_weather_code(code or 0),
            "precipitation_probability": prob or 0,
            "temperature": temp,
            "is_thunderstorm": bool(code in THUNDERSTORM_CODES),
            "is_warning": bool(code in RAIN_CODES or (prob is not None and prob >= PRECIP_PROBABILITY_THRESHOLD)),
        })
    return rows, False, None
