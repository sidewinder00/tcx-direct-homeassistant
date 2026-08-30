from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import TCXConfigEntry
from .api import TCXError
from .const import LIGHT_COLOR_BY_CODE, LIGHT_COLOR_BY_NAME
from .entity import TCXEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TCXConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([TCXPoolLightColorSelect(entry)])


class TCXPoolLightColorSelect(TCXEntity, SelectEntity):
    """Select a confirmed TCX pool-light color or program."""

    _attr_name = "Pool Light Color"
    _attr_icon = "mdi:palette"
    _attr_options = list(LIGHT_COLOR_BY_CODE.values())

    def __init__(self, entry: TCXConfigEntry) -> None:
        super().__init__(entry)
        self._attr_unique_id = f"{entry.data['device_id']}_pool_light_color_control"

    @property
    def available(self) -> bool:
        data = self.coordinator.data or {}
        return (
            super().available
            and data.get("remote_control_available") is not False
            and data.get("light_color_control_supported") is True
            and data.get("light_power_setpoint") is True
        )

    @property
    def current_option(self) -> str | None:
        value = (self.coordinator.data or {}).get("light_color_name")
        return value if isinstance(value, str) and value in self.options else None

    async def async_select_option(self, option: str) -> None:
        color_code = LIGHT_COLOR_BY_NAME.get(option)
        if color_code is None:
            raise HomeAssistantError(f"Unsupported pool light color: {option}")
        try:
            await self.entry.runtime_data.client.async_set_pool_light_color(color_code)
        except TCXError as err:
            raise HomeAssistantError(str(err)) from err
