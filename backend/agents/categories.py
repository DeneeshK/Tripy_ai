"""
categories.py

Weather-aware bias for the planning agent. Grounded in the actual category
taxonomy already in landmarks.csv -- no new schema, no invented field, just
a judgment call on which existing categories are sheltered from weather and
which aren't.

This is a real limitation worth being upfront about: it's a city-wide
indoor/outdoor heuristic, not per-location microclimate routing. Open-Meteo's
free tier forecasts at roughly km-scale resolution, and Trivandrum is a single
small city -- there's no meaningful "this road is rainy, this one isn't"
signal to act on at that resolution. What IS meaningful and implemented here:
when rain is forecast during the remaining part of the day, shift the
remaining stops toward places where rain doesn't ruin the visit.
"""

INDOOR_CATEGORIES = {"Art", "Cafe", "Lounge", "Museum", "Restaurant", "Science"}
OUTDOOR_CATEGORIES = {"Beach", "Nature", "Park", "Zoo", "Theme Park", "Landmark"}
# Heritage, Temple, Hidden Gem, Market, Religious are mixed (often partly open-air,
# e.g. temple courtyards) -- left unbiased rather than guessing wrong in either direction.

INDOOR_BOOST = 1.4
OUTDOOR_PENALTY = 0.5


def apply_weather_bias(candidates: list, prefer_indoor: bool) -> list:
    """
    Adjusts each candidate's relevance score in place toward indoor options
    when prefer_indoor is True. This directly shifts the itinerary engine's
    choices, since relevance feeds its drop-penalty calculation -- a real,
    measurable effect, not cosmetic.
    """
    if not prefer_indoor:
        return candidates
    for c in candidates:
        cat = c.get("category", "")
        if cat in INDOOR_CATEGORIES:
            c["relevance"] = min(1.0, c["relevance"] * INDOOR_BOOST)
        elif cat in OUTDOOR_CATEGORIES:
            c["relevance"] = c["relevance"] * OUTDOOR_PENALTY
    return candidates
