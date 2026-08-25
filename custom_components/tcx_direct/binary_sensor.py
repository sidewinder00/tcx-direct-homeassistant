from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import TCXConfigEntry
from .entity import TCXEntity


@dataclass(frozen=True, kw_only=True)
class TCXBinaryDescription(BinarySensorEntityDescription):
    data_key: str


BINARY_SENSORS = (
    TCXBinaryDescription(
        key="pump",
        data_key="pump",
        name="Pump",
        icon="mdi:pump",
    ),
    TCXBinaryDescription(
        key="light",
        data_key="light",
        name="Pool Light",
        icon="mdi:lightbulb",
    ),
    TCXBinaryDescription(
        key="waterfall_status",
        data_key="waterfall",
        name="Waterfall Status",
        icon="mdi:fountain",
    ),
    TCXBinaryDescription(
        key="connection",
        data_key="connected",
        name="Connection",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TCXBinaryDescription(
        key="websocket_connection",
        data_key="websocket_connected",
        name="WebSocket Connection",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TCXBinaryDescription(
        key="websocket_stream",
        data_key="websocket_stream_healthy",
        name="WebSocket Stream",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TCXBinaryDescription(
        key="cloud_connection",
        data_key="cloud_reachable",
        name="Cloud Connection",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TCXConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities(TCXBinarySensor(entry, description) for description in BINARY_SENSORS)


class TCXBinarySensor(TCXEntity, BinarySensorEntity):
    entity_description: TCXBinaryDescription

    def __init__(self, entry: TCXConfigEntry, description: TCXBinaryDescription) -> None:
        super().__init__(entry)
        self.entity_description = description
        self._attr_unique_id = f"{entry.data['device_id']}_{description.key}"

    @property
    def is_on(self) -> bool | None:
        value = (self.coordinator.data or {}).get(self.entity_description.data_key)
        return value if isinstance(value, bool) else None
