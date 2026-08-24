from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription
from homeassistant.const import (
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import TCXConfigEntry
from .entity import TCXEntity


@dataclass(frozen=True, kw_only=True)
class TCXSensorDescription(SensorEntityDescription):
    data_key: str


SENSORS = (
    TCXSensorDescription(
        key="pool_temperature",
        data_key="pool_temperature",
        name="Pool Temperature",
        native_unit_of_measurement=UnitOfTemperature.FAHRENHEIT,
        icon="mdi:pool-thermometer",
    ),
    TCXSensorDescription(
        key="air_temperature",
        data_key="air_temperature",
        name="Equipment Air Temperature",
        native_unit_of_measurement=UnitOfTemperature.FAHRENHEIT,
        entity_registry_enabled_default=False,
        icon="mdi:thermometer",
    ),
    TCXSensorDescription(
        key="pool_temperature_setpoint",
        data_key="pool_temperature_setpoint",
        name="Pool Temperature Setpoint",
        native_unit_of_measurement=UnitOfTemperature.FAHRENHEIT,
        icon="mdi:thermometer-water",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TCXSensorDescription(
        key="pump_rpm",
        data_key="pump_rpm",
        name="Pump RPM",
        native_unit_of_measurement="rpm",
        suggested_display_precision=0,
        icon="mdi:pump",
    ),
    TCXSensorDescription(
        key="pump_preset",
        data_key="pump_preset",
        name="Pump Preset",
        icon="mdi:tune-variant",
    ),
    TCXSensorDescription(
        key="pump_min_rpm",
        data_key="pump_min_rpm",
        name="Pump Minimum RPM",
        native_unit_of_measurement="rpm",
        suggested_display_precision=0,
        icon="mdi:speedometer-slow",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TCXSensorDescription(
        key="pump_max_rpm",
        data_key="pump_max_rpm",
        name="Pump Maximum RPM",
        native_unit_of_measurement="rpm",
        suggested_display_precision=0,
        icon="mdi:speedometer",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TCXSensorDescription(
        key="swc_level",
        data_key="swc_level",
        name="Salt Water Chlorinator Level",
        native_unit_of_measurement=PERCENTAGE,
        entity_registry_enabled_default=False,
        icon="mdi:water-percent",
    ),
    TCXSensorDescription(
        key="light_color",
        data_key="light_color",
        name="Light Color",
        icon="mdi:palette",
    ),
    TCXSensorDescription(
        key="light_color_name",
        data_key="light_color_name",
        name="Light Color Name",
        icon="mdi:palette-outline",
    ),
    TCXSensorDescription(
        key="wifi_rssi",
        data_key="wifi_rssi",
        name="Wi-Fi Signal",
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        icon="mdi:wifi",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TCXSensorDescription(
        key="firmware_version",
        data_key="firmware_version",
        name="Firmware Version",
        icon="mdi:chip",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TCXSensorDescription(
        key="connection_type",
        data_key="connection_type",
        name="Connection Type",
        icon="mdi:access-point-network",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TCXSensorDescription(
        key="last_successful_update",
        data_key="last_successful_update",
        name="Last Successful Update",
        icon="mdi:clock-check-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TCXSensorDescription(
        key="last_websocket_message",
        data_key="last_websocket_message",
        name="Last WebSocket Message",
        icon="mdi:message-badge-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TCXSensorDescription(
        key="last_shadow_update",
        data_key="last_shadow_update",
        name="Last Shadow Update",
        icon="mdi:cloud-refresh-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TCXSensorDescription(
        key="websocket_messages_received",
        data_key="websocket_messages_received",
        name="WebSocket Messages Received",
        icon="mdi:counter",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TCXSensorDescription(
        key="websocket_reconnect_count",
        data_key="websocket_reconnect_count",
        name="WebSocket Reconnect Count",
        icon="mdi:connection",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TCXSensorDescription(
        key="watchdog_reconnect_count",
        data_key="watchdog_reconnect_count",
        name="Watchdog Reconnect Count",
        icon="mdi:dog-service",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TCXConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities(TCXSensor(entry, description) for description in SENSORS)


class TCXSensor(TCXEntity, SensorEntity):
    entity_description: TCXSensorDescription

    def __init__(self, entry: TCXConfigEntry, description: TCXSensorDescription) -> None:
        super().__init__(entry)
        self.entity_description = description
        self._attr_unique_id = f"{entry.data['device_id']}_{description.key}"

    @property
    def native_value(self) -> Any:
        return (self.coordinator.data or {}).get(self.entity_description.data_key)
