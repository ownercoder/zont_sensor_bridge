"""Low-level WebSocket client for Zont thermostats."""

import asyncio
import json
from typing import Any

import aiohttp
from aiohttp import BasicAuth, WSMsgType

from .const import LOGGER


class InvalidAuth(Exception):
    """Raised when the Zont device rejects the supplied credentials."""


class ZontClient:
    """Async WebSocket client that connects to a Zont thermostat.

    The client maintains a single reconnect loop. Callers push temperature
    commands via :meth:`send_temp`; if the socket is not currently connected,
    the call returns ``False`` and the caller is responsible for retrying.
    """

    def __init__(
        self,
        hass,
        url: str,
        username: str,
        password: str,
        use_basic_auth: bool,
        logger,
    ) -> None:
        self._hass = hass
        self._url = url
        self._username = username
        self._password = password
        self._use_basic_auth = use_basic_auth
        self._logger = logger

        self._session: aiohttp.ClientSession | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._loop_task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self._auth_event = asyncio.Event()

        self._send_lock = asyncio.Lock()
        self._reconnect_delay = 5

    @property
    def is_authenticated(self) -> bool:
        """Return whether the client is connected and authenticated."""
        return self._auth_event.is_set()

    async def wait_authenticated(self, timeout: float) -> bool:
        """Wait until authentication succeeds or the timeout elapses."""
        try:
            await asyncio.wait_for(self._auth_event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            return False
        return True

    async def start(self) -> None:
        """Open the aiohttp session and spawn the reconnect loop."""
        self._session = aiohttp.ClientSession()
        self._stop_event.clear()
        self._loop_task = self._hass.async_create_background_task(
            self._run_loop(),
            "Zont Sensor Bridge reconnect loop",
        )

    async def stop(self) -> None:
        """Cancel the reconnect loop and close the session cleanly."""
        self._stop_event.set()

        if self._loop_task is not None:
            self._loop_task.cancel()
            try:
                await self._loop_task
            except asyncio.CancelledError:
                pass
            self._loop_task = None

        if self._ws is not None:
            await self._ws.close()
            self._ws = None

        if self._session is not None:
            await self._session.close()
            self._session = None

        self._auth_event.clear()

    async def _run_loop(self) -> None:
        """Reconnect loop with exponential backoff."""
        while not self._stop_event.is_set():
            try:
                await self._connect_and_run()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self._logger.warning("Zont Sensor Bridge connection error: %s", exc)

            if self._stop_event.is_set():
                break

            delay = min(self._reconnect_delay, 60)
            self._logger.debug("Reconnecting in %s seconds", delay)
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass

            self._reconnect_delay = min(self._reconnect_delay * 2, 60)

    async def _connect_and_run(self) -> None:
        """Open a single WebSocket connection and consume messages."""
        auth = (
            BasicAuth(self._username, self._password)
            if self._use_basic_auth
            else None
        )
        self._logger.debug("Connecting to %s", self._url)

        async with self._session.ws_connect(
            self._url,
            auth=auth,
            heartbeat=30.0,
        ) as ws:
            self._ws = ws
            self._auth_event.clear()
            self._reconnect_delay = 5

            await self._authenticate()

            async for msg in ws:
                if self._stop_event.is_set():
                    break
                if msg.type == WSMsgType.TEXT:
                    await self._handle_message(msg.data)
                elif msg.type in (WSMsgType.CLOSED, WSMsgType.ERROR):
                    break

    async def _authenticate(self) -> None:
        """Send JSON credentials and wait for an ``auth: 200`` response."""
        auth_payload: dict[str, Any] = {
            "user": self._username,
            "pass": self._password,
        }
        self._logger.debug("Sending auth for user %s", self._username)
        async with self._send_lock:
            await self._ws.send_json(auth_payload)

        try:
            msg = await asyncio.wait_for(self._ws.receive_json(), timeout=10)
        except asyncio.TimeoutError as exc:
            self._logger.warning("Auth response timeout")
            raise InvalidAuth() from exc

        self._logger.debug("Auth response: %s", msg)

        if isinstance(msg, dict) and msg.get("auth") == 200:
            self._logger.debug("Authenticated successfully")
            self._auth_event.set()
            return

        self._auth_event.clear()
        if isinstance(msg, dict) and msg.get("auth") == 401:
            self._logger.warning("Authentication rejected")
        else:
            self._logger.warning("Unexpected auth response: %s", msg)
        raise InvalidAuth()

    async def _handle_message(self, data: str) -> None:
        """Handle one incoming WebSocket message."""
        try:
            msg = json.loads(data)
        except json.JSONDecodeError:
            self._logger.debug("Received non-JSON message: %s", data)
            return

        self._logger.debug("Received message: %s", msg)

        if not isinstance(msg, dict):
            return

        if msg.get("auth") == 401:
            self._logger.debug("Re-authenticating after auth:401")
            self._auth_event.clear()
            await self._authenticate()
            return

        if "cmdres" in msg:
            self._logger.debug("Command result: %s", msg)

    async def send_temp(self, zont_id: int, value: float) -> bool:
        """Send a temperature command if connected and authenticated.

        Returns ``True`` if the command was written to the socket.
        """
        async with self._send_lock:
            if self._ws is None or not self._auth_event.is_set():
                self._logger.debug(
                    "Dropping send for zont_id=%s (not connected)",
                    zont_id,
                )
                return False

            payload = {"id": zont_id, "cmd": f"1 {value:.1f}"}
            self._logger.debug(
                "Sending temperature command zont_id=%s value=%s",
                zont_id,
                value,
            )

            try:
                await self._ws.send_json(payload)
            except Exception as exc:  # noqa: BLE001
                self._logger.debug("Failed to send temperature command: %s", exc)
                return False

            return True


async def async_test_connection(
    hass,  # noqa: ARG001
    url: str,
    username: str,
    password: str,
    use_basic_auth: bool,
) -> None:
    """Open a short-lived connection to verify credentials.

    Raises ``InvalidAuth`` on authentication failure,
    ``TimeoutError`` on timeout, and ``aiohttp.ClientError`` on transport errors.
    """
    auth = BasicAuth(username, password) if use_basic_auth else None
    session = aiohttp.ClientSession()
    try:
        async with session.ws_connect(url, auth=auth, heartbeat=30.0) as ws:
            auth_payload: dict[str, Any] = {
                "user": username,
                "pass": password,
            }
            await ws.send_json(auth_payload)
            msg = await asyncio.wait_for(ws.receive_json(), timeout=10)
    except asyncio.TimeoutError as exc:
        raise TimeoutError("Authentication response not received") from exc
    finally:
        await session.close()

    if not isinstance(msg, dict) or msg.get("auth") != 200:
        raise InvalidAuth()


async def async_fetch_sensors(
    hass,  # noqa: ARG001
    url: str,
    username: str,
    password: str,
    use_basic_auth: bool,
) -> list[dict[str, Any]]:
    """Fetch type-1 temperature sensors from a Zont device.

    Raises ``InvalidAuth`` on authentication failure,
    ``TimeoutError`` on timeout, and ``aiohttp.ClientError`` on transport errors.
    """
    auth = BasicAuth(username, password) if use_basic_auth else None
    session = aiohttp.ClientSession()
    try:
        async with session.ws_connect(url, auth=auth, heartbeat=30.0) as ws:
            await _authenticate_fetch_session(ws, username, password)
            sensor_ids = await _fetch_sensor_ids(ws)
            return await _fetch_temperature_sensors(ws, sensor_ids)
    except asyncio.TimeoutError as exc:
        raise TimeoutError("Did not receive a response from the Zont device") from exc
    finally:
        await session.close()


async def _authenticate_fetch_session(
    ws: aiohttp.ClientWebSocketResponse,
    username: str,
    password: str,
) -> None:
    """Send JSON credentials and wait for ``auth: 200`` during sensor discovery."""
    await ws.send_json({"user": username, "pass": password})
    msg = await asyncio.wait_for(ws.receive_json(), timeout=10)
    if not isinstance(msg, dict) or msg.get("auth") != 200:
        raise InvalidAuth()


async def _fetch_temperature_sensors(
    ws: aiohttp.ClientWebSocketResponse,
    sensor_ids: list[int],
) -> list[dict[str, Any]]:
    """Query each sensor id and keep only temperature sensors."""
    sensors: list[dict[str, Any]] = []
    for sensor_id in sensor_ids:
        sensor = await _fetch_sensor_state(ws, sensor_id)
        if sensor is not None:
            sensors.append(sensor)
    return sensors


async def _fetch_sensor_ids(ws: aiohttp.ClientWebSocketResponse) -> list[int]:
    """Request the list of sensor ids and return only integer ids."""
    await ws.send_json({"req_ids": 1})
    msg = await asyncio.wait_for(ws.receive_json(), timeout=10)
    if not isinstance(msg, dict):
        return []
    ids = msg.get("ids", [])
    if not isinstance(ids, list):
        return []
    return [sensor_id for sensor_id in ids if isinstance(sensor_id, int)]


async def _fetch_sensor_state(
    ws: aiohttp.ClientWebSocketResponse,
    sensor_id: int,
) -> dict[str, Any] | None:
    """Request state for one sensor and return it if it is a temperature sensor."""
    await ws.send_json({"id": sensor_id, "req_state": 0})
    msg = await _receive_matching_state(ws, sensor_id)
    if msg is None:
        return None
    if msg.get("type") != 1:
        return None
    name = msg.get("name")
    if not name:
        name = str(sensor_id)
    return {"id": sensor_id, "name": name}


async def _receive_matching_state(
    ws: aiohttp.ClientWebSocketResponse,
    sensor_id: int,
) -> dict[str, Any] | None:
    """Wait for the state response that matches the requested sensor id."""
    while True:
        msg = await asyncio.wait_for(ws.receive_json(), timeout=10)
        if isinstance(msg, dict) and msg.get("id") == sensor_id:
            return msg
