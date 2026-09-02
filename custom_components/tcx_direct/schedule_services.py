"""Home Assistant adapters for previewed, experimental native schedule changes."""

from __future__ import annotations

from functools import partial

import voluptuous as vol
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.service import async_register_admin_service

from .api import TCXError
from .const import DOMAIN
from .schedules import OPERATIONS, ScheduleError


def _integer_rpm(value):
    """Accept whole numeric selector values, never silently truncate fractional RPM."""
    if type(value) not in (int, float) or not 0 < value < 100_000 or int(value) != value:
        raise vol.Invalid("RPM must be a positive whole number")
    return int(value)


def _boolean(value):
    if type(value) is not bool:
        raise vol.Invalid("enabled must be a boolean")
    return value


def _weekday(value):
    if type(value) is not int or not 0 <= value <= 6:
        raise vol.Invalid("weekday_codes must contain integers from 0 to 6")
    return value


_ENTRY = {vol.Required("config_entry_id"): cv.string}
_SOURCE = {vol.Optional("source", default="rest"): vol.In(("rest", "websocket_authorization"))}
_SCHEMAS = {
    "get_native_schedules": vol.Schema({**_ENTRY, **_SOURCE}),
    "preview_native_schedule": vol.Schema(
        {
            **_ENTRY,
            vol.Required("operation"): vol.In(OPERATIONS),
            vol.Optional("schedule_id"): cv.string,
            vol.Optional("start"): cv.string,
            vol.Optional("end"): cv.string,
            vol.Optional("weekday_codes"): [_weekday],
            vol.Optional("rpm"): _integer_rpm,
            vol.Optional("enabled"): _boolean,
        }
    ),
    "apply_native_schedule": vol.Schema({**_ENTRY, vol.Required("plan_id"): cv.string}),
    "acknowledge_native_schedule_write": vol.Schema(
        {
            **_ENTRY,
            **_SOURCE,
            vol.Required("plan_id"): cv.string,
            vol.Required("revision"): cv.string,
        }
    ),
}


def async_register_schedule_services(hass: HomeAssistant) -> None:
    """Register even without loaded entries; report an actionable error on use."""

    async def handle(call: ServiceCall) -> ServiceResponse:
        entry = hass.config_entries.async_get_entry(call.data["config_entry_id"])
        if entry is None or entry.domain != DOMAIN or entry.state is not ConfigEntryState.LOADED:
            raise ServiceValidationError("Select a loaded TCX Direct config entry")
        manager = entry.runtime_data.client.schedules
        args = {key: value for key, value in call.data.items() if key != "config_entry_id"}
        try:
            if call.service == "get_native_schedules":
                result = await manager.async_read(**args)
            elif call.service == "preview_native_schedule":
                result = await manager.async_preview(**args)
            elif call.service == "apply_native_schedule":
                result = await manager.async_apply(**args)
            else:
                result = await manager.async_acknowledge(**args)
        except ScheduleError as err:
            raise ServiceValidationError(str(err)) from err
        except TCXError as err:
            raise HomeAssistantError(str(err)) from err
        return result if call.return_response else None

    for name, schema in _SCHEMAS.items():
        register = hass.services.async_register
        if name in ("apply_native_schedule", "acknowledge_native_schedule_write"):
            register = partial(async_register_admin_service, hass)
        register(
            DOMAIN,
            name,
            handle,
            schema=schema,
            supports_response=SupportsResponse.ONLY
            if name in ("get_native_schedules", "preview_native_schedule")
            else SupportsResponse.OPTIONAL,
        )
