"""Constants for the Zont Sensor Bridge integration."""

import logging

DOMAIN = "zont_sensor_bridge"

CONF_URL = "url"
CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_BASIC_AUTH = "basic_auth"

CONF_UPDATE_INTERVAL = "update_interval"
CONF_SENSORS = "sensors"

CONF_ENTITY_ID = "entity_id"
CONF_ZONT_ID = "zont_id"
CONF_ZONT_NAME = "zont_name"
CONF_MIN = "min"
CONF_MAX = "max"
CONF_REMOVE = "remove"

DEFAULT_USERNAME = "admin"
DEFAULT_UPDATE_INTERVAL = 60

LOGGER = logging.getLogger(__package__)
