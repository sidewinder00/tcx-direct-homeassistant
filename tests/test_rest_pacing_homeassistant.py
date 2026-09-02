"""Exercise real HA setup/diagnostics boundaries without network or equipment I/O."""

from __future__ import annotations

import asyncio
import importlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from test_rest_pacing import Clock, Response, offline_client
from test_rest_pacing import no_network as no_network  # shared autouse fixture

pytest.importorskip("homeassistant")


@pytest.mark.parametrize("already_cooling", [False, True])
def test_setup_degrades_gracefully_for_real_429_and_local_deferral(monkeypatch, already_cooling):
    import custom_components.tcx_direct as integration

    importlib.reload(integration)

    async def run():
        Clock().install(monkeypatch)
        client, session = offline_client([Response(429, "600")])
        client.websocket_connected = False
        if already_cooling:
            with pytest.raises(integration.TCXConnectionError):
                await client.async_get_shadow()
        client.async_login = AsyncMock()
        client.async_start = AsyncMock()
        coordinator = SimpleNamespace(
            async_load_cache=AsyncMock(return_value=False), async_handle_state=AsyncMock()
        )
        store = SimpleNamespace(async_load=AsyncMock(return_value=None), async_save=AsyncMock())
        monkeypatch.setattr(integration, "TCXClient", lambda *args: client)
        monkeypatch.setattr(integration, "TCXCoordinator", lambda *args: coordinator)
        monkeypatch.setattr(integration, "Store", lambda *args: store)
        monkeypatch.setattr(integration, "async_get_clientsession", lambda hass: session)
        forward = AsyncMock()
        hass = SimpleNamespace(config_entries=SimpleNamespace(async_forward_entry_setups=forward))
        entry = SimpleNamespace(
            entry_id="test-entry",
            data={"username": "test@example.invalid", "password": "unused", "device_id": "test"},
            options={},
            async_on_unload=Mock(),
            add_update_listener=Mock(),
        )
        assert await integration.async_setup_entry(hass, entry) is True
        assert entry.runtime_data.client is client
        client.async_start.assert_awaited_once()
        forward.assert_awaited_once_with(entry, integration.PLATFORMS)
        coordinator.async_handle_state.assert_not_awaited()
        assert client.shadow_rate_limit_count == client.shadow_failure_count == 1
        assert client.shadow_deferred_count == int(already_cooling)
        assert client.shadow_http_attempt_count == len(session.calls) == 1
        assert not client.schedules.writes_enabled
        assert client.control_command_count == 0

    asyncio.run(run())


@pytest.mark.parametrize("header, indefinite", [("600", False), ("9" * 400, True)])
def test_cooldown_diagnostics_are_finite_and_download_only(monkeypatch, header, indefinite):
    import custom_components.tcx_direct as integration

    importlib.reload(integration)
    from custom_components.tcx_direct import api
    from custom_components.tcx_direct.diagnostics import async_get_config_entry_diagnostics

    async def run():
        Clock().install(monkeypatch)
        client, session = offline_client([Response(429, header)])
        with pytest.raises(api.TCXRateLimited):
            await client.async_get_shadow()
        with pytest.raises(api.TCXShadowDeferred):
            await client.async_get_shadow()
        coordinator = SimpleNamespace(
            source="websocket",
            using_cached_data=False,
            last_successful_update=None,
            normalized={},
            raw_reported={},
            pump_zero_suppression_pending=False,
            pump_zero_suppression_count=0,
            last_pump_zero_suppressed_at=None,
        )
        entry = SimpleNamespace(
            runtime_data=SimpleNamespace(client=client, coordinator=coordinator),
            data={},
            options={},
        )
        result = await async_get_config_entry_diagnostics(None, entry)
        shadow = result["shadow"]
        assert shadow["request_count"] == 2
        assert shadow["http_attempt_count"] == shadow["rate_limit_count"] == 1
        assert shadow["deferred_count"] == shadow["failure_count"] == 1
        assert shadow["cooldown_remaining_seconds"] == (None if indefinite else 600)
        assert shadow["cooldown_indefinite"] is indefinite
        json.dumps(result, allow_nan=False)
        assert len(session.calls) == 1
        assert client.control_command_count == 0

    asyncio.run(run())
