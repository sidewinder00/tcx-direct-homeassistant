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
from .const import VERSION, VERSION_CODE
from .entity import TCXEntity


@dataclass(frozen=True, kw_only=True)
class TCXSensorDescription(SensorEntityDescription):
    data_key: str
    attribute_keys: tuple[str, ...] = ()


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
        key="pump_requested_rpm",
        data_key="pump_requested_rpm",
        name="Pump Requested RPM",
        native_unit_of_measurement="rpm",
        suggested_display_precision=0,
        icon="mdi:speedometer-medium",
    ),
    TCXSensorDescription(
        key="pump_operating_phase",
        data_key="pump_operating_phase",
        name="Pump Operating Phase",
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
        key="controller_mode",
        data_key="controller_mode",
        name="Controller Mode",
        icon="mdi:cog-transfer-outline",
        attribute_keys=("system_mode_code",),
    ),
    TCXSensorDescription(
        key="freeze_protection_setpoint",
        data_key="freeze_protection_setpoint",
        name="Freeze Protection Setpoint",
        suggested_display_precision=0,
        icon="mdi:snowflake-thermometer",
        entity_category=EntityCategory.DIAGNOSTIC,
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
        key="last_reported_equipment_state",
        data_key="last_websocket_state",
        name="Last Reported Equipment State",
        icon="mdi:clock-check-outline",
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
    TCXSensorDescription(
        key="control_status",
        data_key="control_status",
        name="Control Status",
        icon="mdi:remote",
        entity_category=EntityCategory.DIAGNOSTIC,
        attribute_keys=(
            "control_command_count",
            "control_success_count",
            "control_failure_count",
            "last_control_command_at",
            "last_control_command",
            "last_control_error",
            "last_control_confirmation_seconds",
            "last_control_failure_at",
            "last_control_failure_command",
            "last_control_failure_error",
        ),
    ),
)

VERSION_SENSOR_DESCRIPTION = SensorEntityDescription(
    key="integration_version",
    name="Integration Version",
    icon="mdi:tag-outline",
    entity_category=EntityCategory.DIAGNOSTIC,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TCXConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities(
        [
            *(TCXSensor(entry, description) for description in SENSORS),
            TCXIntegrationVersionSensor(entry),
        ]
    )


class TCXSensor(TCXEntity, SensorEntity):
    entity_description: TCXSensorDescription

    def __init__(self, entry: TCXConfigEntry, description: TCXSensorDescription) -> None:
        super().__init__(entry)
        self.entity_description = description
        self._attr_unique_id = f"{entry.data['device_id']}_{description.key}"

    @property
    def native_value(self) -> Any:
        return (self.coordinator.data or {}).get(self.entity_description.data_key)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        data = self.coordinator.data or {}
        attributes = {
            key: data[key]
            for key in self.entity_description.attribute_keys
            if data.get(key) is not None
        }
        return attributes or None


class TCXIntegrationVersionSensor(TCXEntity, SensorEntity):
    """Expose the installed TCX Direct release independently of cloud state."""

    entity_description = VERSION_SENSOR_DESCRIPTION
    _attr_native_value = VERSION

    def __init__(self, entry: TCXConfigEntry) -> None:
        super().__init__(entry)
        self._attr_unique_id = f"{entry.data['device_id']}_integration_version"

    @property
    def available(self) -> bool:
        """Keep static release metadata available during TCX cloud outages."""
        return True

    @property
    def extra_state_attributes(self) -> dict[str, int]:
        return {"version_code": VERSION_CODE}
