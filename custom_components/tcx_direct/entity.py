from __future__ import annotations

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

