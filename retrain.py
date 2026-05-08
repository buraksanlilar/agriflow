"""
Yield model adaptation using real IoT field data.

Strategy
--------
Yield is a season-level metric (one value per field per season), not daily.
With a single season of real data we can't fine-tune a tree model (need
variance in y across multiple seasons). Instead we apply a field-specific
bias correction: we record how far off the base model is for this field and
store a persistent offset that is automatically added to future predictions.

When enough seasons accumulate (≥ 3 are recommended), the script switches to
full fine-tuning via LightGBM's warm-start (init_model), adding new trees
on top of the pre-trained base.

Usage
-----
After each harvest, run:

  # single horizon
  python retrain.py --field field_001 --horizon 30 --actual-yield 4800

  # all horizons (if you have separate yield estimates at different crop stages)
  python retrain.py --field field_001 --horizon 30 60 90 --actual-yield 4800

The script reads real sensor readings from SQLite, builds a seasonal feature
vector, and either applies a bias correction (< 3 seasons) or fine-tunes the
model (≥ 3 seasons).
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import yaml

from decision_engine.engine import _FEATURE_ORDER, _MODELS_DIR, _get_crop_te, yield_forecast
from decision_engine import reload_all_models

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s — %(message)s",
)
logger = logging.getLogger(__name__)

DB_PATH = Path("agriflow.db")
OFFSETS_PATH = Path("decision_engine/models/field_yield_offsets.json")
HISTORY_PATH = Path("decision_engine/models/field_season_history.json")

MIN_SEASONS_FOR_FINETUNE = 3


# ── Persistence helpers ──────────────────────────────────────────────────────

def _load_json(path: Path) -> dict:
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return {}


def _save_json(path: Path, data: dict):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


# ── Feature building ─────────────────────────────────────────────────────────

def load_field_config(field_id: str) -> dict:
    with open("config.yaml") as f:
        cfg = yaml.safe_load(f)
    for field in cfg["fields"]:
        if field["id"] == field_id:
            return field
    raise KeyError(f"Field '{field_id}' not found in config.yaml")


def build_seasonal_features(field_id: str, field_cfg: dict) -> dict:
    """Aggregate all sensor readings for this field into one seasonal feature vector."""
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        """
        SELECT
            AVG(air_temp)      AS mean_temp,
            SUM(precipitation) AS total_precip,
            AVG(air_humidity)  AS mean_humidity,
            AVG(soil_temp)     AS mean_soil_temp,
            AVG(soil_moisture) AS mean_soil_moisture,
            MAX(lai)           AS max_lai,
            COUNT(*)           AS n_readings,
            MIN(timestamp)     AS first_ts,
            MAX(timestamp)     AS last_ts
        FROM sensor_readings
        WHERE field_id = ?
        """,
        (field_id,),
    ).fetchone()
    conn.close()

    if not row or row[6] == 0:
        raise ValueError(f"No sensor readings found for field '{field_id}'.")

    # Use midpoint of the season for season_days / year
    first_dt = datetime.fromisoformat(row[7].replace("Z", "+00:00"))
    last_dt  = datetime.fromisoformat(row[8].replace("Z", "+00:00"))
    mid_dt   = first_dt + (last_dt - first_dt) / 2

    features = {
        "mean_temp":          row[0],
        "total_precip":       row[1] or 0.0,
        "mean_humidity":      row[2],
        "mean_soil_temp":     row[3],
        "mean_soil_moisture": row[4],
        "max_lai":            row[5] or 0.0,
        "season_days":        float(mid_dt.timetuple().tm_yday),
        "latitude":           field_cfg["latitude"],
        "longitude":          field_cfg["longitude"],
        "elevation":          field_cfg["elevation"],
        "year":               float(mid_dt.year),
        "WAV":                field_cfg["wav"],
        "crop_te":            _get_crop_te()[field_cfg["crop"]],
    }
    logger.info("Seasonal features built from %d sensor readings (%s → %s)", row[6], row[7][:10], row[8][:10])
    return features


# ── Bias correction (single / few seasons) ──────────────────────────────────

def apply_bias_correction(field_id: str, horizon_days: int, features: dict, actual_yield: float):
    """Record actual vs predicted and store a per-field offset."""
    predicted = yield_forecast(features, _crop_from_features(features), horizon_days)
    error = actual_yield - predicted
    logger.info(
        "day%d  predicted=%.1f  actual=%.1f  error=%.1f kg/ha",
        horizon_days, predicted, actual_yield, error,
    )

    offsets = _load_json(OFFSETS_PATH)
    key = f"{field_id}__day{horizon_days}"
    offsets[key] = round(error, 2)
    _save_json(OFFSETS_PATH, offsets)
    logger.info("Bias correction saved: %s → %+.1f kg/ha", key, error)


def _crop_from_features(features: dict) -> str:
    """Reverse-lookup crop name from crop_te value."""
    te_map = _get_crop_te()
    target = features["crop_te"]
    for crop, val in te_map.items():
        if abs(val - target) < 1e-9:
            return crop
    raise ValueError("Cannot resolve crop from crop_te")


# ── Full fine-tuning (≥ 3 seasons) ──────────────────────────────────────────

def fine_tune(field_id: str, horizon_days: int, season_rows: list[dict], n_new_trees: int = 50):
    import lightgbm as lgb

    X = pd.DataFrame(season_rows)[_FEATURE_ORDER]
    y = np.array([r["actual_yield"] for r in season_rows])

    model_path = _MODELS_DIR / f"iot_lgbm_day{horizon_days}.joblib"
    base_model = joblib.load(model_path)

    logger.info(
        "Fine-tuning day%d model with %d seasons of real data (+%d trees)",
        horizon_days, len(X), n_new_trees,
    )

    new_model = lgb.LGBMRegressor(
        n_estimators=n_new_trees,
        learning_rate=0.01,
        num_leaves=base_model.num_leaves,
        min_child_samples=1,   # allow splits on small real-world datasets
        random_state=42,
    )
    new_model.fit(X, y, init_model=base_model.booster_)

    backup_path = model_path.with_suffix(".joblib.bak")
    joblib.dump(base_model, backup_path)
    joblib.dump(new_model, model_path)
    logger.info("Updated model saved → %s  (backup → %s)", model_path.name, backup_path.name)

    reload_all_models()
    logger.info("Engine hot-reloaded.")


# ── Orchestrator ─────────────────────────────────────────────────────────────

def adapt(field_id: str, horizon_days: int, actual_yield: float, n_new_trees: int):
    field_cfg = load_field_config(field_id)
    features = build_seasonal_features(field_id, field_cfg)

    # Load season history
    history = _load_json(HISTORY_PATH)
    key = f"{field_id}__day{horizon_days}"
    seasons: list[dict] = history.get(key, [])

    # Append this season
    season_record = {**{k: features[k] for k in _FEATURE_ORDER}, "actual_yield": actual_yield}
    seasons.append(season_record)
    history[key] = seasons
    _save_json(HISTORY_PATH, history)

    n_seasons = len(seasons)
    logger.info("Season history for %s: %d season(s)", key, n_seasons)

    if n_seasons < MIN_SEASONS_FOR_FINETUNE:
        logger.info(
            "Only %d season(s) available (need %d for fine-tuning). Applying bias correction instead.",
            n_seasons, MIN_SEASONS_FOR_FINETUNE,
        )
        apply_bias_correction(field_id, horizon_days, features, actual_yield)
    else:
        logger.info("%d seasons available — switching to full fine-tuning.", n_seasons)
        fine_tune(field_id, horizon_days, seasons, n_new_trees=n_new_trees)


# ── CLI ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Adapt yield forecast models with real harvest data")
    parser.add_argument("--field", default="field_001")
    parser.add_argument("--horizon", type=int, nargs="+", choices=[30, 60, 90], default=[30])
    parser.add_argument("--actual-yield", type=float, required=True, metavar="KG_HA",
                        help="Actual measured yield at harvest (kg/ha)")
    parser.add_argument("--new-trees", type=int, default=50)
    args = parser.parse_args()

    for h in args.horizon:
        adapt(
            field_id=args.field,
            horizon_days=h,
            actual_yield=args.actual_yield,
            n_new_trees=args.new_trees,
        )

    logger.info("Done.")


if __name__ == "__main__":
    main()
