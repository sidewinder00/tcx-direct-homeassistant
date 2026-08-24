from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import TCXConfigEntry
from .const import CONF_DEVICE_ID, CONF_DEVICE_NAME, DOMAIN
from .coordinator import TCXCoordinator


class TCXEntity(CoordinatorEntity[TCXCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, entry: TCXConfigEntry) -> None:
        super().__init__(entry.runtime_data.coordinator)
        self.entry = entry
        serial = entry.data[CONF_DEVICE_ID]
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, serial)},
            name=entry.data.get(CONF_DEVICE_NAME, "AquaLink TCX"),
            manufacturer="Jandy / Fluidra",
            model="AquaLink TCX",
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data or {}
        attrs: dict[str, Any] = {
            "websocket_connected": data.get("websocket_connected", False),
            "websocket_stream_healthy": data.get("websocket_stream_healthy", False),
            "cloud_reachable": data.get("cloud_reachable", False),
            "using_cached_data": data.get("using_cached_data", False),
            "data_source": data.get("source", "none"),
            "last_successful_update": data.get("last_successful_update"),
            "last_websocket_message": data.get("last_websocket_message"),
            "last_shadow_update": data.get("last_shadow_update"),
        }
        if data.get("last_error"):
            attrs["last_error"] = data["last_error"]
        return attrs
