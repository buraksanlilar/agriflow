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
    "soil_moisture_0_to_7cm_mean",
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
      relative_humidity_2m_mean, soil_temperature_0_to_7cm_mean,
      soil_moisture_0_to_7cm_mean
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
        "temperature_2m_mean":           _get("temperature_2m_mean"),
        "precipitation_sum":             _get("precipitation_sum"),
        "relative_humidity_2m_mean":     _get("relative_humidity_2m_mean"),
        "soil_temperature_0_to_7cm_mean":_get("soil_temperature_0_to_7cm_mean"),
        "soil_moisture_0_to_7cm_mean":   _get("soil_moisture_0_to_7cm_mean"),
    }


def fetch_season(
    latitude: float,
    longitude: float,
    start_date: date,
    end_date: date,
) -> dict:
    """
    Fetch daily weather for the full season and return seasonal aggregates.

    Returns:
      elevation        — metres (from OpenMeteo)
      mean_temp        — season average daily temperature (°C)
      total_precip     — season total precipitation (mm)
      mean_humidity    — season average relative humidity (%)
      mean_soil_temp   — season average soil temperature (°C)
      mean_soil_moisture — season average soil moisture (m³/m³)
      daily            — list of daily records for charting
    """
    resp = httpx.get(
        _BASE_URL,
        params={
            "latitude":   latitude,
            "longitude":  longitude,
            "start_date": start_date.isoformat(),
            "end_date":   end_date.isoformat(),
            "daily":      ",".join(_DAILY_VARS),
            "timezone":   "UTC",
        },
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

    daily  = data["daily"]
    dates  = daily["time"]
    n      = len(dates)

    def _col(var):
        return [v for v in daily.get(var, [None] * n) if v is not None]

    def _avg(var):
        vals = _col(var)
        return round(sum(vals) / len(vals), 2) if vals else None

    def _total(var):
        vals = _col(var)
        return round(sum(vals), 1) if vals else None

    records = []
    for i in range(n):
        records.append({
            "date":           dates[i],
            "temperature":    daily.get("temperature_2m_mean",          [None]*n)[i],
            "precipitation":  daily.get("precipitation_sum",            [None]*n)[i],
            "humidity":       daily.get("relative_humidity_2m_mean",    [None]*n)[i],
            "soil_moisture":  daily.get("soil_moisture_0_to_7cm_mean",  [None]*n)[i],
        })

    return {
        "elevation":          data.get("elevation"),
        "mean_temp":          _avg("temperature_2m_mean"),
        "total_precip":       _total("precipitation_sum"),
        "mean_humidity":      _avg("relative_humidity_2m_mean"),
        "mean_soil_temp":     _avg("soil_temperature_0_to_7cm_mean"),
        "mean_soil_moisture": _avg("soil_moisture_0_to_7cm_mean"),
        "daily":              records,
    }
