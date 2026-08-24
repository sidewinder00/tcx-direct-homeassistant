from __future__ import annotations

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import TCXConfigEntry
from .entity import TCXEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TCXConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([TCXReconnectButton(entry)])


class TCXReconnectButton(TCXEntity, ButtonEntity):
    entity_description = ButtonEntityDescription(
        key="reconnect",
        name="Reconnect",
        icon="mdi:connection",
        entity_category=EntityCategory.DIAGNOSTIC,
    )

    def __init__(self, entry: TCXConfigEntry) -> None:
        super().__init__(entry)
        self._attr_unique_id = f"{entry.data['device_id']}_reconnect"

    async def async_press(self) -> None:
        await self.entry.runtime_data.client.async_force_reconnect()
