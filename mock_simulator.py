"""
Mock IoT sensor simulator for AgriFlow.

Generates realistic sensor readings and writes them directly to SQLite,
bypassing MQTT entirely. No broker or physical devices required.

Modes
-----
  python mock_simulator.py backfill          # Fill last 7 days (24 readings/day)
  python mock_simulator.py backfill --days 30
  python mock_simulator.py live              # Stream one reading every 5 seconds
  python mock_simulator.py live --interval 2
"""

from __future__ import annotations

import argparse
import logging
import math
import random
import sys
import time
from datetime import datetime, timedelta, timezone

import yaml

from database import init_db, insert_reading, insert_decision
from decision_engine import (
    irrigation_decision,
    irrigation_amount,
    make_actuator_command,
    yield_forecast,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s — %(message)s",
)
logger = logging.getLogger(__name__)

# ── Realistic sensor ranges ──────────────────────────────────────────────────

_BASE = {
    "air_temp":     22.0,   # °C
    "air_humidity": 60.0,   # %
    "soil_moisture": 0.28,  # m³/m³  (well-irrigated wheat)
    "soil_temp":    18.0,   # °C
    "precipitation": 0.0,   # mm
    "lai":           2.1,   # leaf area index
}

_NOISE = {
    "air_temp":      3.0,
    "air_humidity":  8.0,
    "soil_moisture": 0.05,
    "soil_temp":     2.0,
    "lai":           0.3,
}


def _jitter(base: float, noise: float) -> float:
    return round(base + random.gauss(0, noise), 3)


def _rain_event() -> float:
    """Occasional rain: 10 % chance, 0-12 mm."""
    return round(random.uniform(0, 12), 1) if random.random() < 0.10 else 0.0


def make_reading(ts: datetime) -> dict:
    """Generate one plausible sensor reading for the given timestamp."""
    # Diurnal temperature cycle: peak at 14:00 UTC
    hour_offset = math.sin(math.pi * (ts.hour - 6) / 12) * 4.0
    precip = _rain_event()
    sm_bump = 0.04 if precip > 5 else 0.0  # rain raises soil moisture

    return {
        "timestamp":    ts.isoformat(),
        "air_temp":     _jitter(_BASE["air_temp"] + hour_offset, _NOISE["air_temp"]),
        "air_humidity": max(20.0, min(98.0, _jitter(_BASE["air_humidity"], _NOISE["air_humidity"]))),
        "soil_moisture": max(0.05, min(0.55, _jitter(_BASE["soil_moisture"] + sm_bump, _NOISE["soil_moisture"]))),
        "soil_temp":    _jitter(_BASE["soil_temp"], _NOISE["soil_temp"]),
        "precipitation": precip,
        "lai":          max(0.0, _jitter(_BASE["lai"], _NOISE["lai"])),
    }


# ── Decision helper ──────────────────────────────────────────────────────────

def run_decision(field_id: str, field_cfg: dict, readings: list[dict]) -> dict:
    """Aggregate readings, call the engine, return the actuator command."""
    n = len(readings)
    agg = {
        "mean_temp":          sum(r["air_temp"] for r in readings) / n,
        "mean_humidity":      sum(r["air_humidity"] for r in readings) / n,
        "mean_soil_temp":     sum(r["soil_temp"] for r in readings) / n,
        "mean_soil_moisture": sum(r["soil_moisture"] for r in readings) / n,
        "total_precip":       sum(r.get("precipitation", 0.0) for r in readings),
        "max_lai":            max(r.get("lai", 0.0) for r in readings),
    }

    ts = datetime.now(timezone.utc)
    agg["season_days"] = ts.timetuple().tm_yday
    agg["year"] = float(ts.year)

    decision = irrigation_decision(
        field_cfg["crop"],
        agg["season_days"],
        agg["mean_soil_moisture"],
    )

    volume = irrigation_amount(
        delta_sm=decision["delta_sm"],
        field_area_m2=field_cfg["area_m2"],
        root_depth=field_cfg["root_depth"],
    )

    forecast_features = {
        **agg,
        "latitude":  field_cfg["latitude"],
        "longitude": field_cfg["longitude"],
        "elevation": field_cfg["elevation"],
        "WAV":       field_cfg["wav"],
    }
    predicted_yield = yield_forecast(
        forecast_features,
        field_cfg["crop"],
        horizon_days=field_cfg.get("horizon_days", 30),
    )

    command = make_actuator_command(field_id, decision["alarm"], volume, predicted_yield)
    logger.info(
        "field=%s alarm=%s pump=%s volume=%.1fL yield=%.1f kg/ha",
        field_id, command["alarm"], command["pump"],
        command["volume_litres"], command["yield_forecast_kg_ha"],
    )
    return command


# ── Modes ────────────────────────────────────────────────────────────────────

def backfill(fields: list[dict], days: int, readings_per_day: int):
    """Insert historical sensor readings and decisions for the past N days."""
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    interval_hours = 24 / readings_per_day

    for field_cfg in fields:
        fid = field_cfg["id"]
        logger.info("Backfilling field=%s  days=%d  readings/day=%d", fid, days, readings_per_day)

        for day_offset in range(days, 0, -1):
            day_readings: list[dict] = []
            for i in range(readings_per_day):
                ts = now - timedelta(days=day_offset) + timedelta(hours=i * interval_hours)
                reading = make_reading(ts)
                insert_reading(fid, reading)
                day_readings.append(reading)

            command = run_decision(fid, field_cfg, day_readings)
            insert_decision(fid, command)

    logger.info("Backfill complete.")


def live(fields: list[dict], interval_seconds: int, trigger_count: int):
    """Stream mock sensor readings in real time, making a decision every trigger_count readings."""
    logger.info(
        "Live mode — one reading every %ds, decision every %d readings. Ctrl-C to stop.",
        interval_seconds, trigger_count,
    )
    accumulators: dict[str, list[dict]] = {f["id"]: [] for f in fields}
    field_map = {f["id"]: f for f in fields}

    while True:
        ts = datetime.now(timezone.utc)
        for fid, field_cfg in field_map.items():
            reading = make_reading(ts)
            insert_reading(fid, reading)
            accumulators[fid].append(reading)
            logger.info(
                "field=%s  air_temp=%.1f°C  soil_moisture=%.3f  precip=%.1fmm",
                fid, reading["air_temp"], reading["soil_moisture"], reading["precipitation"],
            )

            if len(accumulators[fid]) >= trigger_count:
                command = run_decision(fid, field_cfg, accumulators[fid])
                insert_decision(fid, command)
                accumulators[fid] = []

        time.sleep(interval_seconds)


# ── CLI ──────────────────────────────────────────────────────────────────────

def main():
    with open("config.yaml") as f:
        cfg = yaml.safe_load(f)

    fields = cfg["fields"]
    trigger_count = cfg.get("decision_trigger_count", 24)

    parser = argparse.ArgumentParser(description="AgriFlow mock IoT simulator")
    sub = parser.add_subparsers(dest="mode", required=True)

    bp = sub.add_parser("backfill", help="Insert historical data")
    bp.add_argument("--days", type=int, default=7, help="Days of history to generate (default: 7)")
    bp.add_argument("--readings-per-day", type=int, default=24, help="Readings per day (default: 24)")

    lp = sub.add_parser("live", help="Stream real-time readings")
    lp.add_argument("--interval", type=int, default=5, help="Seconds between readings (default: 5)")
    lp.add_argument(
        "--trigger",
        type=int,
        default=trigger_count,
        help=f"Readings before a decision (default: {trigger_count} from config)",
    )

    args = parser.parse_args()
    init_db()

    if args.mode == "backfill":
        backfill(fields, days=args.days, readings_per_day=args.readings_per_day)
    elif args.mode == "live":
        try:
            live(fields, interval_seconds=args.interval, trigger_count=args.trigger)
        except KeyboardInterrupt:
            logger.info("Live simulation stopped.")


if __name__ == "__main__":
    main()
