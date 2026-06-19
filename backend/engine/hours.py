"""
hours.py

Turns the human-readable opening-hours fields from landmarks.csv into
plain (open_minute, close_minute) windows, where minutes are counted
from midnight (0-1439). Everything downstream (the solver) only ever
deals with integers, never strings.

Expected CSV fields per place:
    closed_on      "None" | a weekday name, e.g. "Monday"
    regular_hours  "HH:MM-HH:MM" or multiple periods separated by ";"
                   e.g. "03:30-12:00; 17:00-19:20"
    special_hours  "None" | "Wed:13:00-16:45" (a single weekday override)

This is intentionally a small, dependency-free module so it can be
unit-tested on its own.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import List, Optional

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
_WEEKDAY_ABBR = {d[:3]: d for d in WEEKDAYS}


def _hhmm_to_min(hhmm: str) -> int:
    h, m = hhmm.strip().split(":")
    return int(h) * 60 + int(m)


def _parse_periods(period_str: str) -> List[tuple]:
    """'03:30-12:00; 17:00-19:20' -> [(210, 720), (1020, 1160)]"""
    periods = []
    for chunk in period_str.split(";"):
        chunk = chunk.strip()
        if not chunk or chunk.lower() == "none":
            continue
        start_s, end_s = chunk.split("-")
        periods.append((_hhmm_to_min(start_s), _hhmm_to_min(end_s)))
    return periods


def _weekday_name(short_or_long: str) -> Optional[str]:
    short_or_long = short_or_long.strip()
    if short_or_long in WEEKDAYS:
        return short_or_long
    return _WEEKDAY_ABBR.get(short_or_long[:3])


@dataclass
class OpeningWindows:
    closed_today: bool
    periods_min: List[tuple]  # list of (open_min, close_min), may be empty if closed


def resolve_for_day(
    closed_on: str,
    regular_hours: str,
    special_hours: str,
    weekday_index: int,  # 0=Monday ... 6=Sunday, matches datetime.weekday()
) -> OpeningWindows:
    """Resolve which opening periods apply on a given weekday."""
    today_name = WEEKDAYS[weekday_index]

    closed_on = (closed_on or "").strip()
    if closed_on and closed_on.lower() != "none":
        closed_day = _weekday_name(closed_on)
        if closed_day == today_name:
            return OpeningWindows(closed_today=True, periods_min=[])

    special_hours = (special_hours or "").strip()
    if special_hours and special_hours.lower() != "none":
        # format: "Wed:13:00-16:45" (one override; multiple separated by ",")
        for override in special_hours.split(","):
            override = override.strip()
            if ":" not in override:
                continue
            day_part, _, hours_part = override.partition(":")
            # day_part may itself contain no further colon, hours_part is "13:00-16:45"
            # rejoin in case hours_part lost a leading "13" due to partition on first ':'
            day_name = _weekday_name(day_part)
            if day_name == today_name:
                full_hours = override.split(":", 1)[1].strip()
                return OpeningWindows(closed_today=False, periods_min=_parse_periods(full_hours))

    return OpeningWindows(closed_today=False, periods_min=_parse_periods(regular_hours))


def best_window_in_span(
    windows: OpeningWindows, span_start: int, span_end: int
) -> Optional[tuple]:
    """
    Of the day's opening periods, pick the one with the largest overlap
    with [span_start, span_end] (the user's trip window). Returns None if
    nothing overlaps at all (place is effectively unreachable today).

    v1 simplification: a place gets ONE window per day in the solver, even
    if it technically reopens later (e.g. a temple open morning and evening).
    If the trip window spans both, only the better-overlapping one is used.
    """
    if windows.closed_today or not windows.periods_min:
        return None

    best = None
    best_overlap = 0
    for (o, c) in windows.periods_min:
        overlap = min(c, span_end) - max(o, span_start)
        if overlap > best_overlap:
            best_overlap = overlap
            best = (max(o, span_start), min(c, span_end))
    return best
