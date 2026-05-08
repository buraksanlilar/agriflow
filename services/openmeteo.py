"""
OpenMeteo daily weather fetcher (free, no API key required).
Fetches historical daily weather for a given lat/lon and date range.
"""

from __future__ import annotations

from datetime import date, timedelta

import httpx

_BASE_URL = "https://archive-api.open-meteo.com/v1/archive"

_DAILY_VARS = [
    "temperature_2m_mean",
    "precipitation_sum",
    "relative_humidity_2m_mean",
    "soil_temperature_0_to_7cm_mean",
]


def fetch_daily(
    latitude: float,
    longitude: float,
    target_date: date,
) -> dict:
    """
    Return daily weather for target_date.

    Returns a dict with keys:
      temperature_2m_mean, precipitation_sum,
      relative_humidity_2m_mean, soil_temperature_0_to_7cm_mean
    Values are floats (or None if unavailable for that day).
    """
    # Archive API requires a small range; fetch ±1 day and pick target
    start = (target_date - timedelta(days=1)).isoformat()
    end = target_date.isoformat()

    resp = httpx.get(
        _BASE_URL,
        params={
            "latitude":   latitude,
            "longitude":  longitude,
            "start_date": start,
            "end_date":   end,
            "daily":      ",".join(_DAILY_VARS),
            "timezone":   "UTC",
        },
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()

    daily = data["daily"]
    dates = daily["time"]

    target_str = target_date.isoformat()
    idx = dates.index(target_str) if target_str in dates else -1

    def _get(var: str) -> float | None:
        values = daily.get(var, [])
        return values[idx] if idx >= 0 and idx < len(values) else None

    return {
        "temperature_2m_mean":          _get("temperature_2m_mean"),
        "precipitation_sum":             _get("precipitation_sum"),
        "relative_humidity_2m_mean":     _get("relative_humidity_2m_mean"),
        "soil_temperature_0_to_7cm_mean":_get("soil_temperature_0_to_7cm_mean"),
    }
