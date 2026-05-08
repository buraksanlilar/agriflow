"""
Irrigation decision engine for IoT-based crop management.

Core functions:
  irrigation_decision  — compare soil moisture against crop's daily profile
  irrigation_amount    — compute irrigation volume in litres
  yield_forecast       — LightGBM prediction for 30/60/90-day horizons
  make_actuator_command — build MQTT pump payload
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

import joblib
import pandas as pd

_HERE = Path(__file__).parent
_PROFILE_PATH = _HERE / "profiles" / "crop_daily_moisture_profile.json"
_TE_MAP_PATH = _HERE / "profiles" / "crop_te_map.json"
_MODELS_DIR = _HERE / "models"

Alarm = Literal["CRITICAL", "IRRIGATE", "NORMAL", "STOP"]

_FEATURE_ORDER = [
    "mean_temp",
    "total_precip",
    "mean_humidity",
    "mean_soil_temp",
    "mean_soil_moisture",
    "max_lai",
    "season_days",
    "latitude",
    "longitude",
    "elevation",
    "year",
    "WAV",
    "crop_te",
]

# Lazy-loaded singletons
_profile: dict | None = None
_crop_te: dict | None = None
_models: dict[int, object] = {}


def _get_profile() -> dict:
    global _profile
    if _profile is None:
        with open(_PROFILE_PATH) as f:
            _profile = json.load(f)
    return _profile


def _get_crop_te() -> dict:
    global _crop_te
    if _crop_te is None:
        with open(_TE_MAP_PATH) as f:
            raw = json.load(f)
        _crop_te = {k: v for k, v in raw.items() if not k.startswith("__")}
    return _crop_te


def _get_model(horizon_days: int):
    if horizon_days not in _models:
        _reload_model(horizon_days)
    return _models[horizon_days]


def reload_all_models():
    """Force-reload all cached models from disk (call after retraining)."""
    for h in list(_models.keys()):
        _reload_model(h)


def _reload_model(horizon_days: int):
    path = _MODELS_DIR / f"iot_lgbm_day{horizon_days}.joblib"
    if not path.exists():
        raise FileNotFoundError(f"Model not found: {path}")
    _models[horizon_days] = joblib.load(path)
    import logging
    logging.getLogger(__name__).info("Model loaded: %s", path.name)


def irrigation_decision(crop: str, day_of_year: int, soil_moisture: float) -> dict:
    """
    Compare soil_moisture (m³/m³) against the crop's daily profile thresholds.

    Returns a dict with:
      alarm        — "CRITICAL" | "IRRIGATE" | "NORMAL" | "STOP"
      soil_moisture — the input value
      thresholds   — {critical, low, optimal, high, upper} for that day
      delta_sm     — deficit vs. optimal (positive = dry, negative = surplus)
    """
    profile = _get_profile()
    if crop not in profile:
        raise KeyError(f"Unknown crop '{crop}'. Available: {sorted(profile)}")

    cp = profile[crop]
    # days list is 0-indexed from 0 to max_day (366); clamp to valid range
    idx = max(0, min(int(day_of_year), cp["max_day"]))

    thresholds = {
        "critical": cp["critical_sm"][idx],
        "low":      cp["low_sm"][idx],
        "optimal":  cp["optimal_sm"][idx],
        "high":     cp["high_sm"][idx],
        "upper":    cp["upper_sm"][idx],
    }

    sm = soil_moisture
    if sm <= thresholds["critical"]:
        alarm: Alarm = "CRITICAL"
    elif sm <= thresholds["low"]:
        alarm = "IRRIGATE"
    elif sm <= thresholds["high"]:
        alarm = "NORMAL"
    else:
        alarm = "STOP"

    return {
        "alarm": alarm,
        "soil_moisture": sm,
        "thresholds": thresholds,
        "delta_sm": max(0.0, thresholds["optimal"] - sm),
    }


def irrigation_amount(delta_sm: float, field_area_m2: float, root_depth: float) -> float:
    """
    Volume of water needed to bring soil moisture back to optimal.

    delta_sm      — moisture deficit in m³/m³  (from irrigation_decision)
    field_area_m2 — field area in m²
    root_depth    — root zone depth in m

    Returns litres.
    """
    return delta_sm * field_area_m2 * root_depth * 1000.0


def _load_field_offsets() -> dict:
    import json
    path = _HERE / "models" / "field_yield_offsets.json"
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return {}


def yield_forecast(
    features: dict,
    crop: str,
    horizon_days: int = 30,
    field_id: str | None = None,
) -> float:
    """
    Predict yield (kg/ha) with the IoT LightGBM model.

    features must supply all keys in _FEATURE_ORDER except crop_te.
    crop_te is resolved automatically from the trained encoding map.
    horizon_days must be 30, 60, or 90.
    field_id is optional — if provided, applies a bias correction offset
    saved by retrain.py after the first real harvest.
    """
    if horizon_days not in (30, 60, 90):
        raise ValueError("horizon_days must be 30, 60, or 90")

    row = dict(features)
    row["crop_te"] = _get_crop_te()[crop]

    missing = [k for k in _FEATURE_ORDER if k not in row]
    if missing:
        raise ValueError(f"Missing features: {missing}")

    X = pd.DataFrame([{k: row[k] for k in _FEATURE_ORDER}])
    prediction = float(_get_model(horizon_days).predict(X)[0])

    if field_id:
        offset = _load_field_offsets().get(f"{field_id}__day{horizon_days}", 0.0)
        if offset:
            import logging
            logging.getLogger(__name__).debug(
                "Applying bias offset %+.1f kg/ha for %s day%d", offset, field_id, horizon_days
            )
            prediction += offset

    return prediction


def make_actuator_command(
    field_id: str,
    alarm: str,
    volume_litres: float,
    yield_kg_ha: float,
) -> dict:
    """
    Build the MQTT actuator payload for the pump topic.

    Pump turns ON for CRITICAL or IRRIGATE alarms, OFF otherwise.
    """
    return {
        "field_id": field_id,
        "pump": "ON" if alarm in ("CRITICAL", "IRRIGATE") else "OFF",
        "alarm": alarm,
        "volume_litres": round(volume_litres, 1),
        "yield_forecast_kg_ha": round(yield_kg_ha, 1),
    }
