from __future__ import annotations

DOMAIN = "tcx_direct"
NAME = "Jandy TCX Direct"


def encode_version_code(version: str) -> int:
    """Encode a three-part release version as a sortable integer."""
    parts = version.split(".")
    if len(parts) != 3:
        raise ValueError("Version must contain exactly three numeric components")
    try:
        major, minor, patch = (int(part) for part in parts)
    except ValueError as err:
        raise ValueError("Version components must be integers") from err
    if major < 0 or not 0 <= minor < 1000 or not 0 <= patch < 1000:
        raise ValueError("Version components must be non-negative and fit the encoding")
    return major * 1_000_000 + minor * 1_000 + patch


VERSION = "0.3.2"
VERSION_CODE = encode_version_code(VERSION)

ATTR_RPM = "rpm"
SERVICE_START_PUMP_AT_SPEED = "start_pump_at_speed"

API_KEY = "EOOEMOW4YR6QNB07"
ZODIAC_API = "https://prod.zodiac-io.com"
IAQUALINK_API = "https://r-api.iaqualink.net"
WEBSOCKET_URL = "wss://prod-socket.zodiac-io.com/devices"
CONTROL_NAMESPACE = "tcx"

CONF_DEVICE_ID = "device_id"
CONF_DEVICE_NAME = "device_name"
CONF_DEVICE_TYPE = "device_type"
CONF_WATERFALL_RPM = "waterfall_rpm"
CONF_EXPERIMENTAL_SCHEDULE_WRITES = "experimental_schedule_writes"

DEFAULT_WATERFALL_RPM = 2850

PLATFORMS = ["sensor", "binary_sensor", "button", "switch", "number", "select"]

CONTROLLER_MODE_AUTO = 1
CONTROLLER_MODES = {
    1: "Auto",
    2: "Quick Clean",
    3: "Service",
    4: "Time Out",
    5: "Transitioning",
}

LIGHT_COLOR_BY_CODE = {
    1: "Alpine White",
    2: "Sky Blue",
    3: "Cobalt Blue",
    4: "Caribbean Blue",
    5: "Spring Green",
    6: "Emerald Green",
    7: "Emerald Rose",
    8: "Magenta",
    9: "Violet",
    10: "Slow Color Splash",
    11: "Fast Color Splash",
    12: "America The Beautiful",
    13: "Fat Tuesday",
    14: "Disco Tech",
}
LIGHT_COLOR_BY_NAME = {name: code for code, name in LIGHT_COLOR_BY_CODE.items()}

SHADOW_INTERVAL = 120
SHADOW_RATE_LIMIT_MAX_INTERVAL = 1800
TOKEN_REFRESH_MARGIN = 300
RECONNECT_MAX = 60
MAX_WEBSOCKET_SESSION = 21600
WEBSOCKET_STALE_SECONDS = 1800
WATCHDOG_RESUBSCRIBE_TIMEOUT = 15
RECENT_WS_STRUCTURES = 20
RECENT_CONTROLLER_MODE_TRANSITIONS = 20
RECENT_POST_PRIME_TRANSITIONS = 20
BOOTSTRAP_SUBSCRIBE_INTERVAL = 3
BOOTSTRAP_SUBSCRIBE_ATTEMPTS = 4
CONTROL_CONFIRM_TIMEOUT = 15
POOL_FILTRATION_CONFIRM_TIMEOUT = 45
PUMP_POWER_CONFIRM_TIMEOUT = 45
POST_PRIME_SYNC_INTERVAL = 5
POST_PRIME_SYNC_TIMEOUT = 300
PUMP_ZERO_GRACE_SECONDS = 90
CACHE_VERSION = 1
