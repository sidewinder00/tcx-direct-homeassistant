"""Fresh Authorization snapshots for normal native schedule operations."""

from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiohttp
import pytest
from test_schedules import Rig, entry

from custom_components.tcx_direct import api, schedules
from custom_components.tcx_direct.schedules import ScheduleError


@pytest.fixture(autouse=True)
def quick_snapshot_timeout(monkeypatch):
    monkeypatch.setattr(schedules, "WS_SNAPSHOT_TIMEOUT", 0.01)


def test_disabled_two_entry_lifecycle_never_requires_rest_or_changes_equipment():
    async def run():
        rig = Rig({})
        baseline = deepcopy(rig.remote)
        rig.client.async_get_shadow = AsyncMock(side_effect=api.TCXShadowUnsupported("No REST"))
        rig.manager.writes_enabled = False
        initial = await rig.manager.async_read()
        assert initial["schedules"] == [] and initial["status"] == "read_only"
        assert initial["snapshot_source"] == "websocket_authorization"
        assert len(rig.subscriptions) == 1 and not rig.messages

        first = await rig.create()
        assert first["after"]["en"] == 0
        assert first["snapshot_source"] == "websocket_authorization"
        assert len(rig.subscriptions) == 2 and not rig.messages
        with pytest.raises(ScheduleError, match="disabled"):
            await rig.manager.async_apply(first["plan_id"])
        rig.manager.writes_enabled = True
        first_result = await rig.manager.async_apply(first["plan_id"])
        assert len(rig.subscriptions) == 4  # fresh preflight AND fresh readback
        first_id = first_result["schedule_id"]
        assert first_result["snapshot_source"] == "websocket_authorization"

        second = await rig.manager.async_preview(
            "create", start="11:30", end="11:45", weekday_codes=[5], rpm=2700
        )
        second_id = (await rig.manager.async_apply(second["plan_id"]))["schedule_id"]
        second_before = deepcopy(rig.remote["sh"][second_id])
        assert second_before["en"] == 0 and first_id != second_id
        update = await rig.manager.async_preview("update", first_id, rpm=2750)
        await rig.manager.async_apply(update["plan_id"])
        assert rig.remote["sh"][first_id]["ar"] == 2750
        assert rig.remote["sh"][first_id]["en"] == 0
        assert rig.remote["sh"][second_id] == second_before

        delete_first = await rig.manager.async_preview("delete", first_id)
        await rig.manager.async_apply(delete_first["plan_id"])
        assert rig.remote["sh"][second_id] == second_before
        delete_second = await rig.manager.async_preview("delete", second_id)
        await rig.manager.async_apply(delete_second["plan_id"])
        assert (await rig.manager.async_read())["schedules"] == []
        assert {k: v for k, v in rig.remote.items() if k != "sh"} == {
            k: v for k, v in baseline.items() if k != "sh"
        }
        assert len(rig.messages) == 5  # two creates, one update, two deletes
        assert all(set(f["payload"]["state"]["desired"]) == {"sh"} for f in rig.messages)
        rig.client.async_get_shadow.assert_not_awaited()
        assert rig.disk["pending"] is None

    asyncio.run(run())


@pytest.mark.parametrize("phase", ["read", "preview", "preflight", "readback"])
@pytest.mark.parametrize("kind", ["cached", "delta", "rest", "wrong_socket", "missing", "null"])
def test_every_stage_requires_its_own_complete_authorization_snapshot(phase, kind):
    async def run():
        rig = Rig({})
        rig.client.async_get_shadow = AsyncMock(side_effect=AssertionError("No REST fallback"))
        plan = await rig.create() if phase in ("preflight", "readback") else None
        subscribe = rig.subscribe

        async def invalid_snapshot(frame):
            if phase == "readback" and not rig.messages:
                return await subscribe(frame)
            rig.subscriptions.append(deepcopy(frame))
            if kind == "cached":
                return
            reported = (
                {"systemMode": 1}
                if kind == "missing"
                else {"sh": None}
                if kind == "null"
                else rig.remote
            )
            rig.manager.observe(
                reported,
                full=kind != "delta",
                source="rest"
                if kind == "rest"
                else "websocket"
                if kind == "delta"
                else "websocket_authorization",
                websocket=object() if kind == "wrong_socket" else rig,
            )

        rig.subscribe = invalid_snapshot
        with pytest.raises(ScheduleError, match="No new complete"):
            if phase == "read":
                await rig.manager.async_read()
            elif phase == "preview":
                await rig.create()
            else:
                await rig.manager.async_apply(plan["plan_id"])
        assert rig.manager._snapshot_request is None
        assert not rig.manager._plans
        if phase == "readback":
            assert len(rig.messages) == 1
            assert rig.disk["pending"]["state"] == "outcome_uncertain"
            assert rig.disk["pending"]["snapshot_source"] == "websocket_authorization"
            with pytest.raises(ScheduleError, match="review"):
                await rig.manager.async_apply(plan["plan_id"])
            assert len(rig.messages) == 1
        else:
            assert not rig.messages and rig.disk["pending"] is None
        rig.client.async_get_shadow.assert_not_awaited()

    asyncio.run(run())


@pytest.mark.parametrize("stage", ["save", "after_write"])
@pytest.mark.parametrize("change", ["closed", "replaced"])
def test_apply_stays_on_the_connection_that_supplied_preflight(stage, change):
    async def run():
        rig = Rig()
        plan = await rig.create()

        def change_connection():
            if change == "closed":
                rig.closed = True
            else:
                rig.client._ws = SimpleNamespace(closed=False)

        if stage == "save":

            async def save(data):
                await rig.save(data)
                change_connection()

            rig.manager._save = save
        else:
            send = rig.client._async_send_control

            async def write(*args, **kwargs):
                await send(*args, **kwargs)
                change_connection()

            rig.client._async_send_control = write
        with pytest.raises(ScheduleError, match="connection changed|connected TCX"):
            await rig.manager.async_apply(plan["plan_id"])
        assert len(rig.messages) == (1 if stage == "after_write" else 0)
        assert len(rig.subscriptions) == 2
        assert rig.disk["pending"]["plan_id"] == plan["plan_id"]

    asyncio.run(run())


def test_cancelled_request_cannot_supply_a_later_read():
    async def run():
        rig = Rig({})
        started = asyncio.Event()
        subscribe = rig.subscribe

        async def silent(frame):
            rig.subscriptions.append(frame)
            started.set()

        rig.subscribe = silent
        task = asyncio.create_task(rig.manager.async_read())
        await started.wait()
        old_future = rig.manager._snapshot_request[1]
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert old_future.cancelled() and rig.manager._snapshot_request is None
        rig.manager.observe(rig.remote, full=True, source="websocket_authorization", websocket=rig)
        with pytest.raises(ScheduleError, match="No new complete"):
            await rig.manager.async_read()
        rig.subscribe = subscribe
        assert (await rig.manager.async_read())["schedules"] == []
        assert rig.disk["pending"] is None and not rig.messages

    asyncio.run(run())


@pytest.mark.parametrize("response_kind", ["complete", "desired_only", "delta"])
def test_real_socket_empty_sched_namespace_without_pending_write(response_kind):
    async def run():
        rig = Rig({"9": entry()})  # stale cache must be replaced, not merged
        rig.manager.writes_enabled = False
        ready = asyncio.Event()
        rig.client.async_get_shadow = AsyncMock(
            return_value={"state": {"reported": {"systemMode": 1}}}
        )

        class Socket:
            closed = False

            def __init__(self):
                self.queue = asyncio.Queue()
                self.frames = []

            def __aiter__(self):
                return self

            async def __anext__(self):
                return await self.queue.get()

            async def send_json(self, frame):
                assert frame["action"] == "subscribe" and set(frame["payload"]) == {"userId"}
                self.frames.append(frame)
                payload = {
                    "main": {"state": {"reported": {"systemMode": 1}}},
                    "sched": {"state": {"reported": {"sh": {}}}},
                }
                if response_kind == "desired_only":
                    payload["sched"] = {"state": {"desired": {"sh": {}}}}
                self.queue.put_nowait(
                    SimpleNamespace(
                        type=aiohttp.WSMsgType.TEXT,
                        data=json.dumps(
                            {
                                "service": "StateStreamer"
                                if response_kind == "delta"
                                else "Authorization",
                                "namespace": "authorization",
                                "payload": payload,
                            }
                        ),
                    )
                )

            async def close(self):
                self.closed = True

        socket = Socket()
        rig.client._open_websocket = AsyncMock(return_value=socket)
        rig.client._bootstrap_resubscribe = AsyncMock()

        async def status():
            ready.set()

        rig.client._notify_status = status
        supervisor = asyncio.create_task(rig.client._socket_supervisor())
        try:
            await asyncio.wait_for(ready.wait(), timeout=1)
            if response_kind == "complete":
                result = await rig.manager.async_read()
                assert result["schedules"] == [] and result["status"] == "read_only"
                assert rig.client.reported["sh"] == {}
                assert result["snapshot_counts"] == {"rest": 0, "websocket_authorization": 1}
            else:
                with pytest.raises(ScheduleError, match="No new complete"):
                    await rig.manager.async_read()
            assert len(socket.frames) == 1
            assert rig.client.control_command_count == 0
            assert rig.manager.pending is None and not rig.manager._plans
            rig.client.async_get_shadow.assert_not_awaited()
        finally:
            rig.client._stopping = True
            supervisor.cancel()
            await asyncio.gather(supervisor, return_exceptions=True)

    asyncio.run(run())
