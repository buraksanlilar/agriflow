"""
MQTT publisher — sends actuator commands to farm/{field_id}/pump.
"""

from __future__ import annotations

import json
import logging

import paho.mqtt.client as mqtt

logger = logging.getLogger(__name__)


class ActuatorPublisher:
    def __init__(self, broker: str, port: int = 1883, username: str = "", password: str = "", qos: int = 1):
        self._qos = qos
        self._client = mqtt.Client(client_id="agriflow-publisher", protocol=mqtt.MQTTv5)
        if username:
            self._client.username_pw_set(username, password)
        self._client.on_connect = self._on_connect
        self._client.on_publish = self._on_publish
        self._client.connect(broker, port)
        self._client.loop_start()

    def _on_connect(self, client, userdata, flags, rc, properties=None):
        if rc == 0:
            logger.info("Publisher connected to MQTT broker")
        else:
            logger.error("Publisher connection failed, rc=%s", rc)

    def _on_publish(self, client, userdata, mid):
        logger.debug("Message published mid=%s", mid)

    def publish(self, field_id: str, command: dict) -> None:
        topic = f"farm/{field_id}/pump"
        payload = json.dumps(command)
        info = self._client.publish(topic, payload, qos=self._qos)
        info.wait_for_publish(timeout=5.0)
        logger.info("Published to %s: %s", topic, payload)

    def disconnect(self) -> None:
        self._client.loop_stop()
        self._client.disconnect()
