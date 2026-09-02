from __future__ import annotations

import logging
from dataclasses import dataclass

import voluptuous as vol
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import service
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType

from .api import (
    TCXAuthError,
    TCXClient,
    TCXConnectionError,
    TCXShadowUnsupported,
)
from .const import (
    ATTR_RPM,
    CONF_DEVICE_ID,
    CONF_EXPERIMENTAL_SCHEDULE_WRITES,
    DOMAIN,
    PLATFORMS,
    SERVICE_START_PUMP_AT_SPEED,
)
from .coordinator import TCXCoordinator
from .schedule_services import async_register_schedule_services
from .schedules import ScheduleError

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class TCXRuntimeData:
    client: TCXClient
    coordinator: TCXCoordinator


TCXConfigEntry = ConfigEntry[TCXRuntimeData]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register TCX Direct service actions independently of config entries."""
    async_register_schedule_services(hass)
    service.async_register_platform_entity_service(
        hass,
        DOMAIN,
        SERVICE_START_PUMP_AT_SPEED,
        entity_domain=SWITCH_DOMAIN,
        schema={vol.Required(ATTR_RPM): cv.positive_float},
        func="async_start_pump_at_speed",
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: TCXConfigEntry) -> bool:
    session = async_get_clientsession(hass)
    client = TCXClient(
        session,
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
        entry.data[CONF_DEVICE_ID],
    )
    coordinator = TCXCoordinator(hass, client, entry.entry_id)
    schedule_store = Store(hass, 1, f"{DOMAIN}.schedules.{entry.entry_id}")
    try:
        client.schedules.configure_storage(
            await schedule_store.async_load(), schedule_store.async_save
        )
    except (OSError, HomeAssistantError, ScheduleError) as err:
        # A broken experimental journal must not stop existing pump telemetry or
        # controls, and must never be silently discarded to permit another add.
        client.schedules.storage_error = "Schedule journal unavailable; schedule writes blocked"
        _LOGGER.error("Native schedule journal could not be loaded: %s", err)
    client.schedules.writes_enabled = entry.options.get(CONF_EXPERIMENTAL_SCHEDULE_WRITES, False)
    had_cache = await coordinator.async_load_cache()

    # Authentication/device discovery already succeeded during config flow, but
    # perform a fresh login on each HA startup. Failure to authenticate is a
    # real setup blocker; REST shadow availability is not.
    try:
        await client.async_login()
    except TCXAuthError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except TCXConnectionError as err:
        if not had_cache:
            raise ConfigEntryNotReady(str(err)) from err
        _LOGGER.warning(
            "Starting TCX Direct from cache while login service is unavailable: %s", err
        )

    # Shadow is only a secondary snapshot/watchdog path. TCX controllers may
    # reject it entirely while their Zodiac WebSocket works normally. Never
    # fail integration setup solely because shadow retrieval failed.
    if client.id_token is not None:
        try:
            await client.async_get_shadow()
            await coordinator.async_handle_state(client.reported, "shadow")
        except TCXShadowUnsupported as err:
            _LOGGER.info("TCX REST shadow unavailable; starting WebSocket-only: %s", err)
        except TCXAuthError as err:
            client.id_token = None
            _LOGGER.warning(
                "TCX shadow authorization failed; WebSocket supervisor will reauthenticate: %s",
                err,
            )
        except TCXConnectionError as err:
            _LOGGER.warning("TCX shadow read failed; continuing with WebSocket transport: %s", err)

    entry.runtime_data = TCXRuntimeData(client=client, coordinator=coordinator)
    entry.async_on_unload(entry.add_update_listener(async_update_options))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await client.async_start()
    return True


async def async_update_options(hass: HomeAssistant, entry: TCXConfigEntry) -> None:
    """Apply the write opt-in without reconnecting or touching pump state."""
    entry.runtime_data.client.schedules.writes_enabled = entry.options.get(
        CONF_EXPERIMENTAL_SCHEDULE_WRITES, False
    )
    await entry.runtime_data.coordinator.async_handle_status()


async def async_unload_entry(hass: HomeAssistant, entry: TCXConfigEntry) -> bool:
    await entry.runtime_data.client.async_stop()
    await entry.runtime_data.coordinator.async_shutdown()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
