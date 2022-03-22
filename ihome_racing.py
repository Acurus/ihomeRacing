import logging
import logging.config
import sys
from dataclasses import dataclass
import time
import irsdk
import paho.mqtt.client as mqtt
import yaml
import json


@dataclass
class Car:
    """Class for engine data."""

    rpm: float
    speed: float
    fuel_percent: float
    gear: int

    def state(self) -> str:
        return "onTrack"

    def attributes(self) -> str:
        data = {'rpm': int(self.rpm), 'speed': int(
            self.speed*3.6), "fuel_percent": 100-round(self.fuel_percent, 1), "gear": int(self.gear)}
        return json.dumps(data)


@ dataclass
class Session:
    """Class for session data"""

    event_type: str
    current_session_type: str
    session_laps_total: int
    lap_completed: int
    player_car_class_position: int


@ dataclass
class Track:
    """Class for session data"""

    name: str
    latitude: float
    longitude: float

    def state(self) -> str:
        data = {'name': self.name}
        return json.dumps(data)

    def attributes(self) -> str:
        data = {'latitude': self.latitude.replace('m', '').strip(),
                'longitude': self.longitude.replace('m', '').strip()}
        return json.dumps(data)


class MQTT:
    def __init__(self, config):
        self.ir_connected = False
        self.tick = 0
        self.mqttConnected = False
        self.base_topic = config["mqtt"]["baseTopic"]
        self.client = mqtt.Client("irClient")
        self.client.username_pw_set(
            config["homeassistant"]["username"],
            config["homeassistant"]["password"]
        )
        self.mqttRC = [
            "Connection successful",
            "Connection refused - incorrect protocol version",
            "Connection refused - invalid client identifier",
            "Connection refused - server unavailable",
            "Connection refused - bad username or password",
            "Connection refused - not authorised",
        ]
        self.client.on_connect = self.on_connect
        self.client.on_disconnect = self.on_disconnect
        self.client.loop_start()
        try:
            self.client.connect(config["mqtt"]["host"],
                                int(config["mqtt"]["port"]))
            self.birth()
        except Exception:
            logger.error("unable to connect to mqtt broker")

    def publish(self, topic_part: str, data: str):
        topic = self.base_topic + topic_part
        self.client.publish(topic, data)
        logger.debug(f"mqtt_publish({topic}/{data})")

    def birth(self):
        topic = "homeassistant/status"
        self.client.publish(topic, "online")

    def will(self):
        topic = "homeassistant/status"
        self.client.publish(topic, "offline")
    # Paho MQTT callback

    def on_connect(self, client, userdata, flags, rc):
        logger.info("MQTT: " + self.mqttRC[rc])
        if rc == 0:
            self.mqttConnected = True
            if self.ir_connected:
                self.publish("state", 1)
        else:
            logger.error("Bad connection Returned code=", rc)

    # Paho MQTT callback
    def on_disconnect(self, client, userdata, rc):
        self.mqttConnected = False
        if rc == 0:
            logger.info("MQTT: connection terminated")
        else:
            logger.error("MQTT: connection terminated unexpectedly")


def get_config():
    config_file = "config.yaml"
    with open(config_file, 'r') as stream:
        try:
            config = yaml.safe_load(stream)
        except yaml.YAMLError as exc:
            print(exc)
    return config


def setup_logger():
    DEFAULT_LOGGING = {
        "version": 1,
        "formatters": {
            "standard": {
                "format": "%(asctime)s %(levelname)s: %(message)s",
                "datefmt": "%Y-%m-%d - %H:%M:%S",
            },
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "formatter": "standard",
                "level": "DEBUG",
                "stream": sys.stdout,
            },
            "file": {
                "class": "logging.FileHandler",
                "formatter": "standard",
                "level": "DEBUG",
                "filename": "ihome_racing.log",
                "mode": "w",
            },
        },
        "loggers": {
            __name__: {
                "level": "DEBUG",
                "handlers": ["console", "file"],
                "propagate": False,
            },
        },
    }

    logging.config.dictConfig(DEFAULT_LOGGING)
    return logging.getLogger(__name__)


def iracing_running(ir: irsdk.IRSDK) -> bool:
    is_startup = ir.startup()
    logger.debug("Waiting for iRacing to start up")
    return is_startup and ir.is_initialized and ir.is_connected


def send_telemetry(ir: irsdk.IRSDK, mqtt: MQTT):
    while ir.is_connected:
        ir.freeze_var_buffer_latest()
        session = Session(
            ir['WeekendInfo']['EventType'], ir['SessionInfo']['Sessions'][0]['SessionType'], ir['SessionLapsTotal'], ir['LapCompleted'], ir['PlayerCarClassPosition'])
        car = Car(ir['RPM'], ir['Speed'], ir['FuelLevelPct'], ir['Gear'])
        track = Track(ir['WeekendInfo']['TrackDisplayName'], ir['WeekendInfo']['TrackLatitude'],
                      ir['WeekendInfo']['TrackLongitude'])
        mqtt.publish("car", car.state())
        mqtt.publish("track", track.state())
        mqtt.publish("car/attributes", car.attributes())
        mqtt.publish("track/attributes", track.attributes())
        time.sleep(1)


def main() -> None:
    config = get_config()

    ir = irsdk.IRSDK()
    mqtt = MQTT(config)

    while not iracing_running(ir):
        time.sleep(1)
    logger.info("Iracing up and running")

    try:
        send_telemetry(ir, mqtt)
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt")
        mqtt.will()
        mqtt.client.loop_stop()
        mqtt.client.disconnect()


if __name__ == "__main__":
    logger = setup_logger()
    main()
