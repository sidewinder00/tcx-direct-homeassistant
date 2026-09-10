"""Exercise the real speed setter with delayed state and no cloud I/O."""

import asyncio
from unittest.mock import AsyncMock

import pytest
from test_api import make_client
from test_rest_pacing import no_network as no_network

from custom_components.tcx_direct import api


def rig():
    client = make_client()
    client.user_id = "synthetic"
    client.websocket_connected = True
    client.reported = {
        "systemMode": 1,
        "filt0": {"et": "F_CTRL", "app": "FILT", "manSpd": 2600, "minSpd": 600, "maxSpd": 3450},
        "ecm0": {"cmdSpd": 2600, "reqSpd": 2600},
    }
    client.async_get_shadow = AsyncMock(side_effect=AssertionError("No REST refresh"))

    class Socket:
        closed = False

        def __init__(self):
            self.frames = []

        async def send_json(self, frame):
            self.frames.append(frame)

    client._ws = Socket()
    return client


@pytest.mark.parametrize("arrival", [22, 44, 46])
def test_speed_confirmation_window_with_simulated_arrival(monkeypatch, arrival):
    async def run():
        client = rig()

        async def wait_for(future, timeout):
            assert timeout == 45
            # Neither a desired echo nor motor RPM alone confirms the filter setpoint.
            client.reported["ecm0"].update(cmdSpd=1200, reqSpd=1200)
            client._resolve_pending_control()
            assert not future.done()
            if arrival > timeout:
                future.cancel()
                raise asyncio.TimeoutError
            client.reported["filt0"]["manSpd"] = 1200
            client._resolve_pending_control()
            return await future

        monkeypatch.setattr(api.asyncio, "wait_for", wait_for)
        if arrival > 45:
            with pytest.raises(api.TCXConnectionError, match="within 45 seconds"):
                await client.async_set_pump_speed(1200)
            assert client.control_failure_count == 1
        else:
            await client.async_set_pump_speed(1200)
            assert client.control_success_count == 1
            assert client.last_control_error is None
        assert len(client._ws.frames) == 1
        assert client._ws.frames[0]["payload"]["state"]["desired"] == {"filt0": {"manSpd": 1200}}
        client.async_get_shadow.assert_not_called()
        assert client._pending_control is None
        assert not client._control_lock.locked()

    asyncio.run(run())


def test_real_timeout_releases_lock_without_retry(monkeypatch):
    async def run():
        client = rig()
        monkeypatch.setattr(api, "PUMP_SPEED_CONFIRM_TIMEOUT", 0.01)
        with pytest.raises(api.TCXConnectionError, match="did not confirm"):
            await client.async_set_pump_speed(1200)
        assert client.control_failure_count == 1
        assert len(client._ws.frames) == 1
        assert not client._control_lock.locked()
        assert client._pending_control is None
        client.async_get_shadow.assert_not_called()

    asyncio.run(run())
