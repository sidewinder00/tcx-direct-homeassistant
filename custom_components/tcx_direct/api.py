from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
import secrets
import string
import time
from collections import deque
from collections.abc import Awaitable, Callable
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import aiohttp

from .const import (
    API_KEY,
    BOOTSTRAP_SUBSCRIBE_ATTEMPTS,
    BOOTSTRAP_SUBSCRIBE_INTERVAL,
    CONTROL_CONFIRM_TIMEOUT,
    CONTROL_NAMESPACE,
    IAQUALINK_API,
    MAX_WEBSOCKET_SESSION,
    RECENT_WS_STRUCTURES,
    RECONNECT_MAX,
    SHADOW_INTERVAL,
    SHADOW_RATE_LIMIT_MAX_INTERVAL,
    TOKEN_REFRESH_MARGIN,
    WATCHDOG_RESUBSCRIBE_TIMEOUT,
    WEBSOCKET_STALE_SECONDS,
    WEBSOCKET_URL,
    ZODIAC_API,
)
from .redaction import safe_structure_key, sanitize_diagnostics

_LOGGER = logging.getLogger(__name__)


class TCXError(Exception):
    """Base TCX error."""


class TCXAuthError(TCXError):
    """Authentication failed."""


class TCXConnectionError(TCXError):
    """Network or service connection failed."""


class TCXRateLimited(TCXConnectionError):
    """A Zodiac endpoint asked the client to reduce its request rate."""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class TCXDeviceNotFound(TCXError):
    """No matching TCX device was found."""


class TCXShadowUnsupported(TCXError):
    """The TCX controller does not expose the Zodiac REST shadow endpoint."""


class TCXControlUnsupported(TCXError):
    """The requested equipment control is not available on this controller."""


@dataclass(slots=True)
class TCXDevice:
    serial: str
    name: str
    device_type: str


@dataclass(slots=True)
class _PendingControl:
    description: str
    predicate: Callable[[dict[str, Any]], bool]
    future: asyncio.Future[None]


StateCallback = Callable[[dict[str, Any], str], Awaitable[None]]
StatusCallback = Callable[[], Awaitable[None]]


def _deep_merge(target: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge source into target and return target."""
    for key, value in source.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_merge(target[key], value)
        else:
            target[key] = deepcopy(value)
    return target


def _extract_reported(data: Any) -> dict[str, Any] | None:
    """Find a state.reported dictionary inside Zodiac responses."""
    if not isinstance(data, dict):
        return None

    state = data.get("state")
    if isinstance(state, dict) and isinstance(state.get("reported"), dict):
        return state["reported"]

    for value in data.values():
        if isinstance(value, dict):
            result = _extract_reported(value)
            if result is not None:
                return result
        elif isinstance(value, list):
            for item in value:
                result = _extract_reported(item)
                if result is not None:
                    return result
    return None


def _collect_reported(data: Any) -> dict[str, Any] | None:
    """Merge every state.reported dictionary found in a Zodiac message.

    The TCX authorization snapshot contains several namespace documents
    (main/ecm/filt/fea/...) in one payload. Returning only the first
    state.reported block loses most of the controller state.
    """
    merged: dict[str, Any] = {}

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            state = value.get("state")
            if isinstance(state, dict) and isinstance(state.get("reported"), dict):
                _deep_merge(merged, state["reported"])
            for child in value.values():
                if isinstance(child, (dict, list)):
                    walk(child)
        elif isinstance(value, list):
            for child in value:
                if isinstance(child, (dict, list)):
                    walk(child)

    walk(data)
    return merged or None


def _extract_device_timestamp(data: Any) -> int | None:
    """Extract a useful Zodiac device timestamp when present."""
    if not isinstance(data, dict):
        return None
    for key in ("timestamp", "ts"):
        value = data.get(key)
        if isinstance(value, (int, float)):
            return int(value)
    payload = data.get("payload")
    if isinstance(payload, dict):
        for key in ("timestamp", "ts"):
            value = payload.get(key)
            if isinstance(value, (int, float)):
                return int(value)
    reported = _extract_reported(data)
    if isinstance(reported, dict):
        aws = reported.get("aws")
        if isinstance(aws, dict):
            value = aws.get("timestamp")
            if isinstance(value, (int, float)):
                return int(value)
    return None


def _norm(text: str) -> str:
    return "".join(ch for ch in text.casefold() if ch.isalnum())


def _flatten(data: Any, prefix: tuple[str, ...] = ()) -> list[tuple[tuple[str, ...], Any]]:
    output: list[tuple[tuple[str, ...], Any]] = []
    if isinstance(data, dict):
        for key, value in data.items():
            path = (*prefix, str(key))
            if isinstance(value, (dict, list)):
                output.extend(_flatten(value, path))
            else:
                output.append((path, value))
    elif isinstance(data, list):
        for idx, value in enumerate(data):
            output.extend(_flatten(value, (*prefix, str(idx))))
    return output


def _coerce_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = value.strip().replace("%", "")
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


def _coerce_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        val = value.strip().casefold()
        if val in {"on", "true", "1", "running", "enabled", "active"}:
            return True
        if val in {"off", "false", "0", "stopped", "disabled", "inactive"}:
            return False
    return None


def _find_terminal(
    flat: list[tuple[tuple[str, ...], Any]],
    aliases: set[str],
    *,
    path_contains: set[str] | None = None,
) -> Any:
    """Find a value by normalized terminal key, optionally requiring path hints."""
    for path, value in flat:
        terminal = _norm(path[-1])
        normalized_path = {_norm(part) for part in path}
        if terminal in aliases and (
            path_contains is None or normalized_path.intersection(path_contains)
        ):
            return value
    return None


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _utc_now_iso() -> str:
    """Return an ISO UTC timestamp for diagnostics."""
    return datetime.now(timezone.utc).isoformat()


def _structure_paths(data: Any, prefix: tuple[str, ...] = ()) -> list[str]:
    """Return value-free JSON paths for WebSocket structure diagnostics."""
    paths: list[str] = []
    if isinstance(data, dict):
        if not data:
            paths.append(".".join(prefix) + ".{}" if prefix else "{}")
        for key, value in data.items():
            paths.extend(_structure_paths(value, (*prefix, safe_structure_key(key))))
    elif isinstance(data, list):
        if not data:
            paths.append(".".join((*prefix, "[]")))
        else:
            # One representative list-item structure is enough for a schema
            # fingerprint and avoids huge diagnostics for metadata arrays.
            paths.extend(_structure_paths(data[0], (*prefix, "[]")))
    else:
        paths.append(".".join(prefix))
    return paths


def _ws_structure(data: dict[str, Any]) -> dict[str, Any]:
    """Describe a WebSocket message without retaining its scalar values."""
    paths = sorted(set(_structure_paths(data)))
    fingerprint_source = "\n".join(paths).encode("utf-8", errors="replace")
    fingerprint = hashlib.sha256(fingerprint_source).hexdigest()[:16]
    payload = data.get("payload") if isinstance(data.get("payload"), dict) else {}
    state = payload.get("state") if isinstance(payload.get("state"), dict) else {}
    return {
        "fingerprint": fingerprint,
        "service": data.get("service"),
        "event": data.get("event"),
        "namespace": data.get("namespace"),
        "action": data.get("action"),
        "root_keys": sorted(str(key) for key in data.keys()),
        "payload_keys": sorted(str(key) for key in payload.keys()),
        "state_keys": sorted(str(key) for key in state.keys()),
        "paths": paths[:160],
        "path_count": len(paths),
    }


def _tcx_temperature(value: Any) -> float | None:
    """Decode common TCX temperature representations to Fahrenheit.

    TCX live state may report temperatures directly in Fahrenheit on some
    firmware and as tenths of a degree Celsius on others. Values above the
    normal direct-Fahrenheit range are treated as tenths-Celsius.
    """
    number = _coerce_number(value)
    if number is None:
        return None
    if 150 <= number <= 600:
        return round((number / 10.0) * 9.0 / 5.0 + 32.0, 1)
    if -40 <= number <= 150:
        return round(number, 1)
    return None


def _find_in_named_object(
    reported: dict[str, Any],
    object_prefixes: tuple[str, ...],
    field_names: tuple[str, ...],
) -> Any:
    """Find a field inside a TCX object whose key starts with a known prefix."""
    prefixes = tuple(_norm(prefix) for prefix in object_prefixes)
    fields = {_norm(field) for field in field_names}
    for key, obj in reported.items():
        if not isinstance(obj, dict):
            continue
        if not _norm(str(key)).startswith(prefixes):
            continue
        for field, value in obj.items():
            if _norm(str(field)) in fields:
                return value
    return None


def _find_waterfall_feature(
    reported: dict[str, Any],
) -> tuple[str, dict[str, Any]] | None:
    """Return the confirmed TCX waterfall feature-relay object.

    Friendly names can be customized, so identify the equipment using the
    controller's FRLY/WF type pair. Requiring both markers prevents an opaque
    fcr object from being treated as a waterfall by position alone.
    """
    for key, value in reported.items():
        if not isinstance(value, dict):
            continue
        if _norm(str(value.get("et", ""))) != "frly":
            continue
        if _norm(str(value.get("app", ""))) != "wf":
            continue
        return str(key), value
    return None


def _find_pool_mode(
    reported: dict[str, Any],
) -> tuple[str, dict[str, Any]] | None:
    """Return the confirmed Pool Filtration mode object."""
    for key, value in reported.items():
        if not isinstance(value, dict):
            continue
        if _norm(str(value.get("et", ""))) != "vpos":
            continue
        if _norm(str(value.get("app", ""))) != "poolm":
            continue
        return str(key), value
    return None


def _find_filter_controller(
    reported: dict[str, Any],
) -> tuple[str, dict[str, Any]] | None:
    """Return the confirmed filtration-controller object."""
    for key, value in reported.items():
        if not isinstance(value, dict):
            continue
        if _norm(str(value.get("et", ""))) != "fctrl":
            continue
        if _norm(str(value.get("app", ""))) != "filt":
            continue
        return str(key), value
    return None


def _find_pool_light(
    reported: dict[str, Any],
) -> tuple[str, dict[str, Any]] | None:
    """Return the confirmed Jandy pool-light object."""
    for key, value in reported.items():
        if not isinstance(value, dict):
            continue
        if _norm(str(value.get("et", ""))) != "jl":
            continue
        if _norm(str(value.get("app", ""))) != "poollt":
            continue
        return str(key), value
    return None


def build_set_state_message(
    device_id: str,
    user_id: str,
    namespace: str,
    desired: dict[str, Any],
    *,
    client_token: str | None = None,
) -> dict[str, Any]:
    """Build the Zodiac WebSocket setState message used by current clients."""
    alphabet = string.ascii_letters + string.digits
    token = client_token or "|".join(
        (
            user_id,
            "".join(secrets.choice(alphabet) for _ in range(22)),
            "".join(secrets.choice(alphabet) for _ in range(22)),
        )
    )
    return {
        "action": "setState",
        "version": 1,
        "namespace": namespace,
        "payload": {
            "state": {"desired": deepcopy(desired)},
            "clientToken": token,
        },
        "service": "StateController",
        "target": device_id,
    }


def merge_normalized_state(
    current: dict[str, Any], parsed: dict[str, Any]
) -> dict[str, Any]:
    """Merge a sparse normalized update while clearing invalid derived values."""
    merged = deepcopy(current)
    for key, value in parsed.items():
        if value is not None:
            merged[key] = value

    if parsed.get("light") is False:
        merged.pop("light_color", None)
        merged.pop("light_color_name", None)
    if parsed.get("pump") is False:
        merged.pop("pump_preset", None)
    return merged


def normalize_tcx_state(reported: dict[str, Any]) -> dict[str, Any]:
    """Normalize the TCX fields observed from AquaLink TCX 5.x."""
    flat = _flatten(reported)

    # ---- Variable-speed pump ----------------------------------------------
    # Live ECM updates distinguish the motor's active command (cmdSpd) from
    # the requested preset speed (reqSpd). During priming these intentionally
    # differ, so keep them separate: cmdSpd drives the live RPM sensor while
    # reqSpd identifies the selected preset. filt0 remains a fallback.
    ecm0 = _mapping(reported.get("ecm0"))
    filt0 = _mapping(reported.get("filt0"))
    pump_obj = ecm0 or filt0
    pump_state = _coerce_bool(pump_obj.get("st"))
    pump_rpm = _coerce_number(ecm0.get("cmdSpd"))
    if pump_rpm is None:
        pump_rpm = _coerce_number(ecm0.get("reqSpd"))
    if pump_rpm is None:
        pump_rpm = _coerce_number(ecm0.get("manSpd"))
    if pump_rpm is None:
        pump_rpm = _coerce_number(filt0.get("manSpd"))
    if pump_state is False:
        pump_rpm = 0.0

    min_rpm = _coerce_number(ecm0.get("minSpd"))
    if min_rpm is None:
        min_rpm = _coerce_number(filt0.get("minSpd"))
    max_rpm = _coerce_number(ecm0.get("maxSpd"))
    if max_rpm is None:
        max_rpm = _coerce_number(filt0.get("maxSpd"))

    requested_rpm = _coerce_number(ecm0.get("reqSpd"))
    if requested_rpm is None:
        requested_rpm = _coerce_number(ecm0.get("manSpd"))
    if requested_rpm is None:
        requested_rpm = _coerce_number(filt0.get("manSpd"))

    # The writable control represents the manual filtration setpoint, not the
    # motor's active command. cmdSpd can legitimately move through priming,
    # filtration, and schedule-selected speeds without a manual-speed write.
    # Keep those live changes isolated to Pump RPM.
    pump_speed_setpoint = _coerce_number(filt0.get("manSpd"))
    if pump_speed_setpoint is None:
        pump_speed_setpoint = _coerce_number(ecm0.get("manSpd"))
    if pump_speed_setpoint is None:
        pump_speed_setpoint = requested_rpm
    pool_mode = _find_pool_mode(reported)
    pump_power_setpoint = (
        _coerce_bool(pool_mode[1].get("st")) if pool_mode is not None else None
    )
    pump_power_control_supported = pool_mode is not None
    pump_speed_control_supported = (
        _find_filter_controller(reported) is not None
        and min_rpm is not None
        and max_rpm is not None
    )

    pump_preset = None
    if pump_state is not False and requested_rpm is not None:
        speed_list = ecm0.get("spdList") or filt0.get("spdList") or []
        if isinstance(speed_list, list):
            for preset in speed_list:
                if not isinstance(preset, dict):
                    continue
                speed = _coerce_number(preset.get("speed"))
                if speed is not None and round(speed) == round(requested_rpm):
                    name = preset.get("name")
                    if name:
                        pump_preset = str(name)
                        break
        if pump_preset is None:
            pump_preset = "Manual"

    # ---- Water temperature -------------------------------------------------
    # Observed TCX payload: water: {value: 328, us: 1} => 32.8 C => 91.0 F.
    # Keep the unit/status member available for future protocol refinement but
    # decode the value using the established tenths-Celsius representation.
    water = _mapping(reported.get("water"))
    pool_temperature = _tcx_temperature(water.get("value"))
    if pool_temperature is None:
        pool_temp_raw = _find_terminal(
            flat,
            {"watertemp", "watertemperature", "pooltemp", "pooltemperature", "currentwatertemp"},
        )
        pool_temperature = _tcx_temperature(pool_temp_raw)

    # ---- Air/outdoor temperature ------------------------------------------
    # hubAir on this controller reports an encoded/sentinel value (e.g. -1311
    # with us=4), so do not present it as degrees. If a proper live `air`
    # object appears later, decode that instead.
    air_temperature = None
    air = _mapping(reported.get("air"))
    if air:
        air_temperature = _tcx_temperature(air.get("value"))
    if air_temperature is None:
        air_temp_raw = _find_terminal(
            flat,
            {"airtemp", "airtemperature", "ambienttemp", "ambienttemperature", "currentairtemp"},
        )
        air_temperature = _tcx_temperature(air_temp_raw)

    # ---- Pool light --------------------------------------------------------
    pool_light = _find_pool_light(reported)
    pool_light_state = pool_light[1] if pool_light is not None else {}
    light_state = _coerce_bool(pool_light_state.get("st"))
    light_color_raw = _coerce_number(pool_light_state.get("currClr"))
    light_color = int(light_color_raw) if light_color_raw is not None else None

    # currClr falls back to 0 while this controller's light is off, while
    # cmdClr/svdClr may retain the previous selection. Do not expose a stale or
    # contradictory current color for equipment that is explicitly off.
    if light_state is False:
        light_color = None

    # AquaLink's P-Series/IntelliBrite emulation sequence. This controller's
    # captured state confirms currClr=3 is reported by the legacy client as
    # Romance.
    pseries_colors = {
        1: "SAm Mode",
        2: "Party Mode",
        3: "Romance",
        4: "Caribbean",
        5: "American",
        6: "California Sunset",
        7: "Royal",
        8: "Blue",
        9: "Green",
        10: "Red",
        11: "White",
        12: "Magenta",
        13: "Hold",
        14: "Recall",
    }
    light_color_name = pseries_colors.get(light_color)

    # ---- Optional salt-water chlorinator ----------------------------------
    # Only expose a value when the controller actually reports an object that
    # identifies itself as salt/chlorinator equipment. Do not assume fcr0 is
    # an SWG simply from its opaque TCX key.
    swc_raw = _find_in_named_object(
        reported,
        ("swc", "chlor", "salt"),
        ("pct", "percent", "level", "output", "production", "productionPercent", "setpoint"),
    )

    # ---- Waterfall feature relay ------------------------------------------
    # Captured official-client traffic identifies the waterfall as an FRLY/WF
    # object and toggles fcr0.st between 1 and 0. The object key is discovered
    # rather than assumed so compatible controllers can number features
    # differently.
    waterfall_feature = _find_waterfall_feature(reported)
    waterfall_state = (
        _coerce_bool(waterfall_feature[1].get("st"))
        if waterfall_feature is not None
        else None
    )

    # ---- Useful diagnostics/configuration ---------------------------------
    wifi_rssi = _coerce_number(reported.get("connectionRSSI"))
    water_setpoint = None
    tsp = _mapping(reported.get("TspBdy0"))
    if "waterTempSet" in tsp:
        water_setpoint = _tcx_temperature(tsp.get("waterTempSet"))

    return {
        "pool_temperature": pool_temperature,
        "pool_temperature_setpoint": water_setpoint,
        "air_temperature": air_temperature,
        "pump_rpm": pump_rpm,
        "pump_min_rpm": min_rpm,
        "pump_max_rpm": max_rpm,
        "pump_speed_setpoint": pump_speed_setpoint,
        "pump_power_setpoint": pump_power_setpoint,
        "pump_power_control_supported": pump_power_control_supported,
        "pump_speed_control_supported": pump_speed_control_supported,
        "light_power_setpoint": light_state,
        "light_control_supported": pool_light is not None,
        "pump_preset": pump_preset,
        "swc_level": _coerce_number(swc_raw),
        "light_color": light_color,
        "light_color_name": light_color_name,
        "pump": pump_state,
        "light": light_state,
        "waterfall": waterfall_state,
        "wifi_rssi": wifi_rssi,
        "firmware_version": reported.get("firmwareVersion"),
        "connection_type": reported.get("connectionType"),
    }


class TCXClient:
    """Direct Zodiac/iAquaLink client for an AquaLink TCX."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        username: str,
        password: str,
        device_id: str | None = None,
    ) -> None:
        self._session = session
        self.username = username
        self._password = password
        self.device_id = device_id

        self.user_id: str | None = None
        self.authentication_token: str | None = None
        self.id_token: str | None = None
        self.refresh_token: str | None = None
        self._token_expires_at = 0.0

        self.reported: dict[str, Any] = {}
        self.websocket_connected = False
        self.cloud_reachable = False
        self.last_error: str | None = None
        self.last_ws_reported_monotonic: float | None = None
        self.last_ws_device_timestamp: int | None = None
        self.shadow_supported: bool | None = None
        self._ws_opened_monotonic: float | None = None

        # WebSocket instrumentation. These counters intentionally survive
        # reconnects for the lifetime of the HA config-entry session so a
        # downloaded diagnostic shows whether the stream has been healthy.
        self.ws_messages_received = 0
        self.ws_text_messages_received = 0
        self.ws_json_messages_received = 0
        self.ws_state_messages_received = 0
        self.ws_desired_messages_received = 0
        self.ws_reported_messages_received = 0
        self.ws_non_state_messages_received = 0
        self.websocket_connect_count = 0
        self.websocket_reconnect_count = 0
        self.watchdog_reconnect_count = 0
        self.manual_reconnect_count = 0
        self.reconnect_reason_counts: dict[str, int] = {}
        self.authorization_subscribe_count = 0
        self.authorization_snapshot_count = 0
        self.bootstrap_resubscribe_count = 0
        self.full_login_count = 0
        self.auth_refresh_count = 0
        self.shadow_request_count = 0
        self.shadow_success_count = 0
        self.shadow_failure_count = 0
        self.shadow_rate_limit_count = 0
        self.shadow_poll_interval = SHADOW_INTERVAL
        self.last_shadow_error: str | None = None
        self.last_shadow_rate_limited_at: str | None = None
        self.watchdog_resubscribe_count = 0
        self.watchdog_resubscribe_success_count = 0
        self.watchdog_resubscribe_failure_count = 0
        self.last_ws_message_at: str | None = None
        self.last_ws_state_at: str | None = None
        self.last_shadow_update_at: str | None = None
        self.last_websocket_opened_at: str | None = None
        self.last_authorization_snapshot_at: str | None = None
        self.last_reconnect_reason: str | None = None
        self.last_ws_message_type: str | None = None
        self.last_ws_service: str | None = None
        self.last_ws_event: str | None = None
        self.last_ws_namespace: str | None = None
        self.last_ws_target: str | None = None
        self.last_ws_payload: dict[str, Any] | None = None
        self._recent_ws_structures: deque[dict[str, Any]] = deque(
            maxlen=RECENT_WS_STRUCTURES
        )
        self._ws_structure_counts: dict[str, int] = {}
        self._recent_desired_payloads: deque[dict[str, Any]] = deque(maxlen=20)

        self._state_callback: StateCallback | None = None
        self._status_callback: StatusCallback | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._tasks: list[asyncio.Task[Any]] = []
        self._stopping = False
        self._reconnect_requested = asyncio.Event()
        self._authorization_snapshot_event = asyncio.Event()
        self._auth_lock = asyncio.Lock()
        self._control_lock = asyncio.Lock()
        self._pending_control: _PendingControl | None = None

        self.control_command_count = 0
        self.control_success_count = 0
        self.control_failure_count = 0
        self.control_command_counts: dict[str, int] = {}
        self.control_success_counts: dict[str, int] = {}
        self.control_failure_counts: dict[str, int] = {}
        self.last_control_at: str | None = None
        self.last_control_description: str | None = None
        self.last_control_error: str | None = None
        self.last_control_frame: dict[str, Any] | None = None

    def set_callbacks(
        self,
        state_callback: StateCallback,
        status_callback: StatusCallback,
    ) -> None:
        self._state_callback = state_callback
        self._status_callback = status_callback

    async def async_login(self) -> None:
        async with self._auth_lock:
            await self._async_full_login()

    async def _async_full_login(self) -> None:
        self.full_login_count += 1
        payload = {
            "apiKey": API_KEY,
            "email": self.username,
            "password": self._password,
        }
        try:
            async with self._session.post(
                f"{ZODIAC_API}/users/v1/login",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=20),
            ) as response:
                if response.status in (401, 403):
                    raise TCXAuthError("Invalid iAquaLink username or password")
                if response.status >= 400:
                    raise TCXConnectionError(
                        f"Login failed with HTTP {response.status}"
                    )
                data = await response.json(content_type=None)
        except TCXError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as err:
            raise TCXConnectionError(f"Unable to reach iAquaLink login: {err}") from err

        self._apply_auth(data, keep_refresh=False)
        self.cloud_reachable = True
        self.last_error = None

    def _apply_auth(self, data: dict[str, Any], *, keep_refresh: bool) -> None:
        auth_token = data.get("authentication_token")
        user_id = data.get("id")
        oauth = data.get("userPoolOAuth") or {}
        id_token = oauth.get("IdToken")
        refresh_token = oauth.get("RefreshToken")
        expires_in = oauth.get("ExpiresIn", 3600)

        if not auth_token or user_id is None or not id_token:
            raise TCXAuthError("iAquaLink login response did not contain required tokens")

        self.authentication_token = str(auth_token)
        self.user_id = str(user_id)
        self.id_token = str(id_token)
        if refresh_token:
            self.refresh_token = str(refresh_token)
        elif not keep_refresh:
            self.refresh_token = None
        try:
            expires = max(600, int(expires_in))
        except (TypeError, ValueError):
            expires = 3600
        self._token_expires_at = time.monotonic() + expires

    async def _async_refresh_auth(self) -> None:
        self.auth_refresh_count += 1
        if not self.refresh_token:
            await self._async_full_login()
            return
        payload = {"email": self.username, "refresh_token": self.refresh_token}
        try:
            async with self._session.post(
                f"{ZODIAC_API}/users/v1/refresh",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=20),
            ) as response:
                if response.status >= 400:
                    await self._async_full_login()
                    return
                data = await response.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
            await self._async_full_login()
            return
        self._apply_auth(data, keep_refresh=True)

    async def async_ensure_auth(self) -> None:
        async with self._auth_lock:
            if not self.id_token or not self.authentication_token or not self.user_id:
                await self._async_full_login()
                return
            if time.monotonic() >= self._token_expires_at - TOKEN_REFRESH_MARGIN:
                await self._async_refresh_auth()

    async def async_discover_devices(self) -> list[TCXDevice]:
        await self.async_ensure_auth()
        assert self.authentication_token is not None
        assert self.user_id is not None
        params = {
            "api_key": API_KEY,
            "authentication_token": self.authentication_token,
            "user_id": self.user_id,
        }
        try:
            async with self._session.get(
                f"{IAQUALINK_API}/devices.json",
                params=params,
                timeout=aiohttp.ClientTimeout(total=20),
            ) as response:
                if response.status in (401, 403):
                    self.id_token = None
                    raise TCXAuthError("iAquaLink session expired")
                if response.status >= 400:
                    raise TCXConnectionError(
                        f"Device discovery failed with HTTP {response.status}"
                    )
                data = await response.json(content_type=None)
        except TCXError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as err:
            raise TCXConnectionError(f"Device discovery failed: {err}") from err

        devices: list[TCXDevice] = []
        if isinstance(data, list):
            for item in data:
                if not isinstance(item, dict):
                    continue
                device_type = str(item.get("device_type", ""))
                if device_type.casefold() != "tcx":
                    continue
                serial = item.get("serial_number")
                if not serial:
                    continue
                devices.append(
                    TCXDevice(
                        serial=str(serial),
                        name=str(item.get("name") or "AquaLink TCX"),
                        device_type=device_type,
                    )
                )
        return devices

    async def async_get_shadow(self) -> dict[str, Any]:
        """Read the TCX shadow and account for every failed attempt."""
        self.shadow_request_count += 1
        try:
            return await self._async_get_shadow()
        except Exception:
            self.shadow_failure_count += 1
            raise

    async def _async_get_shadow(self) -> dict[str, Any]:
        """Read the TCX Zodiac shadow when that endpoint is available.

        TCX deployments are inconsistent here. The v1 endpoint is the one
        observed in current iAquaLink clients; v2 is retained only as a
        compatibility fallback. A TCX that rejects both endpoints is still
        usable over the primary WebSocket path.
        """
        if not self.device_id:
            raise TCXDeviceNotFound("TCX device ID is not configured")
        if self.shadow_supported is False:
            raise TCXShadowUnsupported("TCX REST shadow is not supported")

        await self.async_ensure_auth()
        assert self.id_token is not None
        headers = {
            "Authorization": self.id_token,
            "Accept": "application/json",
        }

        unsupported: list[str] = []
        for version in ("v1", "v2"):
            url = f"{ZODIAC_API}/devices/{version}/{self.device_id}/shadow"
            try:
                async with self._session.get(
                    url,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=20),
                ) as response:
                    if response.status in (401, 403):
                        self.id_token = None
                        raise TCXAuthError("iAquaLink authorization expired")
                    if response.status in (400, 404, 405):
                        unsupported.append(f"{version} HTTP {response.status}")
                        continue
                    if response.status == 429:
                        retry_after: float | None = None
                        retry_after_header = response.headers.get("Retry-After")
                        if retry_after_header is not None:
                            try:
                                retry_after = max(0.0, float(retry_after_header))
                            except ValueError:
                                pass
                        raise TCXRateLimited(
                            f"TCX shadow {version} returned HTTP 429",
                            retry_after=retry_after,
                        )
                    if response.status >= 400:
                        raise TCXConnectionError(
                            f"TCX shadow {version} returned HTTP {response.status}"
                        )
                    data = await response.json(content_type=None)
            except TCXError:
                raise
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as err:
                raise TCXConnectionError(f"Unable to read TCX shadow: {err}") from err

            reported = _collect_reported(data)
            if reported is None:
                unsupported.append(f"{version} response had no state.reported")
                continue

            self.shadow_supported = True
            self.cloud_reachable = True
            self.last_error = None
            self.last_shadow_error = None
            self.shadow_poll_interval = SHADOW_INTERVAL
            self.last_shadow_update_at = _utc_now_iso()
            self.shadow_success_count += 1
            _deep_merge(self.reported, reported)
            return data

        self.shadow_supported = False
        detail = "; ".join(unsupported) if unsupported else "unsupported response"
        raise TCXShadowUnsupported(f"TCX REST shadow unavailable: {detail}")

    async def async_start(self) -> None:
        if self._tasks:
            return
        self._stopping = False
        self._tasks = [
            asyncio.create_task(self._socket_supervisor(), name="tcx_direct_socket"),
            asyncio.create_task(self._shadow_loop(), name="tcx_direct_shadow"),
            asyncio.create_task(self._watchdog_loop(), name="tcx_direct_watchdog"),
        ]

    async def async_stop(self) -> None:
        self._stopping = True
        self._reconnect_requested.set()
        if self._ws is not None and not self._ws.closed:
            await self._ws.close()
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        self.websocket_connected = False

    async def async_force_reconnect(self, reason: str = "manual") -> None:
        """Force the current subscription to be rebuilt and record why."""
        self.last_reconnect_reason = reason
        self.reconnect_reason_counts[reason] = (
            self.reconnect_reason_counts.get(reason, 0) + 1
        )
        if reason == "manual":
            self.manual_reconnect_count += 1
        elif reason.startswith("watchdog"):
            self.watchdog_reconnect_count += 1
        self._reconnect_requested.set()
        if self._ws is not None and not self._ws.closed:
            await self._ws.close(code=aiohttp.WSCloseCode.GOING_AWAY)

    def _resolve_pending_control(self) -> None:
        pending = self._pending_control
        if pending is None:
            return
        if pending.predicate(self.reported) and not pending.future.done():
            pending.future.set_result(None)

    def _fail_pending_control(self, message: str) -> None:
        pending = self._pending_control
        if pending is None:
            return
        if not pending.future.done():
            pending.future.set_exception(TCXConnectionError(message))

    async def _async_send_control(
        self,
        desired: dict[str, Any],
        description: str,
        predicate: Callable[[dict[str, Any]], bool],
    ) -> None:
        """Send one serialized TCX command and require reported confirmation."""
        ws = self._ws
        if ws is None or ws.closed or not self.websocket_connected:
            raise TCXConnectionError(
                f"TCX WebSocket is not connected; {description} command was not sent"
            )
        if not self.device_id or self.user_id is None:
            raise TCXConnectionError(
                f"TCX device or user identity is unavailable; {description} command was not sent"
            )

        message = build_set_state_message(
            self.device_id,
            self.user_id,
            CONTROL_NAMESPACE,
            desired,
        )
        future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        pending = _PendingControl(description, predicate, future)
        self._pending_control = pending
        self.control_command_count += 1
        self.control_command_counts[description] = (
            self.control_command_counts.get(description, 0) + 1
        )
        self.last_control_at = _utc_now_iso()
        self.last_control_description = description
        self.last_control_error = None
        self.last_control_frame = sanitize_diagnostics(message)

        try:
            await ws.send_json(message)
            await asyncio.wait_for(future, timeout=CONTROL_CONFIRM_TIMEOUT)
        except asyncio.TimeoutError as err:
            message_text = (
                f"TCX did not confirm {description} within "
                f"{CONTROL_CONFIRM_TIMEOUT} seconds"
            )
            self.control_failure_count += 1
            self.control_failure_counts[description] = (
                self.control_failure_counts.get(description, 0) + 1
            )
            self.last_control_error = message_text
            raise TCXConnectionError(message_text) from err
        except asyncio.CancelledError:
            raise
        except TCXError as err:
            self.control_failure_count += 1
            self.control_failure_counts[description] = (
                self.control_failure_counts.get(description, 0) + 1
            )
            self.last_control_error = str(err)
            raise
        except (aiohttp.ClientError, ConnectionError, RuntimeError) as err:
            message_text = f"Unable to send TCX {description} command: {err}"
            self.control_failure_count += 1
            self.control_failure_counts[description] = (
                self.control_failure_counts.get(description, 0) + 1
            )
            self.last_control_error = message_text
            raise TCXConnectionError(message_text) from err
        else:
            self.control_success_count += 1
            self.control_success_counts[description] = (
                self.control_success_counts.get(description, 0) + 1
            )
            self.last_control_error = None
        finally:
            if self._pending_control is pending:
                self._pending_control = None

    async def async_set_waterfall(self, enabled: bool) -> None:
        """Set the captured TCX waterfall feature and await reported state."""
        async with self._control_lock:
            feature = _find_waterfall_feature(self.reported)
            if feature is None:
                raise TCXControlUnsupported(
                    "This TCX controller has no confirmed FRLY/WF waterfall feature"
                )
            feature_key, feature_state = feature
            current = _coerce_bool(feature_state.get("st"))
            if current is enabled:
                return
            await self._async_send_control(
                {feature_key: {"st": int(enabled)}},
                "waterfall state",
                lambda reported: (
                    (confirmed := _find_waterfall_feature(reported)) is not None
                    and _coerce_bool(confirmed[1].get("st")) is enabled
                ),
            )

    async def async_set_waterfall_with_speed(self, speed: float) -> None:
        """Enable Waterfall, then apply its configured manual pump speed."""
        await self.async_set_waterfall(True)
        try:
            await self.async_set_pump_speed(speed)
        except TCXError:
            try:
                await self.async_set_waterfall(False)
            except TCXError as rollback_error:
                _LOGGER.warning(
                    "Unable to turn Waterfall back off after its RPM command failed: %s",
                    rollback_error,
                )
            raise

    async def async_set_pump_power(self, enabled: bool) -> None:
        """Set the confirmed Pool Filtration mode and await reported state."""
        async with self._control_lock:
            pool_mode = _find_pool_mode(self.reported)
            if pool_mode is None:
                raise TCXControlUnsupported(
                    "This TCX controller has no confirmed V_POS/POOL_M pool mode"
                )
            pool_key, pool_state = pool_mode
            if _coerce_bool(pool_state.get("st")) is enabled:
                return
            await self._async_send_control(
                {pool_key: {"st": int(enabled)}},
                "pump power state",
                lambda reported: (
                    (confirmed := _find_pool_mode(reported)) is not None
                    and _coerce_bool(confirmed[1].get("st")) is enabled
                ),
            )

    async def async_set_pool_light(self, enabled: bool) -> None:
        """Set the captured TCX pool light and await reported state."""
        async with self._control_lock:
            pool_light = _find_pool_light(self.reported)
            if pool_light is None:
                raise TCXControlUnsupported(
                    "This TCX controller has no confirmed JL/POOL_LT pool light"
                )
            light_key, light_state = pool_light
            if _coerce_bool(light_state.get("st")) is enabled:
                return
            await self._async_send_control(
                {light_key: {"st": int(enabled)}},
                "pool light state",
                lambda reported: (
                    (confirmed := _find_pool_light(reported)) is not None
                    and _coerce_bool(confirmed[1].get("st")) is enabled
                ),
            )

    async def async_set_pump_speed(self, speed: float) -> None:
        """Set the filtration controller's manual speed and await confirmation."""
        async with self._control_lock:
            controller = _find_filter_controller(self.reported)
            if controller is None:
                raise TCXControlUnsupported(
                    "This TCX controller has no confirmed F_CTRL/FILT controller"
                )
            controller_key, controller_state = controller
            ecm0 = _mapping(self.reported.get("ecm0"))
            minimum = _coerce_number(ecm0.get("minSpd"))
            if minimum is None:
                minimum = _coerce_number(controller_state.get("minSpd"))
            maximum = _coerce_number(ecm0.get("maxSpd"))
            if maximum is None:
                maximum = _coerce_number(controller_state.get("maxSpd"))
            if minimum is None or maximum is None:
                raise TCXControlUnsupported(
                    "The TCX controller did not report safe pump speed limits"
                )

            requested = int(round(speed))
            if requested < minimum or requested > maximum:
                raise TCXControlUnsupported(
                    f"Pump speed must be between {minimum:.0f} and {maximum:.0f} RPM"
                )
            current = _coerce_number(controller_state.get("manSpd"))
            if current is not None and round(current) == requested:
                return
            await self._async_send_control(
                {controller_key: {"manSpd": requested}},
                "pump speed",
                lambda reported: (
                    (confirmed := _find_filter_controller(reported)) is not None
                    and (actual := _coerce_number(confirmed[1].get("manSpd")))
                    is not None
                    and round(actual) == requested
                ),
            )

    def _record_ws_structure(self, data: dict[str, Any]) -> None:
        structure = _ws_structure(data)
        fingerprint = structure["fingerprint"]
        count = self._ws_structure_counts.get(fingerprint, 0) + 1
        self._ws_structure_counts[fingerprint] = count

        for existing in self._recent_ws_structures:
            if existing.get("fingerprint") == fingerprint:
                existing["count"] = count
                existing["last_seen"] = self.last_ws_message_at
                return

        structure["count"] = count
        structure["first_seen"] = self.last_ws_message_at
        structure["last_seen"] = self.last_ws_message_at
        self._recent_ws_structures.append(structure)

    def _record_desired_payload(
        self,
        data: dict[str, Any],
        payload: dict[str, Any],
        desired: dict[str, Any],
    ) -> None:
        """Retain unique desired-state echoes without crowding out rare events."""
        signature_source = json.dumps(
            {
                "service": data.get("service"),
                "event": data.get("event"),
                "desired": desired,
            },
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8", errors="replace")
        fingerprint = hashlib.sha256(signature_source).hexdigest()[:16]

        for existing in self._recent_desired_payloads:
            if existing.get("fingerprint") == fingerprint:
                existing["count"] = int(existing.get("count", 1)) + 1
                existing["last_seen"] = self.last_ws_message_at
                existing["timestamp"] = payload.get("timestamp")
                return

        self._recent_desired_payloads.append(
            {
                "fingerprint": fingerprint,
                "count": 1,
                "first_seen": self.last_ws_message_at,
                "last_seen": self.last_ws_message_at,
                "service": data.get("service"),
                "event": data.get("event"),
                "timestamp": payload.get("timestamp"),
                "desired": deepcopy(desired),
            }
        )

    @property
    def recent_ws_structures(self) -> list[dict[str, Any]]:
        return [deepcopy(item) for item in self._recent_ws_structures]

    @property
    def recent_desired_payloads(self) -> list[dict[str, Any]]:
        """Return recent desired-state echoes for control-protocol mapping."""
        return [deepcopy(item) for item in self._recent_desired_payloads]

    @property
    def websocket_stream_healthy(self) -> bool:
        """Return whether actual WebSocket reported-state traffic is recent."""
        if not self.websocket_connected or self.last_ws_reported_monotonic is None:
            return False
        return (
            time.monotonic() - self.last_ws_reported_monotonic
            <= WEBSOCKET_STALE_SECONDS
        )

    def _mark_websocket_opened(self) -> None:
        """Initialize freshness state for a newly opened socket generation."""
        self.websocket_connected = True
        self._ws_opened_monotonic = time.monotonic()
        self.last_ws_reported_monotonic = None
        self.last_websocket_opened_at = _utc_now_iso()

    def _record_connection_failure(self, err: Exception | str) -> None:
        """Record a cloud transport failure consistently."""
        self.last_error = str(err)
        self.cloud_reachable = False

    async def _notify_status(self) -> None:
        if self._status_callback is not None:
            await self._status_callback()

    async def _notify_state(self, source: str) -> None:
        if self._state_callback is not None:
            await self._state_callback(deepcopy(self.reported), source)

    async def _open_websocket(self) -> aiohttp.ClientWebSocketResponse:
        if not self.device_id:
            raise TCXDeviceNotFound("TCX device ID is not configured")
        await self.async_ensure_auth()
        assert self.id_token is not None
        assert self.user_id is not None
        headers = {
            "Authorization": self.id_token,
            "Origin": "https://prod-socket.zodiac-io.com",
        }
        try:
            ws = await self._session.ws_connect(
                WEBSOCKET_URL,
                headers=headers,
                heartbeat=30,
                autoping=True,
                timeout=aiohttp.ClientWSTimeout(ws_close=10),
            )
        except aiohttp.WSServerHandshakeError as err:
            if err.status in (401, 403):
                self.id_token = None
                raise TCXAuthError("TCX WebSocket authorization failed") from err
            raise TCXConnectionError(
                f"TCX WebSocket handshake failed with HTTP {err.status}"
            ) from err
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise TCXConnectionError(f"Unable to open TCX WebSocket: {err}") from err

        self._authorization_snapshot_event.clear()
        await self._send_authorization_subscribe(ws)
        return ws

    async def _send_authorization_subscribe(
        self, ws: aiohttp.ClientWebSocketResponse
    ) -> None:
        """Subscribe to the TCX device and request its current namespace snapshot.

        Zodiac normally answers this subscription with an Authorization payload
        containing the current main/ecm/filt/fea/etc. namespace shadows. TCX
        occasionally accepts the socket but omits that snapshot, so startup
        retries the same read-only subscription a few times before falling back
        to cached state plus live deltas.
        """
        if not self.device_id or self.user_id is None:
            return
        try:
            numeric_user_id: int | str = int(self.user_id)
        except ValueError:
            numeric_user_id = self.user_id
        subscribe = {
            "action": "subscribe",
            "version": 1,
            "namespace": "authorization",
            "payload": {"userId": numeric_user_id},
            "service": "Authorization",
            "target": self.device_id,
        }
        await ws.send_json(subscribe)
        self.authorization_subscribe_count += 1

    async def _bootstrap_resubscribe(
        self, ws: aiohttp.ClientWebSocketResponse
    ) -> None:
        """Retry the read-only Authorization subscription until a snapshot arrives."""
        # The first subscription was sent by _open_websocket. These are only
        # retries, and do not change equipment state.
        for _ in range(max(0, BOOTSTRAP_SUBSCRIBE_ATTEMPTS - 1)):
            try:
                await asyncio.wait_for(
                    self._authorization_snapshot_event.wait(),
                    timeout=BOOTSTRAP_SUBSCRIBE_INTERVAL,
                )
                return
            except asyncio.TimeoutError:
                pass
            if self._stopping or ws.closed or ws is not self._ws:
                return
            try:
                await self._send_authorization_subscribe(ws)
                self.bootstrap_resubscribe_count += 1
                _LOGGER.debug(
                    "TCX startup snapshot not received yet; re-sent Authorization subscription"
                )
            except (aiohttp.ClientError, ConnectionError, RuntimeError):
                return

    async def _socket_supervisor(self) -> None:
        failures = 0
        while not self._stopping:
            self._reconnect_requested.clear()
            bootstrap_task: asyncio.Task[Any] | None = None
            try:
                reconnecting = self.websocket_connect_count > 0
                self._ws = await self._open_websocket()
                self.websocket_connect_count += 1
                if reconnecting:
                    self.websocket_reconnect_count += 1
                self._mark_websocket_opened()
                self.cloud_reachable = True
                self.last_error = None
                await self._notify_status()
                bootstrap_task = asyncio.create_task(
                    self._bootstrap_resubscribe(self._ws),
                    name="tcx_direct_bootstrap",
                )

                async for msg in self._ws:
                    if self._stopping or self._reconnect_requested.is_set():
                        break

                    self.ws_messages_received += 1
                    self.last_ws_message_type = getattr(msg.type, "name", str(msg.type))

                    if msg.type == aiohttp.WSMsgType.TEXT:
                        now = time.monotonic()
                        self.ws_text_messages_received += 1
                        self.last_ws_message_at = _utc_now_iso()
                        try:
                            data = json.loads(msg.data)
                        except json.JSONDecodeError:
                            _LOGGER.debug("Ignoring non-JSON TCX WebSocket frame")
                            continue

                        self.ws_json_messages_received += 1
                        if isinstance(data, dict):
                            self.last_ws_payload = deepcopy(data)
                            self.last_ws_service = (
                                None if data.get("service") is None else str(data.get("service"))
                            )
                            self.last_ws_event = (
                                None if data.get("event") is None else str(data.get("event"))
                            )
                            self.last_ws_namespace = (
                                None
                                if data.get("namespace") is None
                                else str(data.get("namespace"))
                            )
                            self.last_ws_target = (
                                None if data.get("target") is None else str(data.get("target"))
                            )
                            self._record_ws_structure(data)

                        reported = _collect_reported(data)
                        payload = data.get("payload") if isinstance(data, dict) else None
                        payload_state = (
                            payload.get("state")
                            if isinstance(payload, dict) and isinstance(payload.get("state"), dict)
                            else None
                        )
                        if isinstance(payload_state, dict):
                            desired = payload_state.get("desired")
                            if isinstance(desired, dict) and any(
                                value is not None for value in desired.values()
                            ):
                                self.ws_desired_messages_received += 1
                                self._record_desired_payload(data, payload, desired)

                        if (
                            isinstance(data, dict)
                            and data.get("service") == "Authorization"
                            and reported is not None
                        ):
                            self.authorization_snapshot_count += 1
                            self.last_authorization_snapshot_at = self.last_ws_message_at
                            self._authorization_snapshot_event.set()

                        is_state_message = reported is not None or payload_state is not None
                        if is_state_message:
                            self.ws_state_messages_received += 1
                        else:
                            self.ws_non_state_messages_received += 1

                        if reported is not None:
                            self.ws_reported_messages_received += 1
                            _deep_merge(self.reported, reported)
                            self._resolve_pending_control()
                            self.last_ws_reported_monotonic = now
                            self.last_ws_state_at = self.last_ws_message_at
                            failures = 0
                            stamp = _extract_device_timestamp(data)
                            if stamp is not None:
                                self.last_ws_device_timestamp = stamp
                            await self._notify_state("websocket")
                    elif msg.type in (
                        aiohttp.WSMsgType.CLOSED,
                        aiohttp.WSMsgType.CLOSE,
                        aiohttp.WSMsgType.ERROR,
                    ):
                        break
                    else:
                        continue

                if not self._stopping and not self._reconnect_requested.is_set():
                    failures += 1
                    self._record_connection_failure("TCX WebSocket closed unexpectedly")
            except TCXAuthError as err:
                self._record_connection_failure(err)
                self.id_token = None
                failures += 1
            except (TCXConnectionError, aiohttp.ClientError, asyncio.TimeoutError) as err:
                self._record_connection_failure(err)
                failures += 1
            except asyncio.CancelledError:
                raise
            except Exception as err:  # defensive supervisor: never die silently
                self._record_connection_failure(f"Unexpected WebSocket error: {err}")
                _LOGGER.exception("Unexpected TCX WebSocket failure")
                failures += 1
            finally:
                if bootstrap_task is not None:
                    bootstrap_task.cancel()
                    await asyncio.gather(bootstrap_task, return_exceptions=True)
                self.websocket_connected = False
                self._fail_pending_control(
                    "TCX WebSocket closed before the equipment confirmed the command"
                )
                if self._ws is not None and not self._ws.closed:
                    await self._ws.close()
                self._ws = None
                self._ws_opened_monotonic = None
                await self._notify_status()

            if self._stopping:
                break
            if failures >= 3:
                self.id_token = None
                self.authentication_token = None
                self._token_expires_at = 0
            delay = min(RECONNECT_MAX, max(2, 2 ** min(failures, 5)))
            delay += random.uniform(0, min(3, delay / 4))
            try:
                await asyncio.wait_for(self._reconnect_requested.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass

    async def _shadow_loop(self) -> None:
        await asyncio.sleep(2)
        while not self._stopping:
            if self.shadow_supported is False:
                try:
                    await asyncio.sleep(MAX_WEBSOCKET_SESSION)
                except asyncio.CancelledError:
                    raise
                continue
            try:
                await self.async_get_shadow()
                await self._notify_state("shadow")
            except TCXShadowUnsupported as err:
                # Some TCX controllers are WebSocket-only. Do not mark the
                # integration failed and do not keep hammering an unsupported
                # REST endpoint. The watchdog will rotate the WebSocket
                # periodically to guarantee a fresh subscription.
                self.last_shadow_error = str(err)
                await self._notify_status()
            except TCXRateLimited as err:
                self.shadow_rate_limit_count += 1
                self.last_shadow_rate_limited_at = _utc_now_iso()
                self.last_shadow_error = str(err)
                next_interval = max(
                    SHADOW_INTERVAL * 2,
                    self.shadow_poll_interval * 2,
                    err.retry_after or 0,
                )
                self.shadow_poll_interval = min(
                    SHADOW_RATE_LIMIT_MAX_INTERVAL, next_interval
                )
                if not self.websocket_connected:
                    self.cloud_reachable = False
                await self._notify_status()
            except TCXAuthError as err:
                self.last_error = str(err)
                self.last_shadow_error = str(err)
                self.cloud_reachable = False
                self.id_token = None
                await self._notify_status()
            except (TCXConnectionError, aiohttp.ClientError, asyncio.TimeoutError) as err:
                self.last_shadow_error = str(err)
                if not self.websocket_connected:
                    self.cloud_reachable = False
                await self._notify_status()
            except asyncio.CancelledError:
                raise
            except Exception as err:
                self.last_shadow_error = f"Unexpected shadow error: {err}"
                if not self.websocket_connected:
                    self.cloud_reachable = False
                _LOGGER.exception("Unexpected TCX shadow polling failure")
                await self._notify_status()

            try:
                await asyncio.sleep(self.shadow_poll_interval)
            except asyncio.CancelledError:
                raise

    async def _async_refresh_stale_subscription(self) -> bool:
        """Refresh a quiet subscription in place before replacing its socket."""
        ws = self._ws
        if ws is None or ws.closed or not self.websocket_connected:
            return False
        self._authorization_snapshot_event.clear()
        self.watchdog_resubscribe_count += 1
        try:
            await self._send_authorization_subscribe(ws)
            await asyncio.wait_for(
                self._authorization_snapshot_event.wait(),
                timeout=WATCHDOG_RESUBSCRIBE_TIMEOUT,
            )
        except asyncio.CancelledError:
            raise
        except (asyncio.TimeoutError, aiohttp.ClientError, ConnectionError, RuntimeError):
            self.watchdog_resubscribe_failure_count += 1
            return False
        self.watchdog_resubscribe_success_count += 1
        return True

    async def _watchdog_loop(self) -> None:
        """Guarantee that a logically stale TCX subscription gets replaced.

        Transport ping/pong alone cannot prove that Zodiac is still streaming
        the device subscription. Even a socket that remains TCP-connected is
        therefore rotated periodically. This is intentionally conservative
        and directly addresses the common TCX failure mode where values freeze
        until the client is restarted.
        """
        while not self._stopping:
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                raise

            if self._stopping or not self.websocket_connected:
                continue
            if self._ws_opened_monotonic is None:
                continue

            now = time.monotonic()
            age = now - self._ws_opened_monotonic
            reported_age = (
                None
                if self.last_ws_reported_monotonic is None
                else now - self.last_ws_reported_monotonic
            )

            # A TCP/WebSocket connection is not enough. If no reported-state
            # message has arrived for the stale window, first refresh the
            # Authorization subscription on the existing socket. Reconnect
            # only if Zodiac does not answer that read-only refresh.
            if (
                (reported_age is None and age >= WEBSOCKET_STALE_SECONDS)
                or (
                    reported_age is not None
                    and reported_age >= WEBSOCKET_STALE_SECONDS
                )
            ):
                _LOGGER.info(
                    "TCX WebSocket has produced no reported state for %s seconds; refreshing subscription",
                    f"{age:.0f}" if reported_age is None else f"{reported_age:.0f}",
                )
                if not await self._async_refresh_stale_subscription():
                    _LOGGER.warning(
                        "TCX subscription refresh was not confirmed; reconnecting"
                    )
                    await self.async_force_reconnect("watchdog_stale_stream")
                continue

            if age >= MAX_WEBSOCKET_SESSION:
                _LOGGER.info(
                    "Rotating TCX WebSocket after %.0f seconds to refresh subscription",
                    age,
                )
                await self.async_force_reconnect("watchdog_session_rotation")

    @property
    def healthy(self) -> bool:
        # Deliberately do not call a transport-only open socket healthy.
        # Shadow polling may still keep values current, but the connection
        # diagnostic specifically represents the live WebSocket stream.
        return self.websocket_stream_healthy
