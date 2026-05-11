import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path("agriflow.db")

_DDL = """
CREATE TABLE IF NOT EXISTS sensor_readings (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    field_id      TEXT    NOT NULL,
    timestamp     TEXT    NOT NULL,
    air_temp      REAL,
    air_humidity  REAL,
    soil_moisture REAL,
    soil_temp     REAL,
    precipitation REAL    DEFAULT 0.0,
    lai           REAL    DEFAULT 0.0
);
CREATE TABLE IF NOT EXISTS decisions (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    field_id             TEXT NOT NULL,
    timestamp            TEXT NOT NULL,
    alarm                TEXT NOT NULL,
    volume_litres        REAL,
    yield_forecast_kg_ha REAL,
    pump_status          TEXT DEFAULT 'OFF'
);
CREATE TABLE IF NOT EXISTS daily_forecasts (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    field_id             TEXT    NOT NULL,
    date                 TEXT    NOT NULL,
    horizon_days         INTEGER NOT NULL,
    yield_forecast_kg_ha REAL,
    iot_readings         INTEGER,
    lai_value            REAL,
    lai_source           TEXT,
    openmeteo_used       INTEGER DEFAULT 0,
    created_at           TEXT    NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_daily_forecasts_uniq ON daily_forecasts(field_id, date, horizon_days);
CREATE INDEX IF NOT EXISTS idx_readings_field_ts ON sensor_readings(field_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_decisions_field_ts ON decisions(field_id, timestamp);
"""


def init_db():
    with _conn() as c:
        c.executescript(_DDL)
    _migrate()


def _migrate():
    new_columns = [
        "ALTER TABLE daily_forecasts ADD COLUMN ndvi REAL",
        "ALTER TABLE daily_forecasts ADD COLUMN ndwi REAL",
        "ALTER TABLE daily_forecasts ADD COLUMN ndre REAL",
    ]
    conn = sqlite3.connect(DB_PATH)
    try:
        for sql in new_columns:
            try:
                conn.execute(sql)
            except sqlite3.OperationalError:
                pass  # column already exists
        conn.commit()
    finally:
        conn.close()


@contextmanager
def _conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def insert_reading(field_id: str, reading: dict):
    ts = reading.get("timestamp") or datetime.now(timezone.utc).isoformat()
    with _conn() as c:
        c.execute(
            """INSERT INTO sensor_readings
               (field_id, timestamp, air_temp, air_humidity, soil_moisture, soil_temp, precipitation, lai)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                field_id, ts,
                reading.get("air_temp"),
                reading.get("air_humidity"),
                reading.get("soil_moisture"),
                reading.get("soil_temp"),
                reading.get("precipitation", 0.0),
                reading.get("lai", 0.0),
            ),
        )


def insert_decision(field_id: str, decision: dict):
    ts = datetime.now(timezone.utc).isoformat()
    with _conn() as c:
        c.execute(
            """INSERT INTO decisions
               (field_id, timestamp, alarm, volume_litres, yield_forecast_kg_ha, pump_status)
               VALUES (?,?,?,?,?,?)""",
            (
                field_id, ts,
                decision.get("alarm", "NORMAL"),
                decision.get("volume_litres", 0.0),
                decision.get("yield_forecast_kg_ha", 0.0),
                decision.get("pump", "OFF"),
            ),
        )


def get_latest_reading(field_id: str) -> dict | None:
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM sensor_readings WHERE field_id=? ORDER BY timestamp DESC LIMIT 1",
            (field_id,),
        ).fetchone()
    return dict(row) if row else None


def get_sparkline(field_id: str, sensor: str, n: int = 12) -> list[float]:
    with _conn() as c:
        rows = c.execute(
            f"SELECT {sensor} FROM sensor_readings WHERE field_id=? ORDER BY timestamp DESC LIMIT ?",
            (field_id, n),
        ).fetchall()
    values = [r[0] for r in reversed(rows) if r[0] is not None]
    if not values:
        return []
    lo, hi = min(values), max(values)
    span = hi - lo or 1
    return [20 + 75 * (v - lo) / span for v in values]


def get_latest_decision(field_id: str) -> dict | None:
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM decisions WHERE field_id=? ORDER BY timestamp DESC LIMIT 1",
            (field_id,),
        ).fetchone()
    return dict(row) if row else None


def get_history(field_id: str, date_from: str, date_to: str, page: int = 1, per_page: int = 50):
    offset = (page - 1) * per_page
    with _conn() as c:
        total = c.execute(
            "SELECT COUNT(*) FROM sensor_readings WHERE field_id=? AND timestamp BETWEEN ? AND ?",
            (field_id, date_from, date_to),
        ).fetchone()[0]
        rows = c.execute(
            """SELECT r.timestamp, r.air_temp, r.air_humidity, r.soil_temp, r.soil_moisture,
                      d.alarm, d.volume_litres, d.pump_status
               FROM sensor_readings r
               LEFT JOIN decisions d ON d.field_id = r.field_id
                   AND d.timestamp = (
                       SELECT MAX(d2.timestamp) FROM decisions d2
                       WHERE d2.field_id = r.field_id AND d2.timestamp <= r.timestamp
                   )
               WHERE r.field_id=? AND r.timestamp BETWEEN ? AND ?
               ORDER BY r.timestamp DESC
               LIMIT ? OFFSET ?""",
            (field_id, date_from, date_to, per_page, offset),
        ).fetchall()
    return [dict(r) for r in rows], total


def get_chart_series(field_id: str, date_from: str, date_to: str) -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            """SELECT timestamp, air_temp, air_humidity, soil_moisture, soil_temp
               FROM sensor_readings
               WHERE field_id=? AND timestamp BETWEEN ? AND ?
               ORDER BY timestamp ASC LIMIT 500""",
            (field_id, date_from, date_to),
        ).fetchall()
    return [dict(r) for r in rows]


def upsert_daily_forecast(
    field_id: str,
    date: str,
    horizon_days: int,
    yield_forecast_kg_ha: float,
    iot_readings: int,
    lai_value: float | None,
    lai_source: str,
    openmeteo_used: bool,
    ndvi: float | None = None,
    ndwi: float | None = None,
    ndre: float | None = None,
):
    ts = datetime.now(timezone.utc).isoformat()
    with _conn() as c:
        c.execute(
            """INSERT INTO daily_forecasts
               (field_id, date, horizon_days, yield_forecast_kg_ha, iot_readings,
                lai_value, lai_source, openmeteo_used, ndvi, ndwi, ndre, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(field_id, date, horizon_days) DO UPDATE SET
                 yield_forecast_kg_ha = excluded.yield_forecast_kg_ha,
                 iot_readings         = excluded.iot_readings,
                 lai_value            = excluded.lai_value,
                 lai_source           = excluded.lai_source,
                 openmeteo_used       = excluded.openmeteo_used,
                 ndvi                 = excluded.ndvi,
                 ndwi                 = excluded.ndwi,
                 ndre                 = excluded.ndre,
                 created_at           = excluded.created_at""",
            (field_id, date, horizon_days, yield_forecast_kg_ha, iot_readings,
             lai_value, lai_source, int(openmeteo_used), ndvi, ndwi, ndre, ts),
        )


def get_daily_forecasts(field_id: str, horizon_days: int = 30, n: int = 60) -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            """SELECT date, yield_forecast_kg_ha, iot_readings, lai_value, lai_source,
                      openmeteo_used, ndvi, ndwi, ndre, created_at
               FROM daily_forecasts
               WHERE field_id=? AND horizon_days=?
               ORDER BY date DESC LIMIT ?""",
            (field_id, horizon_days, n),
        ).fetchall()
    return [dict(r) for r in reversed(rows)]
