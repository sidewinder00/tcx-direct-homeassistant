"""Synthetic execution regressions for the capture-47 split setpoint/motor state."""

import asyncio

import pytest
from test_api import post_prime_client

from custom_components.tcx_direct import api


async def until(predicate):
    async with asyncio.timeout(1):
        while not predicate():
            await asyncio.sleep(0)


def rig(monkeypatch, filter_key="filt0"):
    client = post_prime_client(filter_key)
    client.reported["systemMode"] = 1
    client.user_id = "synthetic"
    client.websocket_connected = True
    client.reported["ecm0"]["reqSpd"] = 2575
    monkeypatch.setattr(api, "POST_PRIME_SYNC_INTERVAL", 0)
    monkeypatch.setattr(api, "POST_PRIME_MOTOR_CONFIRM_TIMEOUT", 0.05)

    class Socket:
        closed = False

        def __init__(self):
            self.frames = []

        async def send_json(self, message):
            desired = message["payload"]["state"]["desired"]
            self.frames.append(desired)
            # A controller setpoint echo alone does not alter motor state.
            client.reported[filter_key]["manSpd"] = desired[filter_key]["manSpd"]
            client._resolve_pending_control()

    socket = Socket()
    client._ws = socket
    return client, socket


async def leave_priming(client, rpm=2600):
    client._schedule_post_prime_sync(2575)
    task = client._post_prime_sync_task
    await until(lambda: client._post_prime_sync_context.priming_observed)
    client.reported["ecm0"].update(manSpd=rpm, reqSpd=rpm, cmdSpd=rpm)
    return task


@pytest.mark.parametrize("filter_key", ["filt0", "filt3"])
def test_equal_setpoint_wrong_motor_sends_once_and_waits_for_execution(monkeypatch, filter_key):
    async def run():
        client, socket = rig(monkeypatch, filter_key)
        task = await leave_priming(client)
        await until(lambda: bool(socket.frames))
        await until(lambda: not client._control_lock.locked())
        assert client.post_prime_sync_success_count == 0
        assert not task.done()
        assert client.reported[filter_key]["manSpd"] == 2575
        # Requested speed alone is still insufficient.
        client.reported["ecm0"]["reqSpd"] = 2575
        await asyncio.sleep(0)
        assert client.post_prime_sync_success_count == 0
        client.reported["ecm0"]["cmdSpd"] = 2575
        await task
        assert socket.frames == [{filter_key: {"manSpd": 2575}}]
        assert client.last_post_prime_sync_result == "motor_speed_confirmed"
        assert client.post_prime_sync_success_count == 1

    asyncio.run(run())


def test_setpoint_only_ack_times_out_without_retry(monkeypatch):
    async def run():
        client, socket = rig(monkeypatch)
        task = await leave_priming(client)
        await task
        assert len(socket.frames) == 1
        assert client.control_success_count == 1  # setpoint only
        assert client.post_prime_sync_success_count == 0
        assert client.post_prime_sync_timeout_count == 1
        assert client.last_post_prime_sync_result == "motor_speed_not_confirmed"
        assert client.reported["ecm0"]["cmdSpd"] == 2600

    asyncio.run(run())


@pytest.mark.parametrize(
    "interruption", ["desired", "manual", "off", "waterfall", "mode", "disconnect", "socket"]
)
def test_interrupted_motor_wait_never_reasserts_target(monkeypatch, interruption):
    async def run():
        client, socket = rig(monkeypatch)
        task = await leave_priming(client)
        await until(lambda: bool(socket.frames))
        await until(lambda: not client._control_lock.locked())
        if interruption == "desired":
            client._handle_post_prime_desired({"filt0": {"manSpd": 1800}})
        elif interruption == "manual":
            await client.async_set_pump_speed(1800)
        elif interruption == "off":
            client.reported["pool"]["st"] = 0
        elif interruption == "waterfall":
            client.reported["fcr0"]["st"] = 1
        elif interruption == "mode":
            client.reported["systemMode"] = 3
        elif interruption == "disconnect":
            client.websocket_connected = False
        else:
            client._ws = object()
        await asyncio.gather(task, return_exceptions=True)
        assert socket.frames.count({"filt0": {"manSpd": 2575}}) == 1
        assert len(socket.frames) == (2 if interruption == "manual" else 1)
        assert client.post_prime_sync_success_count == 0

    asyncio.run(run())


def test_already_matching_motor_does_not_write(monkeypatch):
    async def run():
        client, socket = rig(monkeypatch)
        task = await leave_priming(client, rpm=2575)
        await task
        assert not socket.frames
        assert client.post_prime_sync_success_count == 1

    asyncio.run(run())


def test_no_write_during_priming_and_no_background_retry_after_cancel(monkeypatch):
    async def run():
        client, socket = rig(monkeypatch)
        client._schedule_post_prime_sync(2575)
        await until(lambda: client._post_prime_sync_context.priming_observed)
        assert not socket.frames
        await client._async_cancel_post_prime_sync("pump_off_commanded")
        assert not socket.frames
        assert client.post_prime_sync_success_count == 0

    asyncio.run(run())


def test_failed_corrective_send_is_not_retried(monkeypatch):
    async def run():
        client, socket = rig(monkeypatch)

        async def fail(message):
            socket.frames.append(message["payload"]["state"]["desired"])
            raise ConnectionError("synthetic send failure")

        socket.send_json = fail
        task = await leave_priming(client)
        await task
        assert len(socket.frames) == 1
        assert client.post_prime_sync_success_count == 0
        assert client.post_prime_sync_state == "failed"

    asyncio.run(run())


def test_new_generation_cancels_old_motor_observation(monkeypatch):
    async def run():
        client, socket = rig(monkeypatch)
        task = await leave_priming(client)
        await until(lambda: bool(socket.frames))
        await until(lambda: not client._control_lock.locked())
        await client._async_cancel_post_prime_sync("superseded_by_schedule")
        client.reported["filt0"]["manSpd"] = 2600
        client._schedule_post_prime_sync(2600)
        replacement = client._post_prime_sync_task
        await replacement
        assert task.done()
        assert len(socket.frames) == 1
        assert client.post_prime_sync_success_count == 1
        assert client.post_prime_sync_target == 2600

    asyncio.run(run())
