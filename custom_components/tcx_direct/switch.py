from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import TCXConfigEntry
from .api import TCXError
from .entity import TCXEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TCXConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([TCXWaterfallSwitch(entry)])


class TCXWaterfallSwitch(TCXEntity, SwitchEntity):
    """Control the confirmed TCX waterfall feature relay."""

    _attr_name = "Waterfall"
    _attr_icon = "mdi:fountain"

    def __init__(self, entry: TCXConfigEntry) -> None:
        super().__init__(entry)
        self._attr_unique_id = f"{entry.data['device_id']}_waterfall"

    @property
    def available(self) -> bool:
        return super().available and isinstance(
            (self.coordinator.data or {}).get("waterfall"), bool
        )

    @property
    def is_on(self) -> bool | None:
        value = (self.coordinator.data or {}).get("waterfall")
        return value if isinstance(value, bool) else None

    async def async_turn_on(self, **kwargs: object) -> None:
        await self._async_set_state(True)

    async def async_turn_off(self, **kwargs: object) -> None:
        await self._async_set_state(False)

    async def _async_set_state(self, enabled: bool) -> None:
        try:
            await self.entry.runtime_data.client.async_set_waterfall(enabled)
        except TCXError as err:
            raise HomeAssistantError(str(err)) from err
