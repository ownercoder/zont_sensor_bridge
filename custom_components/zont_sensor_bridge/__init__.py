"""The Zont Sensor Bridge integration."""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.util.unit_conversion import TemperatureConverter

from .client import ZontClient
from .const import (
    CONF_BASIC_AUTH,
    CONF_ENTITY_ID,
    CONF_MAX,
    CONF_MIN,
    CONF_PASSWORD,
    CONF_SENSORS,
    CONF_UPDATE_INTERVAL,
    CONF_URL,
    CONF_USERNAME,
    CONF_ZONT_ID,
    DEFAULT_UPDATE_INTERVAL,
    DEFAULT_USERNAME,
    DOMAIN,
    LOGGER,
)


@dataclass
class SensorBinding:
    """Mapping between a Home Assistant sensor and a Zont sensor id."""

    entity_id: str
    zont_id: int
    min: float | None
    max: float | None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SensorBinding":
        """Parse a raw sensor options dict into a trusted binding."""
        return cls(
            entity_id=data[CONF_ENTITY_ID],
            zont_id=int(data[CONF_ZONT_ID]),
            min=data.get(CONF_MIN),
            max=data.get(CONF_MAX),
        )


SERVICE_SEND_TEMPERATURE = "send_temperature"

SERVICE_SEND_TEMPERATURE_SCHEMA = vol.Schema(
    {
        vol.Required("device_id"): cv.string,
        vol.Required("zont_id"): vol.Coerce(int),
        vol.Required("temperature"): vol.Coerce(float),
    }
)


def _host_from_url(url: str) -> str:
    """Return the network location part of a WebSocket URL."""
    url = url.removeprefix("ws://").removeprefix("wss://")
    return url.split("/", 1)[0]


def _entry_id_from_device(device: dr.DeviceEntry) -> str | None:
    """Find the Zont Sensor Bridge config entry id stored on a device."""
    for domain, entry_id in device.identifiers:
        if domain == DOMAIN:
            return entry_id
    return None


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Set up the Zont Sensor Bridge domain and register services."""

    async def handle_send_temperature(call: ServiceCall) -> None:
        """Service handler that sends a temperature command to a device."""
        device_id = call.data["device_id"]
        zont_id = call.data["zont_id"]
        temperature = call.data["temperature"]

        device_registry = dr.async_get(hass)
        device = device_registry.async_get(device_id)
        if device is None:
            raise HomeAssistantError(f"Device {device_id} not found")

        entry_id = _entry_id_from_device(device)
        if entry_id is None:
            raise HomeAssistantError(f"Device {device_id} is not a Zont Sensor Bridge device")

        data = hass.data.get(DOMAIN, {}).get(entry_id)
        if data is None:
            raise HomeAssistantError(f"Zont Sensor Bridge device {device_id} is not loaded")

        await data.client.send_temp(zont_id, temperature)

    hass.services.async_register(
        DOMAIN,
        SERVICE_SEND_TEMPERATURE,
        handle_send_temperature,
        schema=SERVICE_SEND_TEMPERATURE_SCHEMA,
    )

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a Zont Sensor Bridge config entry."""
    url = entry.data[CONF_URL]
    username = entry.data.get(CONF_USERNAME, DEFAULT_USERNAME)
    password = entry.data[CONF_PASSWORD]
    use_basic_auth = entry.data.get(CONF_BASIC_AUTH, False)

    update_interval = entry.options.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL)
    raw_sensors = entry.options.get(CONF_SENSORS, [])

    bindings = [SensorBinding.from_dict(sensor) for sensor in raw_sensors]

    client = ZontClient(hass, url, username, password, use_basic_auth, LOGGER)
    await client.start()

    data = ZontData(hass, client, bindings, update_interval)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = data

    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name=_host_from_url(url),
        manufacturer="Zont",
        model="Zont thermostat",
    )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a Zont Sensor Bridge config entry."""
    data = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if data is not None:
        await data.async_unload()
    return True


class ZontData:
    """Runtime state for one Zont Sensor Bridge config entry."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: ZontClient,
        bindings: list[SensorBinding],
        update_interval: int,
    ) -> None:
        self.hass = hass
        self.client = client
        self.bindings = bindings
        self.update_interval = update_interval

        self._remove_listener: Callable[[], None] | None = None
        self._periodic_task: asyncio.Task | None = None

        self._start_tracking()

    def _start_tracking(self) -> None:
        """Subscribe to state changes and start the periodic re-send loop."""
        entity_ids = [binding.entity_id for binding in self.bindings]
        if entity_ids:
            self._remove_listener = async_track_state_change_event(
                self.hass,
                entity_ids,
                self._on_state_change,
            )

        self._periodic_task = self.hass.async_create_background_task(
            self._periodic_loop(),
            "Zont Sensor Bridge periodic send",
        )

    async def _on_state_change(self, event: Any) -> None:
        """Push the new temperature when a bound sensor changes."""
        entity_id = event.data.get("entity_id")
        binding = next(
            (b for b in self.bindings if b.entity_id == entity_id),
            None,
        )
        if binding is None:
            return

        await self._send_binding(binding)

    async def _periodic_loop(self) -> None:
        """Re-send all bound temperatures on a fixed interval."""
        while True:
            await self._send_all()
            await asyncio.sleep(self.update_interval)

    async def _send_all(self) -> None:
        """Send the current value for every bound sensor."""
        for binding in self.bindings:
            await self._send_binding(binding)

    async def _send_binding(self, binding: SensorBinding) -> None:
        """Read, clamp, convert and send one sensor value."""
        try:
            value = self._read_temperature(binding)
        except ValueError as exc:
            LOGGER.debug(
                "Skipping send for %s: %s",
                binding.entity_id,
                exc,
            )
            return

        if value is None:
            return

        try:
            await self.client.send_temp(binding.zont_id, value)
        except Exception as exc:  # noqa: BLE001
            LOGGER.debug(
                "Failed to send temperature for %s: %s",
                binding.entity_id,
                exc,
            )

    def _read_temperature(self, binding: SensorBinding) -> float | None:
        """Return the current temperature for a binding in Celsius.

        Raises ``ValueError`` for states that cannot be sent.
        """
        state = self.hass.states.get(binding.entity_id)
        if state is None:
            return None
        if state.state in ("unknown", "unavailable"):
            return None

        try:
            value = float(state.state)
        except ValueError as exc:
            raise ValueError(f"State is not numeric: {state.state}") from exc

        if binding.min is not None:
            value = max(value, binding.min)
        if binding.max is not None:
            value = min(value, binding.max)

        unit = state.attributes.get("unit_of_measurement")
        if unit is not None and unit != UnitOfTemperature.CELSIUS:
            try:
                value = TemperatureConverter.convert(
                    value,
                    unit,
                    UnitOfTemperature.CELSIUS,
                )
            except ValueError as exc:
                raise ValueError(f"Unsupported temperature unit: {unit}") from exc

        return value

    async def async_unload(self) -> None:
        """Stop tracking and close the WebSocket client."""
        if self._periodic_task is not None:
            self._periodic_task.cancel()
            try:
                await self._periodic_task
            except asyncio.CancelledError:
                pass
            self._periodic_task = None

        if self._remove_listener is not None:
            self._remove_listener()
            self._remove_listener = None

        await self.client.stop()
