"""
Passive MQTT bridge — subscribes to all sensor and pump topics,
persists everything to SQLite. Runs in a background thread.
Does not duplicate the decision logic in mqtt/consumer.py.
"""

from __future__ import annotations

import json
import logging
import threading

import paho.mqtt.client as mqtt

from database import insert_reading, insert_decision

logger = logging.getLogger(__name__)


class MQTTBridge:
    def __init__(self, config: dict):
        mqtt_cfg = config["mqtt"]
        self._broker = mqtt_cfg["broker"]
        self._port = mqtt_cfg.get("port", 1883)
        self._qos = mqtt_cfg.get("qos", 1)

        self._client = mqtt.Client(client_id="agriflow-bridge", protocol=mqtt.MQTTv5)
        if mqtt_cfg.get("username"):
            self._client.username_pw_set(mqtt_cfg["username"], mqtt_cfg.get("password", ""))

        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message
        self._stop_event = threading.Event()

    def _on_connect(self, client, userdata, flags, rc, properties=None):
        if rc != 0:
            logger.error("Bridge connection failed rc=%s", rc)
            return
        logger.info("MQTT bridge connected")
        client.subscribe("farm/+/sensors", qos=self._qos)
        client.subscribe("farm/+/pump", qos=self._qos)

    def _on_message(self, client, userdata, msg: mqtt.MQTTMessage):
        try:
            payload = json.loads(msg.payload.decode())
        except (json.JSONDecodeError, UnicodeDecodeError):
            return

        parts = msg.topic.split("/")
        if len(parts) != 3:
            return
        field_id = parts[1]
        topic_type = parts[2]

        if topic_type == "sensors":
            insert_reading(field_id, payload)
        elif topic_type == "pump":
            insert_decision(field_id, payload)

    def start(self):
        try:
            self._client.connect(self._broker, self._port)
            self._client.loop_forever()
        except Exception as e:
            logger.warning("MQTT bridge could not connect: %s", e)

    def stop(self):
        self._client.loop_stop()
        self._client.disconnect()
