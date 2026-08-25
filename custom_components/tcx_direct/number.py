from __future__ import annotations

from typing import Any

from homeassistant.components.number import NumberEntity, NumberMode
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
    async_add_entities([TCXPumpSpeedNumber(entry)])


class TCXPumpSpeedNumber(TCXEntity, NumberEntity):
    """Control the TCX filtration speed setpoint."""

    _attr_name = "Pump Speed"
    _attr_icon = "mdi:speedometer"
    _attr_native_step = 25.0
    _attr_native_unit_of_measurement = "rpm"
    _attr_mode = NumberMode.BOX

    def __init__(self, entry: TCXConfigEntry) -> None:
        super().__init__(entry)
        self._attr_unique_id = f"{entry.data['device_id']}_pump_speed_control"

    def _number(self, key: str) -> float | None:
        value: Any = (self.coordinator.data or {}).get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
        return None

    @property
    def available(self) -> bool:
        return (
            super().available
            and (self.coordinator.data or {}).get("pump_speed_control_supported") is True
            and self._number("pump_speed_setpoint") is not None
            and self._number("pump_min_rpm") is not None
            and self._number("pump_max_rpm") is not None
        )

    @property
    def native_value(self) -> float | None:
        return self._number("pump_speed_setpoint")

    @property
    def native_min_value(self) -> float:
        return self._number("pump_min_rpm") or 0.0

    @property
    def native_max_value(self) -> float:
        return self._number("pump_max_rpm") or 0.0

    async def async_set_native_value(self, value: float) -> None:
        try:
            await self.entry.runtime_data.client.async_set_pump_speed(value)
        except TCXError as err:
            raise HomeAssistantError(str(err)) from err
