"""Config flow for the Zont Sensor Bridge integration."""

from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.selector import SelectOptionDict

from .client import InvalidAuth, async_fetch_sensors, async_test_connection
from .const import (
    CONF_BASIC_AUTH,
    CONF_ENTITY_ID,
    CONF_MAX,
    CONF_MIN,
    CONF_PASSWORD,
    CONF_REMOVE,
    CONF_SENSORS,
    CONF_UPDATE_INTERVAL,
    CONF_URL,
    CONF_USERNAME,
    CONF_ZONT_ID,
    CONF_ZONT_NAME,
    DEFAULT_UPDATE_INTERVAL,
    DEFAULT_USERNAME,
    DOMAIN,
)


def _host_from_url(url: str) -> str:
    """Return the network location part of a WebSocket URL."""
    url = url.removeprefix("ws://").removeprefix("wss://")
    return url.split("/", 1)[0]


def _device_schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    """Schema for the WebSocket connection step."""
    defaults = defaults or {}
    return vol.Schema(
        {
            vol.Required(
                CONF_URL,
                default=defaults.get(CONF_URL, ""),
            ): selector.TextSelector(),
            vol.Required(
                CONF_USERNAME,
                default=defaults.get(CONF_USERNAME, DEFAULT_USERNAME),
            ): selector.TextSelector(),
            vol.Required(
                CONF_PASSWORD,
                default=defaults.get(CONF_PASSWORD, ""),
            ): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD),
            ),
            vol.Optional(
                CONF_BASIC_AUTH,
                default=defaults.get(CONF_BASIC_AUTH, False),
            ): selector.BooleanSelector(),
        }
    )


def _options_schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    """Schema for the update-interval step."""
    defaults = defaults or {}
    return vol.Schema(
        {
            vol.Required(
                CONF_UPDATE_INTERVAL,
                default=defaults.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=5,
                    step=1,
                    mode=selector.NumberSelectorMode.BOX,
                ),
            ),
        }
    )


def _add_sensor_schema(zont_sensors: list[dict[str, Any]]) -> vol.Schema:
    """Schema for adding one sensor mapping."""
    options = [
        SelectOptionDict(
            value=str(sensor["id"]),
            label=f"{sensor['name']} ({sensor['id']})",
        )
        for sensor in zont_sensors
    ]
    return vol.Schema(
        {
            vol.Required(CONF_ENTITY_ID): selector.EntitySelector(
                selector.EntitySelectorConfig(
                    domain=["sensor"],
                    device_class=["temperature"],
                ),
            ),
            vol.Required(CONF_ZONT_ID): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=options,
                    mode=selector.SelectSelectorMode.LIST,
                ),
            ),
            vol.Optional(CONF_MIN): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    mode=selector.NumberSelectorMode.BOX,
                    step=0.1,
                ),
            ),
            vol.Optional(CONF_MAX): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    mode=selector.NumberSelectorMode.BOX,
                    step=0.1,
                ),
            ),
        }
    )


def _sensor_summary(sensors: list[dict[str, Any]]) -> str:
    """Build a compact, human-readable summary of sensor mappings."""
    if not sensors:
        return ""
    return ", ".join(
        f"{sensor[CONF_ENTITY_ID]} → {sensor.get(CONF_ZONT_NAME, sensor[CONF_ZONT_ID])}"
        for sensor in sensors
    )


class SensorStepsMixin:
    """Shared temperature-sensor management steps.

    Concrete flows must provide ``self._sensors`` (a list of mapping dicts) and
    implement ``_connection_params``.
    """

    _sensors: list[dict[str, Any]]
    _zont_sensors: list[dict[str, Any]] | None = None

    def _connection_params(self) -> dict[str, Any]:
        """Return the WebSocket connection parameters for this flow.

        Must be implemented by concrete flows.
        """
        raise NotImplementedError

    async def _load_zont_sensors(self) -> None:
        """Fetch and cache the list of type-1 Zont sensors."""
        if self._zont_sensors is not None:
            return
        self._zont_sensors = await async_fetch_sensors(
            self.hass,
            **self._connection_params(),
        )

    async def async_step_sensors(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Show the sensor-list menu."""
        return self.async_show_menu(
            step_id="sensors",
            menu_options=["add_sensor", "remove_sensor", "finish"],
            description_placeholders={"sensors": _sensor_summary(self._sensors)},
        )

    async def async_step_add_sensor(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Append one sensor mapping and return to the menu."""
        errors: dict[str, str] = {}

        try:
            await self._load_zont_sensors()
        except InvalidAuth:
            return self._show_sensors_error("invalid_auth")
        except (TimeoutError, aiohttp.ClientError):
            return self._show_sensors_error("cannot_connect")

        if not self._zont_sensors:
            return self._show_sensors_error("no_zont_sensors")

        if user_input is not None:
            zont_id = int(user_input[CONF_ZONT_ID])
            if any(sensor[CONF_ZONT_ID] == zont_id for sensor in self._sensors):
                errors["base"] = "duplicate_zont_id"
            else:
                zont_name = next(
                    (
                        sensor["name"]
                        for sensor in self._zont_sensors
                        if sensor["id"] == zont_id
                    ),
                    str(zont_id),
                )
                sensor: dict[str, Any] = {
                    CONF_ENTITY_ID: user_input[CONF_ENTITY_ID],
                    CONF_ZONT_ID: zont_id,
                    CONF_ZONT_NAME: zont_name,
                }
                if user_input.get(CONF_MIN) is not None:
                    sensor[CONF_MIN] = float(user_input[CONF_MIN])
                if user_input.get(CONF_MAX) is not None:
                    sensor[CONF_MAX] = float(user_input[CONF_MAX])
                self._sensors.append(sensor)
                return await self.async_step_sensors()

        return self.async_show_form(
            step_id="add_sensor",
            data_schema=_add_sensor_schema(self._zont_sensors),
            errors=errors,
        )

    async def async_step_remove_sensor(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Remove selected sensor mappings and return to the menu."""
        if not self._sensors:
            return await self.async_step_sensors()

        if user_input is not None:
            to_remove = {int(index) for index in user_input.get(CONF_REMOVE, [])}
            self._sensors = [
                sensor
                for index, sensor in enumerate(self._sensors)
                if index not in to_remove
            ]
            return await self.async_step_sensors()

        options = [
            SelectOptionDict(
                value=str(index),
                label=(
                    f"{sensor[CONF_ENTITY_ID]} → "
                    f"{sensor.get(CONF_ZONT_NAME, sensor[CONF_ZONT_ID])}"
                ),
            )
            for index, sensor in enumerate(self._sensors)
        ]

        return self.async_show_form(
            step_id="remove_sensor",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_REMOVE): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=options,
                            multiple=True,
                            mode=selector.SelectSelectorMode.LIST,
                        )
                    )
                }
            ),
        )

    def _show_sensors_error(
        self, error: str
    ) -> config_entries.ConfigFlowResult:
        """Return to the sensor menu with a validation error."""
        return self.async_show_form(
            step_id="sensors",
            data_schema=vol.Schema({}),
            errors={"base": error},
            description_placeholders={"sensors": _sensor_summary(self._sensors)},
        )


class ZontSensorBridgeConfigFlow(SensorStepsMixin, config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Zont Sensor Bridge."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize an empty config flow."""
        self._data: dict[str, Any] = {}
        self._options: dict[str, Any] = {CONF_UPDATE_INTERVAL: DEFAULT_UPDATE_INTERVAL}
        self._sensors: list[dict[str, Any]] = []

    def _connection_params(self) -> dict[str, Any]:
        """Return connection parameters from the in-progress config data."""
        return {
            "url": self._data[CONF_URL],
            "username": self._data.get(CONF_USERNAME, DEFAULT_USERNAME),
            "password": self._data[CONF_PASSWORD],
            "use_basic_auth": self._data.get(CONF_BASIC_AUTH, False),
        }

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Ask for WebSocket connection details."""
        errors: dict[str, str] = {}

        if user_input is not None:
            url = user_input[CONF_URL].strip()
            if not url.startswith(("ws://", "wss://")):
                errors["base"] = "invalid_url"
            else:
                user_input[CONF_URL] = url
                self._data = user_input
                return await self.async_step_test()

        return self.async_show_form(
            step_id="user",
            data_schema=_device_schema(user_input),
            errors=errors,
        )

    async def async_step_test(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Verify the connection before collecting sensor mappings."""
        try:
            await async_test_connection(
                self.hass,
                self._data[CONF_URL],
                self._data.get(CONF_USERNAME, DEFAULT_USERNAME),
                self._data[CONF_PASSWORD],
                self._data.get(CONF_BASIC_AUTH, False),
            )
        except InvalidAuth:
            return self.async_show_form(
                step_id="user",
                data_schema=_device_schema(self._data),
                errors={"base": "invalid_auth"},
            )
        except (TimeoutError, aiohttp.ClientError):
            return self.async_show_form(
                step_id="user",
                data_schema=_device_schema(self._data),
                errors={"base": "cannot_connect"},
            )

        await self.async_set_unique_id(self._data[CONF_URL])
        self._abort_if_unique_id_configured()
        return await self.async_step_options()

    async def async_step_options(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Ask for the periodic update interval."""
        if user_input is not None:
            self._options[CONF_UPDATE_INTERVAL] = user_input[CONF_UPDATE_INTERVAL]
            return await self.async_step_sensors()

        return self.async_show_form(
            step_id="options",
            data_schema=_options_schema(self._options),
        )

    async def async_step_finish(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Validate the sensor list and create the config entry."""
        if not self._sensors:
            return self._show_sensors_error("no_sensors")

        self._options[CONF_SENSORS] = self._sensors
        return self.async_create_entry(
            title=_host_from_url(self._data[CONF_URL]),
            data=self._data,
            options=self._options,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlowWithReload:
        """Return the options flow handler."""
        return ZontSensorBridgeOptionsFlow()


class ZontSensorBridgeOptionsFlow(SensorStepsMixin, config_entries.OptionsFlowWithReload):
    """Handle an options flow for Zont Sensor Bridge."""

    def __init__(self) -> None:
        """Initialize an empty options flow."""
        self._options: dict[str, Any] = {}
        self._sensors: list[dict[str, Any]] = []

    def _connection_params(self) -> dict[str, Any]:
        """Return connection parameters from the existing config entry."""
        data = self.config_entry.data
        return {
            "url": data[CONF_URL],
            "username": data.get(CONF_USERNAME, DEFAULT_USERNAME),
            "password": data[CONF_PASSWORD],
            "use_basic_auth": data.get(CONF_BASIC_AUTH, False),
        }

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Entry point for the options flow."""
        return await self.async_step_options(user_input)

    async def async_step_options(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Edit the periodic update interval."""
        if not self._sensors:
            self._sensors = list(self.config_entry.options.get(CONF_SENSORS, []))
        if CONF_UPDATE_INTERVAL not in self._options:
            self._options[CONF_UPDATE_INTERVAL] = self.config_entry.options.get(
                CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL
            )

        if user_input is not None:
            self._options[CONF_UPDATE_INTERVAL] = user_input[CONF_UPDATE_INTERVAL]
            return await self.async_step_sensors()

        return self.async_show_form(
            step_id="options",
            data_schema=_options_schema(
                {CONF_UPDATE_INTERVAL: self._options[CONF_UPDATE_INTERVAL]}
            ),
        )

    async def async_step_finish(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Validate the sensor list and save the options."""
        if not self._sensors:
            return self._show_sensors_error("no_sensors")

        self._options[CONF_SENSORS] = self._sensors
        return self.async_create_entry(title="", data=self._options)
