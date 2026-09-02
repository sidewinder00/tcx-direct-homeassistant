"""Real HA service/selector/flow tests; the separate CI HA job installs its runtime."""

from __future__ import annotations

import asyncio
import importlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("homeassistant")

from homeassistant.config_entries import ConfigEntryState  # noqa: E402
from homeassistant.core import Context, HomeAssistant  # noqa: E402
from homeassistant.exceptions import ServiceValidationError, Unauthorized  # noqa: E402
from homeassistant.helpers.selector import selector  # noqa: E402
from homeassistant.helpers.storage import Store  # noqa: E402
from test_schedules import Rig  # noqa: E402

from custom_components.tcx_direct.schedule_services import (  # noqa: E402
    _SCHEMAS,
    async_register_schedule_services,
)


def test_real_ha_preview_apply_services_and_missing_entries(tmp_path):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        rig = Rig()
        config = SimpleNamespace(
            domain="tcx_direct",
            state=ConfigEntryState.LOADED,
            runtime_data=SimpleNamespace(client=rig.client),
        )
        hass.config_entries = SimpleNamespace(
            async_get_entry=lambda key: config if key == "test" else None
        )
        async_register_schedule_services(hass)
        args = {
            "config_entry_id": "test",
            "operation": "create",
            "start": "11:00",
            "end": "11:15",
            "rpm": 2650,
            "weekday_codes": [5],
        }
        plan = await hass.services.async_call(
            "tcx_direct", "preview_native_schedule", args, blocking=True, return_response=True
        )
        assert not rig.messages
        result = await hass.services.async_call(
            "tcx_direct",
            "apply_native_schedule",
            {"config_entry_id": "test", "plan_id": plan["plan_id"]},
            blocking=True,
            return_response=True,
        )
        assert result["result"] == "confirmed"
        assert rig.remote["sh"]["1"]["en"] == 0
        with pytest.raises(ServiceValidationError, match="loaded"):
            await hass.services.async_call(
                "tcx_direct",
                "get_native_schedules",
                {"config_entry_id": "missing"},
                blocking=True,
                return_response=True,
            )
        config.domain = "other_integration"
        with pytest.raises(ServiceValidationError):
            await hass.services.async_call(
                "tcx_direct",
                "get_native_schedules",
                {"config_entry_id": "test"},
                blocking=True,
                return_response=True,
            )

    asyncio.run(run())


def test_non_admin_cannot_apply_schedule(tmp_path):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        hass.auth = SimpleNamespace(
            async_get_user=AsyncMock(return_value=SimpleNamespace(is_admin=False))
        )
        async_register_schedule_services(hass)
        with pytest.raises(Unauthorized):
            await hass.services.async_call(
                "tcx_direct",
                "apply_native_schedule",
                {"config_entry_id": "test", "plan_id": "test"},
                blocking=True,
                context=Context(user_id="non-admin"),
            )

    asyncio.run(run())


@pytest.mark.parametrize("rpm", [2650.1, True, "2650", 0, -1, float("inf")])
def test_service_schema_does_not_truncate_rpm(rpm):
    import voluptuous as vol

    with pytest.raises(vol.Invalid):
        _SCHEMAS["preview_native_schedule"](
            {"config_entry_id": "test", "operation": "create", "rpm": rpm}
        )


def test_service_selectors_and_translations_match_real_ha():
    import yaml

    root = Path("custom_components/tcx_direct")
    services = yaml.safe_load((root / "services.yaml").read_text())
    strings = json.loads((root / "strings.json").read_text())
    assert strings == json.loads((root / "translations/en.json").read_text())
    for name, spec in services.items():
        assert name in strings["services"]
        for field, definition in spec.get("fields", {}).items():
            assert field in strings["services"][name]["fields"]
            selector(definition["selector"])


def test_real_storage_latch_survives_manager_recreation(tmp_path):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        store = Store(hass, 1, "tcx_direct.test_schedules")
        rig = Rig()
        rig.manager.configure_storage(None, store.async_save)
        # Test persistence independently of the synthetic transport's disk assertion.
        pending = {"plan_id": "uncertain-test", "operation": "create", "state": "outcome_uncertain"}
        await store.async_save({"pending": pending})
        restored = Rig()
        restored.manager.configure_storage(
            await Store(hass, 1, "tcx_direct.test_schedules").async_load(), store.async_save
        )
        assert restored.manager.pending == pending
        assert restored.manager.snapshot()["status"] == "needs_review"

    asyncio.run(run())


def test_options_flow_preserves_other_options_and_sensor_imports():
    import custom_components.tcx_direct as integration

    integration = importlib.reload(integration)  # conftest normally bypasses HA __init__
    from custom_components.tcx_direct.config_flow import TCXOptionsFlow
    from custom_components.tcx_direct.sensor import SENSORS

    async def run():
        flow = TCXOptionsFlow()
        # Config entry is normally provided by the HA options-flow manager.
        flow.handler = "test"
        flow.hass = SimpleNamespace(
            config_entries=SimpleNamespace(
                async_get_known_entry=lambda key: SimpleNamespace(options={"waterfall_rpm": 2850})
            )
        )
        form = await flow.async_step_init()
        assert form["data_schema"]({}) == {"experimental_schedule_writes": False}
        result = await flow.async_step_init({"experimental_schedule_writes": True})
        assert result["data"] == {"waterfall_rpm": 2850, "experimental_schedule_writes": True}
        assert any(item.key == "native_schedules" for item in SENSORS)
        coordinator = SimpleNamespace(async_handle_status=AsyncMock())
        rig = Rig()
        config = SimpleNamespace(
            options={"experimental_schedule_writes": False},
            runtime_data=SimpleNamespace(client=rig.client, coordinator=coordinator),
        )
        await integration.async_update_options(None, config)
        assert not rig.manager.writes_enabled
        assert not rig.messages

    asyncio.run(run())
