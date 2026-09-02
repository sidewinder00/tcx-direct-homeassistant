from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import TCXAuthError, TCXClient, TCXConnectionError, TCXDevice
from .const import (
    CONF_DEVICE_ID,
    CONF_DEVICE_NAME,
    CONF_DEVICE_TYPE,
    CONF_EXPERIMENTAL_SCHEDULE_WRITES,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


class TCXDirectConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> TCXOptionsFlow:
        return TCXOptionsFlow()

    def __init__(self) -> None:
        self._username: str | None = None
        self._password: str | None = None
        self._devices: list[TCXDevice] = []
        self._reauth_entry = None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            self._username = str(user_input[CONF_USERNAME]).strip()
            self._password = str(user_input[CONF_PASSWORD])
            try:
                self._devices = await self._discover(self._username, self._password)
            except TCXAuthError:
                errors["base"] = "invalid_auth"
            except TCXConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error discovering TCX devices")
                errors["base"] = "unknown"
            else:
                if not self._devices:
                    errors["base"] = "no_tcx_devices"
                elif len(self._devices) == 1:
                    return await self._create_for_device(self._devices[0])
                else:
                    return await self.async_step_device()

        schema = vol.Schema(
            {
                vol.Required(CONF_USERNAME): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.EMAIL)
                ),
                vol.Required(CONF_PASSWORD): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.PASSWORD)
                ),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_device(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            serial = str(user_input[CONF_DEVICE_ID])
            device = next((d for d in self._devices if d.serial == serial), None)
            if device is not None:
                return await self._create_for_device(device)

        options = [
            {"value": dev.serial, "label": f"{dev.name} ({dev.serial})"} for dev in self._devices
        ]
        schema = vol.Schema(
            {
                vol.Required(CONF_DEVICE_ID): SelectSelector(
                    SelectSelectorConfig(
                        options=options,
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                )
            }
        )
        return self.async_show_form(step_id="device", data_schema=schema)

    async def _discover(self, username: str, password: str) -> list[TCXDevice]:
        client = TCXClient(async_get_clientsession(self.hass), username, password)
        await client.async_login()
        return await client.async_discover_devices()

    async def _create_for_device(self, device: TCXDevice) -> ConfigFlowResult:
        assert self._username is not None
        assert self._password is not None
        await self.async_set_unique_id(device.serial)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=device.name,
            data={
                CONF_USERNAME: self._username,
                CONF_PASSWORD: self._password,
                CONF_DEVICE_ID: device.serial,
                CONF_DEVICE_NAME: device.name,
                CONF_DEVICE_TYPE: device.device_type,
            },
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        self._reauth_entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        assert self._reauth_entry is not None
        errors: dict[str, str] = {}
        if user_input is not None:
            username = str(user_input[CONF_USERNAME]).strip()
            password = str(user_input[CONF_PASSWORD])
            try:
                devices = await self._discover(username, password)
            except TCXAuthError:
                errors["base"] = "invalid_auth"
            except TCXConnectionError:
                errors["base"] = "cannot_connect"
            else:
                serial = self._reauth_entry.data[CONF_DEVICE_ID]
                if not any(dev.serial == serial for dev in devices):
                    errors["base"] = "device_not_found"
                else:
                    new_data = {
                        **self._reauth_entry.data,
                        CONF_USERNAME: username,
                        CONF_PASSWORD: password,
                    }
                    self.hass.config_entries.async_update_entry(self._reauth_entry, data=new_data)
                    await self.hass.config_entries.async_reload(self._reauth_entry.entry_id)
                    return self.async_abort(reason="reauth_successful")

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_USERNAME,
                    default=self._reauth_entry.data.get(CONF_USERNAME, ""),
                ): TextSelector(TextSelectorConfig(type=TextSelectorType.EMAIL)),
                vol.Required(CONF_PASSWORD): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.PASSWORD)
                ),
            }
        )
        return self.async_show_form(step_id="reauth_confirm", data_schema=schema, errors=errors)


class TCXOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            # Retain unrelated options, including the user's Waterfall RPM.
            return self.async_create_entry(data={**self.config_entry.options, **user_input})
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_EXPERIMENTAL_SCHEDULE_WRITES,
                        default=self.config_entry.options.get(
                            CONF_EXPERIMENTAL_SCHEDULE_WRITES, False
                        ),
                    ): bool
                }
            ),
        )
