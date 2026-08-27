from __future__ import annotations

from typing import Any

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import TCXConfigEntry
from .api import TCXError
from .const import CONF_WATERFALL_RPM, DEFAULT_WATERFALL_RPM
from .entity import TCXEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TCXConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities(
        [
            TCXPumpSpeedNumber(entry),
            TCXPoolFiltrationPresetNumber(entry),
            TCXWaterfallRPMNumber(entry),
        ]
    )


def _number(data: dict[str, Any], key: str) -> float | None:
    value = data.get(key)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def configured_waterfall_rpm(entry: TCXConfigEntry) -> int:
    """Return the persisted Waterfall RPM constrained to reported limits."""
    data = entry.runtime_data.coordinator.data or {}
    minimum = _number(data, "pump_min_rpm")
    maximum = _number(data, "pump_max_rpm")
    configured = entry.options.get(CONF_WATERFALL_RPM, DEFAULT_WATERFALL_RPM)
    if not isinstance(configured, (int, float)) or isinstance(configured, bool):
        configured = DEFAULT_WATERFALL_RPM
    value = int(round(configured))
    if minimum is not None:
        value = max(value, int(round(minimum)))
    if maximum is not None:
        value = min(value, int(round(maximum)))
    return value


class TCXPumpSpeedNumber(TCXEntity, NumberEntity):
    """Control the TCX manual filtration speed setpoint."""

    _attr_name = "Pump Manual Speed"
    _attr_icon = "mdi:speedometer"
    _attr_native_step = 25.0
    _attr_native_unit_of_measurement = "rpm"
    _attr_mode = NumberMode.BOX

    def __init__(self, entry: TCXConfigEntry) -> None:
        super().__init__(entry)
        self._attr_unique_id = f"{entry.data['device_id']}_pump_speed_control"

    def _number(self, key: str) -> float | None:
        return _number(self.coordinator.data or {}, key)

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


class TCXPoolFiltrationPresetNumber(TCXEntity, NumberEntity):
    """Control the persistent TCX Pool Filtration speed preset."""

    _attr_name = "Pool Filtration Preset"
    _attr_icon = "mdi:pump"
    _attr_native_step = 25.0
    _attr_native_unit_of_measurement = "rpm"
    _attr_mode = NumberMode.BOX

    def __init__(self, entry: TCXConfigEntry) -> None:
        super().__init__(entry)
        self._attr_unique_id = f"{entry.data['device_id']}_pool_filtration_preset"

    def _number(self, key: str) -> float | None:
        return _number(self.coordinator.data or {}, key)

    @property
    def available(self) -> bool:
        return (
            super().available
            and (self.coordinator.data or {}).get("pool_filtration_preset_control_supported")
            is True
            and self._number("pool_filtration_preset") is not None
            and self._number("pump_min_rpm") is not None
            and self._number("pump_max_rpm") is not None
        )

    @property
    def native_value(self) -> float | None:
        return self._number("pool_filtration_preset")

    @property
    def native_min_value(self) -> float:
        return self._number("pump_min_rpm") or 0.0

    @property
    def native_max_value(self) -> float:
        return self._number("pump_max_rpm") or 0.0

    async def async_set_native_value(self, value: float) -> None:
        try:
            await self.entry.runtime_data.client.async_set_pool_filtration_preset(value)
        except TCXError as err:
            raise HomeAssistantError(str(err)) from err


class TCXWaterfallRPMNumber(TCXEntity, NumberEntity):
    """Configure the manual pump speed applied while Waterfall is active."""

    _attr_name = "Waterfall RPM"
    _attr_icon = "mdi:fountain"
    _attr_native_step = 25.0
    _attr_native_unit_of_measurement = "rpm"
    _attr_mode = NumberMode.BOX

    def __init__(self, entry: TCXConfigEntry) -> None:
        super().__init__(entry)
        self._attr_unique_id = f"{entry.data['device_id']}_waterfall_rpm"

    def _number(self, key: str) -> float | None:
        return _number(self.coordinator.data or {}, key)

    @property
    def available(self) -> bool:
        data = self.coordinator.data or {}
        return (
            super().available
            and isinstance(data.get("waterfall"), bool)
            and data.get("pump_speed_control_supported") is True
            and self._number("pump_min_rpm") is not None
            and self._number("pump_max_rpm") is not None
        )

    @property
    def native_value(self) -> float:
        return float(configured_waterfall_rpm(self.entry))

    @property
    def native_min_value(self) -> float:
        return self._number("pump_min_rpm") or 0.0

    @property
    def native_max_value(self) -> float:
        return self._number("pump_max_rpm") or 0.0

    async def async_set_native_value(self, value: float) -> None:
        requested = int(round(value))
        try:
            if (self.coordinator.data or {}).get("waterfall") is True:
                await self.entry.runtime_data.client.async_set_pump_speed(requested)
        except TCXError as err:
            raise HomeAssistantError(str(err)) from err

        options = dict(self.entry.options)
        options[CONF_WATERFALL_RPM] = requested
        self.hass.config_entries.async_update_entry(self.entry, options=options)
        self.async_write_ha_state()
