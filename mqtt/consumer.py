"""
MQTT consumer — reads daily IoT sensor data, aggregates it, calls the
decision engine, and publishes the actuator command.

Expected sensor message schema (JSON):
  {
    "timestamp":    "2024-06-15T14:00:00Z",   # ISO-8601 UTC
    "air_temp":     22.5,                      # °C
    "air_humidity": 65.0,                      # %
    "soil_moisture": 0.28,                     # m³/m³
    "soil_temp":    18.3,                      # °C
    "precipitation": 0.0,                      # mm (interval total)
    "lai":          2.1                        # optional, defaults to 0
  }

Readings accumulate in memory until `decision_trigger_count` messages
have arrived, then a decision is made and the accumulator is reset.
Set decision_trigger_count=1 in config.yaml for pre-aggregated payloads.
"""

from __future__ import annotations

import json
import logging
import threading
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

import paho.mqtt.client as mqtt
import yaml

from decision_engine import (
    irrigation_decision,
    irrigation_amount,
    make_actuator_command,
    yield_forecast,
)
from mqtt.publisher import ActuatorPublisher

logger = logging.getLogger(__name__)


def _load_config(path: str = "config.yaml") -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


class SensorAccumulator:
    """Thread-safe accumulator for within-day sensor readings."""

    def __init__(self):
        self._lock = threading.Lock()
        self._reset()

    def _reset(self):
        self._readings: list[dict] = []

    def add(self, reading: dict) -> int:
        with self._lock:
            self._readings.append(reading)
            return len(self._readings)

    def aggregate_and_reset(self) -> dict:
        with self._lock:
            r = self._readings
            self._reset()

        if not r:
            raise ValueError("No readings to aggregate")

        ts = datetime.now(timezone.utc)
        return {
            "mean_temp":          sum(x["air_temp"] for x in r) / len(r),
            "mean_humidity":      sum(x["air_humidity"] for x in r) / len(r),
            "mean_soil_temp":     sum(x["soil_temp"] for x in r) / len(r),
            "mean_soil_moisture": sum(x["soil_moisture"] for x in r) / len(r),
            "total_precip":       sum(x.get("precipitation", 0.0) for x in r),
            "max_lai":            max(x.get("lai", 0.0) for x in r),
            "season_days":        ts.timetuple().tm_yday,
            "year":               float(ts.year),
        }


class SensorConsumer:
    def __init__(self, config_path: str = "config.yaml"):
        cfg = _load_config(config_path)
        self._cfg = cfg
        self._trigger = cfg.get("decision_trigger_count", 24)

        mqtt_cfg = cfg["mqtt"]
        self._publisher = ActuatorPublisher(
            broker=mqtt_cfg["broker"],
            port=mqtt_cfg.get("port", 1883),
            username=mqtt_cfg.get("username", ""),
            password=mqtt_cfg.get("password", ""),
            qos=mqtt_cfg.get("qos", 1),
        )

        # Build field lookup: field_id → field config dict
        self._fields: dict[str, dict] = {f["id"]: f for f in cfg["fields"]}
        # Per-field accumulators
        self._accumulators: dict[str, SensorAccumulator] = defaultdict(SensorAccumulator)

        self._client = mqtt.Client(client_id="agriflow-consumer", protocol=mqtt.MQTTv5)
        if mqtt_cfg.get("username"):
            self._client.username_pw_set(mqtt_cfg["username"], mqtt_cfg.get("password", ""))
        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message
        self._client.connect(mqtt_cfg["broker"], mqtt_cfg.get("port", 1883), keepalive=mqtt_cfg.get("keepalive", 60))

    def _on_connect(self, client, userdata, flags, rc, properties=None):
        if rc != 0:
            logger.error("Consumer connection failed, rc=%s", rc)
            return
        logger.info("Consumer connected to MQTT broker")
        for field_id in self._fields:
            topic = f"farm/{field_id}/sensors"
            self._client.subscribe(topic, qos=self._cfg["mqtt"].get("qos", 1))
            logger.info("Subscribed to %s", topic)

    def _on_message(self, client, userdata, msg: mqtt.MQTTMessage):
        try:
            payload = json.loads(msg.payload.decode())
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            logger.warning("Bad payload on %s: %s", msg.topic, e)
            return

        # topic format: farm/{field_id}/sensors
        parts = msg.topic.split("/")
        if len(parts) != 3:
            return
        field_id = parts[1]

        field_cfg = self._fields.get(field_id)
        if field_cfg is None:
            logger.warning("Received message for unknown field '%s'", field_id)
            return

        required = {"air_temp", "air_humidity", "soil_moisture", "soil_temp"}
        if not required.issubset(payload):
            logger.warning("Sensor message missing fields %s", required - payload.keys())
            return

        acc = self._accumulators[field_id]
        count = acc.add(payload)
        logger.debug("field=%s accumulated %d/%d readings", field_id, count, self._trigger)

        if count >= self._trigger:
            self._make_decision(field_id, field_cfg, acc)

    def _make_decision(self, field_id: str, field_cfg: dict, acc: SensorAccumulator):
        try:
            aggregated = acc.aggregate_and_reset()
        except ValueError:
            return

        crop = field_cfg["crop"]
        ts = datetime.now(timezone.utc)
        day_of_year = ts.timetuple().tm_yday

        decision = irrigation_decision(crop, day_of_year, aggregated["mean_soil_moisture"])
        alarm = decision["alarm"]
        delta_sm = decision["delta_sm"]

        volume = irrigation_amount(
            delta_sm=delta_sm,
            field_area_m2=field_cfg["area_m2"],
            root_depth=field_cfg["root_depth"],
        )

        forecast_features = {
            **aggregated,
            "latitude":  field_cfg["latitude"],
            "longitude": field_cfg["longitude"],
            "elevation": field_cfg["elevation"],
            "WAV":       field_cfg["wav"],
        }
        horizon = field_cfg.get("horizon_days", 30)
        predicted_yield = yield_forecast(forecast_features, crop, horizon_days=horizon)

        command = make_actuator_command(field_id, alarm, volume, predicted_yield)
        self._publisher.publish(field_id, command)

        logger.info(
            "field=%s crop=%s alarm=%s volume=%.1fL yield=%.1f kg/ha",
            field_id, crop, alarm, volume, predicted_yield,
        )

    def run_forever(self):
        logger.info("Starting sensor consumer loop")
        self._client.loop_forever()

    def disconnect(self):
        self._client.disconnect()
        self._publisher.disconnect()


if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )
    config_path = sys.argv[1] if len(sys.argv) > 1 else "config.yaml"
    SensorConsumer(config_path).run_forever()
