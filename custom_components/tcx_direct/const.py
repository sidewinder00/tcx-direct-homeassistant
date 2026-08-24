from __future__ import annotations

DOMAIN = "tcx_direct"
NAME = "Jandy TCX Direct"
VERSION = "0.1.6"

API_KEY = "EOOEMOW4YR6QNB07"
ZODIAC_API = "https://prod.zodiac-io.com"
IAQUALINK_API = "https://r-api.iaqualink.net"
WEBSOCKET_URL = "wss://prod-socket.zodiac-io.com/devices"

CONF_DEVICE_ID = "device_id"
CONF_DEVICE_NAME = "device_name"
CONF_DEVICE_TYPE = "device_type"

PLATFORMS = ["sensor", "binary_sensor", "button"]

SHADOW_INTERVAL = 60
TOKEN_REFRESH_MARGIN = 300
RECONNECT_MAX = 60
MAX_WEBSOCKET_SESSION = 900
WEBSOCKET_STALE_SECONDS = 600
RECENT_WS_STRUCTURES = 20
BOOTSTRAP_SUBSCRIBE_INTERVAL = 3
BOOTSTRAP_SUBSCRIBE_ATTEMPTS = 4
CACHE_VERSION = 1
