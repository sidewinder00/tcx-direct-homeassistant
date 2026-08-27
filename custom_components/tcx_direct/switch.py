from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import TCXConfigEntry
from .api import TCXError
from .entity import TCXEntity
from .number import configured_waterfall_rpm


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TCXConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities(
        [
            TCXPumpPowerSwitch(entry),
            TCXPoolLightSwitch(entry),
            TCXWaterfallSwitch(entry),
        ]
    )


class TCXPumpPowerSwitch(TCXEntity, SwitchEntity):
    """Control the confirmed TCX Pool Filtration mode."""

    _attr_name = "Pump Power"
    _attr_icon = "mdi:pump"

    def __init__(self, entry: TCXConfigEntry) -> None:
        super().__init__(entry)
        self._attr_unique_id = f"{entry.data['device_id']}_pump_power"

    @property
    def available(self) -> bool:
        data = self.coordinator.data or {}
        return (
            super().available
            and data.get("pump_power_control_supported") is True
            and isinstance(data.get("pump_power_setpoint"), bool)
        )

    @property
    def is_on(self) -> bool | None:
        value = (self.coordinator.data or {}).get("pump_power_setpoint")
        return value if isinstance(value, bool) else None

    async def async_turn_on(self, **kwargs: object) -> None:
        await self._async_set_state(True)

    async def async_turn_off(self, **kwargs: object) -> None:
        await self._async_set_state(False)

    async def async_start_pump_at_speed(self, rpm: float) -> None:
        """Set the Pool Filtration preset, then start the pump normally."""
        try:
            await self.entry.runtime_data.client.async_start_pump_at_speed(rpm)
        except TCXError as err:
            raise HomeAssistantError(str(err)) from err

    async def _async_set_state(self, enabled: bool) -> None:
        try:
            await self.entry.runtime_data.client.async_set_pump_power(enabled)
        except TCXError as err:
            raise HomeAssistantError(str(err)) from err


class TCXPoolLightSwitch(TCXEntity, SwitchEntity):
    """Control the confirmed TCX pool light."""

    _attr_name = "Pool Light Power"
    _attr_icon = "mdi:lightbulb"

    def __init__(self, entry: TCXConfigEntry) -> None:
        super().__init__(entry)
        self._attr_unique_id = f"{entry.data['device_id']}_pool_light_power"

    @property
    def available(self) -> bool:
        data = self.coordinator.data or {}
        return (
            super().available
            and data.get("light_control_supported") is True
            and isinstance(data.get("light_power_setpoint"), bool)
        )

    @property
    def is_on(self) -> bool | None:
        value = (self.coordinator.data or {}).get("light_power_setpoint")
        return value if isinstance(value, bool) else None

    async def async_turn_on(self, **kwargs: object) -> None:
        await self._async_set_state(True)

    async def async_turn_off(self, **kwargs: object) -> None:
        await self._async_set_state(False)

    async def _async_set_state(self, enabled: bool) -> None:
        try:
            await self.entry.runtime_data.client.async_set_pool_light(enabled)
        except TCXError as err:
            raise HomeAssistantError(str(err)) from err


class TCXWaterfallSwitch(TCXEntity, SwitchEntity):
    """Control the confirmed TCX waterfall feature relay."""

    _attr_name = "Waterfall"
    _attr_icon = "mdi:fountain"

    def __init__(self, entry: TCXConfigEntry) -> None:
        super().__init__(entry)
        self._attr_unique_id = f"{entry.data['device_id']}_waterfall"

    @property
    def available(self) -> bool:
        data = self.coordinator.data or {}
        return (
            super().available
            and isinstance(data.get("waterfall"), bool)
            and data.get("pump_speed_control_supported") is True
            and isinstance(data.get("pump_min_rpm"), (int, float))
            and not isinstance(data.get("pump_min_rpm"), bool)
            and isinstance(data.get("pump_max_rpm"), (int, float))
            and not isinstance(data.get("pump_max_rpm"), bool)
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
            client = self.entry.runtime_data.client
            if enabled:
                await client.async_set_waterfall_with_speed(configured_waterfall_rpm(self.entry))
            else:
                await client.async_set_waterfall(False)
        except TCXError as err:
            raise HomeAssistantError(str(err)) from err
