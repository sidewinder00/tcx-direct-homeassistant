from __future__ import annotations

import logging
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .api import TCXClient, merge_normalized_state, normalize_tcx_state
from .const import CACHE_VERSION, DOMAIN

_LOGGER = logging.getLogger(__name__)


class TCXCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Push coordinator with persistent last-known-good state."""

    def __init__(self, hass: HomeAssistant, client: TCXClient, entry_id: str) -> None:
        super().__init__(hass, _LOGGER, name=DOMAIN)
        self.client = client
        self.store: Store[dict[str, Any]] = Store(
            hass, CACHE_VERSION, f"{DOMAIN}.{entry_id}"
        )
        self.raw_reported: dict[str, Any] = {}
        self.normalized: dict[str, Any] = {}
        self.last_successful_update: str | None = None
        self.source = "none"
        self.using_cached_data = False
        self.client.set_callbacks(self.async_handle_state, self.async_handle_status)

    async def async_load_cache(self) -> bool:
        saved = await self.store.async_load()
        if not isinstance(saved, dict):
            return False
        normalized = saved.get("normalized")
        if not isinstance(normalized, dict):
            return False
        self.normalized = deepcopy(normalized)
        raw_reported = saved.get("raw_reported")
        if isinstance(raw_reported, dict):
            self.raw_reported = deepcopy(raw_reported)
            # Seed the client before login/socket startup so the first sparse
            # REST shadow or WebSocket delta augments known state rather than
            # forcing entities back to Unknown.
            self.client.reported = deepcopy(raw_reported)
            # Re-apply current normalization rules to stored state so an
            # upgrade immediately clears derived values that older versions
            # could retain after equipment turned off.
            self.normalized = merge_normalized_state(
                self.normalized, normalize_tcx_state(self.raw_reported)
            )
        self.last_successful_update = saved.get("last_successful_update")
        self.source = "cache"
        self.using_cached_data = True
        self.async_set_updated_data(self._build_data())
        return True

    async def async_handle_state(self, reported: dict[str, Any], source: str) -> None:
        self.raw_reported = deepcopy(reported)
        parsed = normalize_tcx_state(reported)
        self.normalized = merge_normalized_state(self.normalized, parsed)
        self.last_successful_update = datetime.now(timezone.utc).isoformat()
        self.source = source
        self.using_cached_data = False
        self.async_set_updated_data(self._build_data())
        self.store.async_delay_save(self._cache_data, 5)

    async def async_handle_status(self) -> None:
        if not self.client.healthy and self.normalized:
            self.using_cached_data = True
        self.async_set_updated_data(self._build_data())

    def _cache_data(self) -> dict[str, Any]:
        return {
            "normalized": deepcopy(self.normalized),
            "raw_reported": deepcopy(self.raw_reported),
            "last_successful_update": self.last_successful_update,
        }

    def _build_data(self) -> dict[str, Any]:
        return {
            **self.normalized,
            "connected": self.client.healthy,
            "websocket_connected": self.client.websocket_connected,
            "websocket_stream_healthy": self.client.websocket_stream_healthy,
            "cloud_reachable": self.client.cloud_reachable,
            "using_cached_data": self.using_cached_data,
            "last_successful_update": self.last_successful_update,
            "last_websocket_message": self.client.last_ws_message_at,
            "last_websocket_state": self.client.last_ws_state_at,
            "last_shadow_update": self.client.last_shadow_update_at,
            "websocket_messages_received": self.client.ws_messages_received,
            "websocket_state_messages_received": self.client.ws_state_messages_received,
            "websocket_reconnect_count": self.client.websocket_reconnect_count,
            "watchdog_reconnect_count": self.client.watchdog_reconnect_count,
            "source": self.source,
            "last_error": self.client.last_error,
        }
