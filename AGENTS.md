# AGENTS.md — Instructions for AI Coding Agents

## Project overview

Zont Sensor Bridge (`custom_components/zont_sensor_bridge/`) is a Home Assistant custom integration, domain `zont_sensor_bridge`, that acts as a strictly one-way bridge from Home Assistant to a Zont thermostat. It subscribes to state changes of user-configured HA `sensor` entities (`device_class: temperature`) and pushes their values to the thermostat over the Zont device's local WebSocket connection, re-sending all current values on a periodic update interval. During setup the integration fetches the Zont temperature-sensor list (`type: 1`) over the same WebSocket, and the user maps each Home Assistant sensor to a discovered Zont sensor by name. It exposes no sensor entities of its own. Connection details are stored in the config entry's `data`; mutable settings (`update_interval`, sensor mappings) live in `entry.options`, with automatic reload on options change.

## Mandatory philosophy

Before writing or modifying backend/logic code in this repository, load the `code-philosophy` skill and verify the implementation against it. In particular:

- Keep functions single-responsibility — one concern per function.
- Prefer guard clauses over nested conditionals.
- Validate all inputs at the boundaries (config flow, service calls, incoming sensor states), not deep inside the logic.
- Use descriptive names for functions, coroutine names, and variables.

## Hard rules

- The integration domain is fixed at `zont_sensor_bridge`. Do not rename it. The logger name resolves automatically from `__package__` (`const.py` holds the shared logger/`\_LOGGER` and constants).
- Never log the password or the full auth payload (`{"user": ..., "pass": ...}`). Log a redacted form when debugging authentication is needed. DEBUG logging of other raw JSON is acceptable and useful for protocol inspection.

## File map

| File | Purpose |
|------|---------|
| `manifest.json` | Integration metadata: `domain: zont_sensor_bridge`, `config_flow: true`, `iot_class: local_push`, no extra requirements, HA 2024.1.0+ |
| `const.py` | Shared constants and the logger |
| `client.py` | Low-level WebSocket client (`ZontClient`): connect → JSON auth → send temperature; reconnect loop with exponential backoff 5–60 s; WS heartbeat 30 s; re-auth on mid-session `auth: 401`. Also short-lived `async_test_connection()` and `async_fetch_sensors()` helpers for setup-time connection testing and sensor discovery |
| `config_flow.py` | UI config: `ZontSensorBridgeConfigFlow` and `ZontSensorBridgeOptionsFlow`; fetches the Zont type-1 sensor list over WebSocket and maps it to HA temperature entities |
| `__init__.py` | Lifecycle, `ZontData` orchestration, state-change subscription, periodic send, service registration |
| `translations/en.json` | English translations |
| `translations/ru.json` | Russian translations |
| `services.yaml` | Service definition (`zont_sensor_bridge.send_temperature`) |
| `hacs.json` | HACS metadata |

At the repository root (outside `custom_components/`): `icon.svg` (brand icon), plus these docs. The component also ships `brand/icon.png` (256×256) and `brand/icon@2x.png` (512×512) inside `custom_components/zont_sensor_bridge/` for the Home Assistant frontend brand icon (HA 2026.3+).

## Structure conventions

- Connection details (WebSocket URL, username, password, HTTP Basic Auth toggle) are stored in `entry.data` and must not change at runtime.
- Mutable settings — `update_interval` and the sensor mappings list — are stored in `entry.options` and edited via `ZontSensorBridgeOptionsFlow`.
- Each sensor mapping is a dict `{entity_id, zont_id, zont_name, min?, max?}`. `zont_id` is the Zont sensor id discovered via `req_ids`/`req_state`; `zont_name` is stored for display only and ignored at runtime.
- The options flow must extend `OptionsFlowWithReload` (not plain `OptionsFlow`) so Home Assistant automatically reloads the config entry when options change. Do not add an update listener (`entry.add_update_listener`) — an `OptionsFlowWithReload` raises an error when a listener is present.
- Changing connection details requires re-setup of the entry (new config flow run), since they live in `entry.data`.

## Protocol reference

Verified against a `wscat` capture and the official Zont MQTT docs. The local WebSocket API is not publicly documented, so treat this protocol as the source of truth:

1. Connect to the user-supplied URL (`ws://`/`wss://`). Optional HTTP Basic Auth on the handshake.
2. Send `{"user": "<username>", "pass": "<password>"}`; await `{"auth": 200}`. `{"auth": 401}` means rejection; a mid-session `401` triggers re-auth.
3. Discover temperature sensors: send `{"req_ids": 1}`; the reply `{"ids": [<int>, ...]}` lists type-1 (digital/temperature) sensor ids. For each id, send `{"id": <id>, "req_state": 0}`; the reply is the sensor state, e.g. `{"id": 8239, "type": 1, "name": "Погода из интернета", "t": 5.8, "a": 1, "trig": 0}`. Keep only entries with `type: 1`; use `name` for the UI list.
4. Send `{"id": <sensor_id>, "cmd": "1 <temp>"}`; success is `{"id": <sensor_id>, "cmdres": 0}`. `sensor_id` is the user-mapped Zont sensor id; `temp` is Celsius as a float with one decimal place; the leading `1` in `cmd` is the command type for digital (type-1) sensors (e.g. `"1 13.4"`).

Temperature is always Celsius — convert °F source values before sending.

## Verification checklist

Before declaring any change done:

1. Byte-compile the integration:

   ```sh
   python3 -m compileall -q custom_components/zont_sensor_bridge
   ```

2. Validate JSON files parse correctly: `manifest.json`, `translations/en.json`, `translations/ru.json`, `hacs.json`.
3. Validate `services.yaml` is valid YAML and follows the HA service schema.
4. Confirm translations: every key used by the config flow exists in both `translations/en.json` and `translations/ru.json`.

## Repository hygiene

- The HACS brand icon is `icon.svg` at the repository root. Home Assistant (2026.3+) brand icons live in `custom_components/zont_sensor_bridge/brand/` as PNGs (`icon.png` 256×256, `icon@2x.png` 512×512). These PNGs are the ONLY binary files in the repo and are generated from the root `icon.svg`. Do not create any other binary files.
- Do not edit files outside `custom_components/` (documentation excluded from this restriction), and keep the integration self-contained — no new files under `custom_components/` without a clear reason.
