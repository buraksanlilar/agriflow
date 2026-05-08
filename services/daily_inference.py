"""
Daily inference pipeline.

For each field, every day:
  1. Aggregate the previous day's IoT hourly readings from SQLite.
  2. Fill gaps with OpenMeteo (precipitation, humidity, temperature).
  3. Get LAI from Sentinel-2 (cached 5 days) or fall back to IoT sensor value.
  4. Build the full feature vector and call yield_forecast.
  5. Persist the result to daily_forecasts table.

Can be run as a standalone script or called from the FastAPI lifespan scheduler.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import yaml

from database import init_db, upsert_daily_forecast
from decision_engine import yield_forecast

logger = logging.getLogger(__name__)

DB_PATH = Path("agriflow.db")


# ── IoT aggregation ──────────────────────────────────────────────────────────

def _aggregate_iot(field_id: str, target_date: date) -> tuple[dict | None, int]:
    """
    Average the IoT readings stored for field_id on target_date.
    Returns (aggregated_dict, n_readings). Returns (None, 0) if no data.
    """
    date_str = target_date.isoformat()
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
            COUNT(*)           AS n
        FROM sensor_readings
        WHERE field_id = ? AND date(timestamp) = ?
        """,
        (field_id, date_str),
    ).fetchone()
    conn.close()

    n = row[6] or 0
    if n == 0:
        return None, 0

    return {
        "mean_temp":          row[0],
        "total_precip":       row[1] or 0.0,
        "mean_humidity":      row[2],
        "mean_soil_temp":     row[3],
        "mean_soil_moisture": row[4],
        "max_lai":            row[5] or 0.0,
    }, n


# ── Single-field daily inference ─────────────────────────────────────────────

def run_for_field(field_cfg: dict, target_date: date, horizon_days: int):
    field_id = field_cfg["id"]
    crop     = field_cfg["crop"]
    lat      = field_cfg["latitude"]
    lon      = field_cfg["longitude"]

    # 1. IoT data
    iot, n_readings = _aggregate_iot(field_id, target_date)

    # 2. OpenMeteo — always try; use to fill IoT gaps
    openmeteo_used = False
    weather: dict = {}
    try:
        from services.openmeteo import fetch_daily
        weather = fetch_daily(lat, lon, target_date)
        openmeteo_used = True
        logger.info("field=%s OpenMeteo OK for %s", field_id, target_date)
    except Exception as e:
        logger.warning("field=%s OpenMeteo failed: %s", field_id, e)

    if iot is None and not weather:
        logger.warning("field=%s no data for %s — skipping", field_id, target_date)
        return

    # Merge: prefer IoT, fall back to OpenMeteo
    def _pick(iot_key, om_key, fallback=0.0):
        if iot and iot.get(iot_key) is not None:
            return iot[iot_key]
        return weather.get(om_key) or fallback

    mean_temp     = _pick("mean_temp",     "temperature_2m_mean")
    total_precip  = _pick("total_precip",  "precipitation_sum")
    mean_humidity = _pick("mean_humidity", "relative_humidity_2m_mean")
    mean_soil_temp= _pick("mean_soil_temp","soil_temperature_0_to_7cm_mean")
    mean_soil_mois= _pick("mean_soil_moisture", None, fallback=0.25)

    # 3. Sentinel-2 LAI (cached; fall back to IoT sensor value or seasonal default)
    lai_value, lai_source = None, "fallback"
    try:
        from services.sentinel_lai import fetch_lai
        lai_value, scene_date = fetch_lai(lat, lon)
        lai_source = f"sentinel2:{scene_date}"
        logger.info("field=%s Sentinel-2 LAI=%.2f (scene %s)", field_id, lai_value, scene_date)
    except Exception as e:
        logger.warning("field=%s Sentinel-2 LAI failed: %s", field_id, e)
        if iot and iot.get("max_lai"):
            lai_value = iot["max_lai"]
            lai_source = "iot"
        else:
            lai_value = field_cfg.get("wav", 2.0) / 100.0 * 3  # rough seasonal default
            lai_source = "fallback"

    # 4. Build full feature vector
    day_dt = datetime(target_date.year, target_date.month, target_date.day, tzinfo=timezone.utc)
    features = {
        "mean_temp":          mean_temp,
        "total_precip":       total_precip,
        "mean_humidity":      mean_humidity,
        "mean_soil_temp":     mean_soil_temp,
        "mean_soil_moisture": mean_soil_mois,
        "max_lai":            lai_value,
        "season_days":        float(day_dt.timetuple().tm_yday),
        "latitude":           lat,
        "longitude":          lon,
        "elevation":          field_cfg["elevation"],
        "year":               float(target_date.year),
        "WAV":                field_cfg["wav"],
    }

    predicted = yield_forecast(features, crop, horizon_days=horizon_days, field_id=field_id)
    logger.info(
        "field=%s date=%s horizon=%d yield_forecast=%.1f kg/ha (iot=%d readings, lai_src=%s, openmeteo=%s)",
        field_id, target_date, horizon_days, predicted, n_readings, lai_source, openmeteo_used,
    )

    # 5. Persist
    upsert_daily_forecast(
        field_id=field_id,
        date=target_date.isoformat(),
        horizon_days=horizon_days,
        yield_forecast_kg_ha=predicted,
        iot_readings=n_readings,
        lai_value=lai_value,
        lai_source=lai_source,
        openmeteo_used=openmeteo_used,
    )


# ── Public API ────────────────────────────────────────────────────────────────

def run_daily(config: dict, target_date: date | None = None):
    """Run inference for all fields. Defaults to yesterday's data."""
    if target_date is None:
        target_date = (datetime.now(timezone.utc) - timedelta(days=1)).date()

    horizons = [30, 60, 90]
    for field_cfg in config["fields"]:
        for h in horizons:
            try:
                run_for_field(field_cfg, target_date, h)
            except Exception as e:
                logger.exception("field=%s horizon=%d date=%s failed: %s",
                                 field_cfg["id"], h, target_date, e)


class DailyInferenceScheduler:
    """Background thread that calls run_daily once per day at a configured UTC hour."""

    def __init__(self, config: dict, run_hour_utc: int = 6):
        self._config = config
        self._run_hour = run_hour_utc
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="daily-inference"
        )

    def start(self):
        self._thread.start()
        logger.info("Daily inference scheduler started (runs at %02d:00 UTC)", self._run_hour)

    def stop(self):
        self._stop.set()

    def _loop(self):
        last_run_date: date | None = None
        while not self._stop.is_set():
            now = datetime.now(timezone.utc)
            today = now.date()
            if now.hour >= self._run_hour and last_run_date != today:
                logger.info("Daily inference triggered for %s", today - timedelta(days=1))
                try:
                    run_daily(self._config)
                except Exception as e:
                    logger.exception("Daily inference error: %s", e)
                last_run_date = today
            self._stop.wait(timeout=300)  # check every 5 minutes


# ── CLI (backfill past days) ─────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s — %(message)s")

    parser = argparse.ArgumentParser(description="Run daily inference pipeline")
    parser.add_argument("--days", type=int, default=1,
                        help="How many past days to process (default: 1 = yesterday)")
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args()

    with open(args.config) as f:
        cfg = yaml.safe_load(f)

    init_db()
    today = datetime.now(timezone.utc).date()
    for offset in range(args.days, 0, -1):
        target = today - timedelta(days=offset)
        logger.info("Processing %s …", target)
        run_daily(cfg, target_date=target)
