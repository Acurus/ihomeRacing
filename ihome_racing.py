from enum import Enum
import json
import logging
import logging.config
import math
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from math import ceil
from pathlib import Path

import irsdk
import paho.mqtt.client as mqtt
import yaml

# https://github.com/kutu/pyirsdk/blob/master/vars.txt

sessionSateMap = {0: "Venter", 1: "Gjør seg klar", 2: "Oppvarming",
                  3: "Kjører til start", 4: "Racing", 5: "I mål", 6: "Cooldown"}


@dataclass
class Car:
    """Class for engine data."""

    rpm: float = 0
    speed: float = 0
    fuel_percent: float = 0
    gear: int = 0

    def sensors(self) -> dict:
        data = {'rpm': int(self.rpm), 'speed': int(
            self.speed*3.6), "fuel_percent": round(self.fuel_percent*100, 1), "gear": int(self.gear)}
        return data


@ dataclass
class Session:
    """Class for session data"""

    event_type: str = None
    current_session_type: str = None
    session_laps_total: int = None
    session_time_remain: int = None
    lap_best_lap_time: int = None
    lap_completed: int = None
    player_car_class_position: int = None
    track_name: str = None
    car_name: str = None
    session_state: str = None

    @property
    def session_state_mapped(self):
        if self.session_state:
            try:
                return sessionSateMap[self.session_state]
            except KeyError:
                logger.warning(f"{self.session_state} not in map")
                return self.session_state

    def sensors(self) -> dict:
        data = {'event_type': self.event_type,
                'session_laps_total': self.session_laps_total,
                'session_time_remain': time.strftime("%H:%M:%S", time.gmtime(self.session_time_remain)),
                'session_state': self.session_state,
                'lap_best_lap_time': self.lap_best_lap_time,
                'current_session_type': self.current_session_type,
                "lap_completed": self.lap_completed,
                "player_car_class_position": self.player_car_class_position,
                "track_name": self.track_name,
                "car_name": self.car_name,
                "session_state": self.session_state_mapped}
        return data


class Track:
    """Class for session data"""

    def __init__(self, temperature=0.0, latitude=None, longitude=None):
        config = get_config()
        self.temperature: float = temperature
        self.latitude: str = config["home"]["lat"] if latitude is None else latitude
        self.longitude: str = config["home"]["lon"]if longitude is None else longitude

    def state(self) -> str:
        data = {'temperature': self.temperature}
        return json.dumps(data)

    def attributes(self) -> str:
        data = {'latitude': self.latitude.replace('m', '').strip(),
                'longitude': self.longitude.replace('m', '').strip()}
        return json.dumps(data)


class MQTT:
    def __init__(self, config):
        self.config = config
        self.connected = False
        self.base_topic = self.config["mqtt"]["baseTopic"]
        self.client = mqtt.Client("irClient")
        self.client.username_pw_set(
            self.config["homeassistant"]["username"],
            self.config["homeassistant"]["password"]
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
            self.client.connect(self.config["mqtt"]["host"],
                                int(self.config["mqtt"]["port"]))
            self.birth()
        except Exception as e:
            logger.error(e)

    def publish(self, topic_part: str, data: str) -> None:
        topic = self.base_topic + topic_part
        self.client.publish(topic, data)
        logger.debug(f"mqtt_publish({topic}/{data})")

    def birth(self) -> None:
        topic = "homeassistant/status"
        self.client.publish(topic, "online")

    def will(self) -> None:
        topic = "homeassistant/status"
        self.client.publish(topic, "offline")
    # Paho MQTT callback

    def on_connect(self, client, userdata, flags, rc):
        logger.info("MQTT: " + self.mqttRC[rc])
        if rc == 0:
            self.connected = True
        else:
            logger.error("Bad connection Returned code=", rc)

    # Paho MQTT callback
    def on_disconnect(self, client, userdata, rc):
        self.connected = False
        if rc == 0:
            logger.info("MQTT: connection terminated")
        else:
            logger.error("MQTT: connection terminated unexpectedly")


def get_config() -> dict:
    config_file = Path("C:/01_Dev/01_Python/iRacing/iHomeRacing/config.yaml")
    with open(config_file, 'r') as stream:
        try:
            config = yaml.safe_load(stream)
        except yaml.YAMLError as exc:
            print(exc)
    return config


def setup_logger():
    date_string = datetime.now().strftime("%Y-%m-%dT%H%M")
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
                "filename": f"C:/01_Dev/01_Python/iRacing/iHomeRacing/ihome_racing_{date_string}.log",
                "mode": "w",
            },
        },
        "loggers": {
            __name__: {
                "level": "INFO",
                "handlers": ["console", "file"],
                "propagate": False,
            },
        },
    }

    logging.config.dictConfig(DEFAULT_LOGGING)
    return logging.getLogger(__name__)


def iracing_running(ir: irsdk.IRSDK) -> bool:
    is_startup = ir.startup()
    return is_startup and ir.is_initialized and ir.is_connected


def process(ir: irsdk.IRSDK, mqtt: MQTT):
    while True:
        if iracing_running(ir):
            ir.freeze_var_buffer_latest()
            session = Session(
                ir['WeekendInfo']['EventType'],
                ir['SessionInfo']['Sessions'][ir['SessionNum']]['SessionType'],
                ir['SessionLapsTotal'],
                ir['SessionTimeRemain'],
                ir['LapBestLapTime'],
                ir['LapCompleted'],
                ir['PlayerCarClassPosition'],
                ir['WeekendInfo']['TrackDisplayName'],
                ir['DriverInfo']['Drivers'][ir['PlayerCarIdx']]['CarScreenName'],
                ir['SessionState'])

            car = Car(ir['RPM'], ir['Speed'], ir['FuelLevelPct'],
                      ir['Gear'])

            track = Track(ir['WeekendInfo']['TrackAirTemp'],
                          ir['WeekendInfo']['TrackLatitude'],
                          ir['WeekendInfo']['TrackLongitude']
                          )

            send_telemetry(mqtt, session, car, track)
            time.sleep(1)
        else:
            send_telemetry(mqtt, Session(), Car(),  Track())
            logger.debug("Waiting for iRacing to start up")
            time.sleep(1)


def send_telemetry(mqtt: MQTT, session: Session, car: Car, track: Track):
    mqtt.publish("track/attributes", track.attributes())

    for sensor, value in car.sensors().items():
        mqtt.publish(f"car/{sensor}", value)

    for sensor, value in session.sensors().items():
        mqtt.publish(f"session/{sensor}", value)
    mqtt.publish("track", track.state())


def main() -> None:
    config = get_config()

    ir = irsdk.IRSDK()
    mqtt = MQTT(config)

    while not iracing_running(ir):
        logger.debug("Waiting for iRacing to start up")
        time.sleep(1)
    logger.info("Iracing up and running")

    try:
        process(ir, mqtt)
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt")
        send_telemetry(mqtt, Session(), Car(),  Track())
        mqtt.will()
        mqtt.client.loop_stop()
        mqtt.client.disconnect()


if __name__ == "__main__":

    logger = setup_logger()
    main()
