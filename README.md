# AgriFlow

IoT-driven precision irrigation and yield forecasting platform. Reads field sensor data over MQTT, decides when and how much to irrigate, forecasts crop yield with LightGBM, and enriches predictions with real satellite imagery (Sentinel-2) and weather data (Open-Meteo).

---

## Features

- **Real-time irrigation decisions** — CRITICAL / IRRIGATE / NORMAL / STOP alarms based on crop-specific daily soil moisture profiles
- **Yield forecasting** — LightGBM models for 30-, 60-, and 90-day horizons across 22 crops
- **Sentinel-2 vegetation indices** — NDVI, LAI, NDWI, NDRE pulled from free Element84 Earth Search; 5-day in-memory cache
- **Open-Meteo weather gap-fill** — replaces missing IoT readings with reanalysis data automatically
- **Daily inference pipeline** — background scheduler runs at a configured UTC hour; results persisted to SQLite
- **Model adaptation** — `retrain.py` applies field-specific bias corrections or LightGBM warm-start fine-tuning after real harvests
- **Web dashboard** — FastAPI + Jinja2 + HTMX; monitoring, forecast history, field profile, and raw-data tables

---

## Architecture

```
IoT Sensors
    │  MQTT  farm/{field}/sensors
    ▼
SensorConsumer ─── decision_engine ──► ActuatorPublisher
(mqtt/consumer.py)                          │  farm/{field}/pump
                                            │
MQTTBridge ◄────────────────────────────────┘
(services/mqtt_bridge.py)
    │  persists all topics
    ▼
SQLite (agriflow.db)
    ▲
    │
DailyInferenceScheduler ──► OpenMeteo + Sentinel-2 ──► daily_forecasts table
(services/daily_inference.py)

FastAPI (main.py)
  /monitoring   — live sensor cards & irrigation status
  /forecast     — daily yield chart + satellite indices
  /forecast/quick — one-off forecast for any location
  /history      — paginated sensor log
  /profile      — field configuration
```

---

## Requirements

- Python 3.11+
- MQTT broker (e.g. Mosquitto) — optional; app starts without one
- Internet access for Sentinel-2 STAC queries and Open-Meteo API

```bash
pip install -r requirements.txt
```

---

## Quick start

### 1. Configure fields

Edit `config.yaml`:

```yaml
mqtt:
  broker: localhost
  port: 1883

fields:
  - id: field_001
    crop: wheat
    area_m2: 10000
    root_depth: 0.6
    latitude: 39.93
    longitude: 32.86
    elevation: 850.0
    wav: 100.0          # plant-available water at season start (mm)
    horizon_days: 30    # preferred forecast horizon: 30, 60, or 90
```

### 2. Seed the database with mock data (no broker needed)

```bash
# Backfill 7 days of realistic sensor readings + irrigation decisions
python mock_simulator.py backfill

# Or backfill 30 days
python mock_simulator.py backfill --days 30

# Stream live readings every 5 s (Ctrl-C to stop)
python mock_simulator.py live
```

### 3. Run the app

```bash
uvicorn main:app --reload
```

Open `http://localhost:8000` — you land on the monitoring dashboard.

---

## MQTT message format

**Sensor topic:** `farm/{field_id}/sensors`

```json
{
  "timestamp":     "2025-05-11T14:00:00Z",
  "air_temp":      22.5,
  "air_humidity":  65.0,
  "soil_moisture": 0.28,
  "soil_temp":     18.3,
  "precipitation": 0.0,
  "lai":           2.1
}
```

**Actuator topic (published by consumer):** `farm/{field_id}/pump`

```json
{
  "field_id":               "field_001",
  "pump":                   "ON",
  "alarm":                  "IRRIGATE",
  "volume_litres":          1800.0,
  "yield_forecast_kg_ha":   4650.0
}
```

Readings accumulate until `decision_trigger_count` (default 24) have arrived, then a decision is computed and the pump command is published.

---

## Daily inference pipeline

Runs automatically at `inference_hour_utc` (default 06:00 UTC). For each field and each horizon (30 / 60 / 90 days):

1. Aggregates the previous day's IoT readings from SQLite
2. Fills missing values from Open-Meteo (reanalysis)
3. Fetches Sentinel-2 LAI, NDVI, NDWI, NDRE (5-day cache)
4. Builds the full feature vector and calls the LightGBM model
5. Upserts the result into the `daily_forecasts` table

**Backfill past days manually:**

```bash
python -m services.daily_inference --days 14
```

---

## Model adaptation after harvest

After each harvest, run `retrain.py` to keep the yield model accurate for your specific field:

```bash
# Record actual yield — applies bias correction (< 3 seasons of data)
python retrain.py --field field_001 --horizon 30 --actual-yield 4800

# All three horizons at once
python retrain.py --field field_001 --horizon 30 60 90 --actual-yield 4800
```

With fewer than 3 seasons, a per-field bias offset is saved to `decision_engine/models/field_yield_offsets.json` and applied automatically to all future predictions. Once 3 or more seasons have accumulated, the script switches to full LightGBM warm-start fine-tuning (`init_model`), adding 50 new trees on top of the base model.

---

## Supported crops

barley, cassava, chickpea, cotton, cowpea, fababean, groundnut, maize, millet, mungbean, pigeonpea, potato, rapeseed, rice, seed_onion, sorghum, soybean, sugarbeet, sunflower, sweetpotato, tobacco, wheat

---

## Project layout

```
agriflow/
├── main.py                        # FastAPI app, lifespan wiring
├── config.yaml                    # Field and MQTT configuration
├── database.py                    # SQLite schema + helpers
├── retrain.py                     # Harvest-time model adaptation CLI
├── mock_simulator.py              # Backfill / live IoT simulator
├── decision_engine/
│   ├── engine.py                  # irrigation_decision, yield_forecast, …
│   ├── models/                    # iot_lgbm_day{30,60,90}.joblib
│   └── profiles/                  # crop moisture profiles, TE encoding map
├── mqtt/
│   ├── consumer.py                # MQTT subscriber + decision trigger
│   └── publisher.py               # MQTT actuator publisher
├── services/
│   ├── mqtt_bridge.py             # Passive bridge: MQTT → SQLite
│   ├── daily_inference.py         # Daily pipeline + background scheduler
│   ├── sentinel_lai.py            # Sentinel-2 vegetation indices (STAC)
│   └── openmeteo.py               # Open-Meteo weather fetcher
├── routers/
│   ├── monitoring.py              # /monitoring
│   ├── forecast.py                # /forecast, /forecast/quick
│   ├── history.py                 # /history
│   └── profile.py                 # /profile
└── templates/                     # Jinja2 HTML + HTMX partials
```

---

## SQLite tables

| Table | Purpose |
|---|---|
| `sensor_readings` | Raw IoT readings ingested over MQTT or from the simulator |
| `decisions` | Irrigation decisions (alarm level, volume, pump state) |
| `daily_forecasts` | Per-field, per-horizon daily yield predictions with Sentinel-2 indices |

---

## Key environment knobs (`config.yaml`)

| Key | Default | Description |
|---|---|---|
| `mqtt.broker` | `localhost` | MQTT broker host |
| `mqtt.qos` | `1` | MQTT QoS level |
| `decision_trigger_count` | `24` | Readings before a decision fires |
| `inference_hour_utc` | `6` | Hour (UTC) the daily pipeline runs |
| `fields[*].horizon_days` | `30` | Default forecast horizon per field |
| `fields[*].wav` | `100.0` | Plant-available water at season start (mm) |
