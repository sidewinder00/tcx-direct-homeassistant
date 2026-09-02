"""Regression probes for reported types, request provenance, and recovery."""

from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiohttp
import pytest
from test_schedules import Rig, entry

from custom_components.tcx_direct import api, protocol_helpers, schedules
from custom_components.tcx_direct.schedules import ScheduleError, schedule_revision


@pytest.mark.parametrize("mode", [1, 1.0, "1", "1.0"])
def test_integral_reported_auto_modes_are_accepted(mode):
    async def run():
        rig = Rig()
        rig.remote["systemMode"] = mode
        plan = await rig.create()
        # Recheck normalization after the journal save too.
        rig.mode_on_save = mode
        assert (await rig.manager.async_apply(plan["plan_id"]))["result"] == "confirmed"

    asyncio.run(run())


@pytest.mark.parametrize("mode", [True, False, "Service", "3", 1.5, "1.5", "nan", "inf"])
def test_bad_reported_modes_never_become_auto(mode):
    async def run():
        rig = Rig()
        plan = await rig.create()
        rig.mode_on_save = mode
        with pytest.raises(ScheduleError, match="mode changed"):
            await rig.manager.async_apply(plan["plan_id"])
        assert not rig.messages

    asyncio.run(run())


@pytest.mark.parametrize("duplicate", [None, "pool", "filt0"])
def test_shared_normalized_discovery_preserves_uniqueness_and_first_match(duplicate):
    async def run():
        rig = Rig()
        rig.remote["pool"].update(et="v-pos", app="pool_m")
        rig.remote["filt0"].update(et="F-CTRL", app="filt")
        if duplicate:
            rig.remote["duplicate"] = deepcopy(rig.remote[duplicate])
        assert api._find_pool_mode(rig.remote)[0] == "pool"
        assert api._find_filter_controller(rig.remote)[0] == "filt0"
        assert api._find_pool_modes is protocol_helpers._find_pool_modes
        assert api._find_filter_controllers is protocol_helpers._find_filter_controllers
        if duplicate:
            with pytest.raises(ScheduleError, match="unique"):
                await rig.create()
            assert not rig.messages
        else:
            plan = await rig.create()
            await rig.manager.async_apply(plan["plan_id"])

    asyncio.run(run())


@pytest.mark.parametrize("limits_at", ["filter", "motor", "split", "motor_preferred"])
def test_reported_limit_coercion_fallback_and_motor_precedence(limits_at):
    async def run():
        rig = Rig()
        motor, filt = rig.remote["ecm0"], rig.remote["filt0"]
        filt.update(minSpd="600", maxSpd="3450")
        if limits_at == "motor":
            motor.update(minSpd=filt.pop("minSpd"), maxSpd=filt.pop("maxSpd"))
        elif limits_at == "split":
            motor["minSpd"] = filt.pop("minSpd")
        elif limits_at == "motor_preferred":
            motor.update(minSpd="600", maxSpd="3450")
            filt.update(minSpd=2800, maxSpd=2900)
        plan = await rig.create()
        await rig.manager.async_apply(plan["plan_id"])
        assert api._pump_speed_limits is protocol_helpers._pump_speed_limits

    asyncio.run(run())


@pytest.mark.parametrize(
    "minimum,maximum", [(True, 3450), ("nan", 3450), (600, "inf"), (3000, 2000)]
)
def test_invalid_reported_limits_fail_closed(minimum, maximum):
    async def run():
        rig = Rig()
        rig.remote["filt0"].update(minSpd=minimum, maxSpd=maximum)
        with pytest.raises(ScheduleError, match="limits"):
            await rig.create()
        assert not rig.messages

    asyncio.run(run())


@pytest.mark.parametrize("concurrent_source", ["rest", "websocket_authorization"])
def test_other_snapshot_cannot_certify_rest_response_missing_sh(concurrent_source):
    async def run():
        rig = Rig()

        async def wrong_response():
            rig.manager.observe(rig.remote, full=True, source=concurrent_source, websocket=rig)
            return {"state": {"reported": {"systemMode": 1}}}

        rig.client.async_get_shadow = wrong_response
        with pytest.raises(ScheduleError, match="complete schedule table"):
            await rig.manager.async_read(source="rest")
        assert not rig.messages

    asyncio.run(run())


def test_empty_table_is_valid_but_missing_table_is_not(monkeypatch):
    monkeypatch.setattr(schedules, "WS_SNAPSHOT_TIMEOUT", 0.01)

    async def run():
        rig = Rig({})
        plan = await rig.create()
        await rig.manager.async_apply(plan["plan_id"])
        assert rig.remote["sh"]["1"] == entry()
        rig = Rig()
        rig.remote.pop("sh")
        with pytest.raises(ScheduleError, match="No new complete"):
            await rig.create()

    asyncio.run(run())


def test_rest_snapshot_changed_during_notification_is_rejected():
    async def run():
        rig = Rig()

        async def notify(source):
            rig.client.reported["sh"]["1"] = entry()

        rig.client._notify_state = notify
        with pytest.raises(ScheduleError, match="changed while reading"):
            await rig.manager.async_read(source="rest")
        assert not rig.messages

    asyncio.run(run())


def recovery_rig():
    pending = {"plan_id": "test-pending", "operation": "create", "state": "outcome_uncertain"}
    rig = Rig({"1": entry()}, disk={"pending": pending})
    rig.manager.writes_enabled = False

    async def unsupported():
        raise api.TCXShadowUnsupported("REST unavailable")

    rig.client.async_get_shadow = unsupported

    async def subscribe(frame):
        assert frame["action"] == "subscribe" and frame["service"] == "Authorization"
        rig.messages.append(deepcopy(frame))
        rig.manager.observe(rig.remote, full=True, source="websocket_authorization", websocket=rig)

    rig.send_json = subscribe
    return rig


def test_ws_read_and_ack_each_request_new_snapshot_and_persist_provenance():
    async def run():
        rig = recovery_rig()
        with pytest.raises(api.TCXShadowUnsupported):
            await rig.manager.async_read(source="rest")
        result = await rig.manager.async_read(source="websocket_authorization")
        assert rig.disk["pending"] is not None
        assert result["snapshot_source"] == "websocket_authorization"
        assert result["revision"] == schedule_revision(rig.remote["sh"])
        await rig.manager.async_acknowledge(
            "test-pending", result["revision"], source="websocket_authorization"
        )
        assert len(rig.messages) == 2
        assert all(frame["action"] == "subscribe" for frame in rig.messages)
        assert rig.disk["pending"] is None
        audit = rig.disk["last_acknowledgement"]
        assert audit["source"] == "websocket_authorization"
        assert audit["revision"] == result["revision"]
        assert audit["plan_id"] == "test-pending" and audit["at"]
        restarted = Rig(disk=deepcopy(rig.disk))
        assert restarted.manager.snapshot()["last_acknowledgement"] == audit
        plan = await restarted.create()
        await restarted.manager.async_apply(plan["plan_id"])
        assert restarted.disk["last_acknowledgement"] == audit
        rig.manager.writes_enabled = True
        plan = await rig.manager.async_preview(
            "create", start="12:00", end="12:15", weekday_codes=[5], rpm=2700
        )
        assert plan["snapshot_source"] == "websocket_authorization"
        with pytest.raises(api.TCXShadowUnsupported):
            await rig.manager.async_read(source="rest")

    asyncio.run(run())


@pytest.mark.parametrize("kind", ["cached", "delta", "rest", "missing", "null", "wrong_socket"])
def test_ws_recovery_rejects_unverified_snapshots(kind, monkeypatch):
    monkeypatch.setattr(schedules, "WS_SNAPSHOT_TIMEOUT", 0.001)

    async def run():
        rig = recovery_rig()
        # A complete snapshot received BEFORE the request is not enough.
        rig.manager.observe(rig.remote, full=True, source="websocket_authorization", websocket=rig)

        async def subscribe(frame):
            rig.messages.append(frame)
            if kind == "cached":
                return
            reported = {"systemMode": 1} if kind == "missing" else rig.remote
            if kind == "null":
                reported = {"sh": None}
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

        rig.send_json = subscribe
        with pytest.raises(ScheduleError, match="No new complete"):
            await rig.manager.async_acknowledge(
                "test-pending",
                schedule_revision(rig.remote["sh"]),
                source="websocket_authorization",
            )
        assert rig.disk["pending"] is not None
        assert rig.manager._snapshot_request is None

    asyncio.run(run())


@pytest.mark.parametrize(
    "kind", ["closed", "replaced", "changed", "cancel", "send_error", "save_error"]
)
def test_ws_recovery_interruptions_keep_latch(kind):
    async def run():
        rig = recovery_rig()
        baseline = deepcopy(rig.disk)
        send = rig.send_json

        async def subscribe(frame):
            if kind == "cancel":
                raise asyncio.CancelledError
            if kind == "send_error":
                raise ConnectionError("disconnected")
            await send(frame)
            if kind == "closed":
                rig.closed = True
            elif kind == "replaced":
                rig.client._ws = SimpleNamespace(closed=False)
            elif kind == "changed":
                rig.client.reported["sh"]["1"]["ar"] = 2700
            elif kind == "save_error":
                rig.fail_save = True

        rig.send_json = subscribe
        expected = (
            asyncio.CancelledError
            if kind == "cancel"
            else OSError
            if kind == "save_error"
            else ScheduleError
        )
        with pytest.raises(expected):
            await rig.manager.async_acknowledge(
                "test-pending",
                schedule_revision(rig.remote["sh"]),
                source="websocket_authorization",
            )
        assert rig.disk == baseline
        assert rig.manager.pending is not None
        assert rig.manager._snapshot_request is None

    asyncio.run(run())


def test_ws_ack_checks_review_revision_and_pending_id():
    async def run():
        rig = recovery_rig()
        result = await rig.manager.async_read(source="websocket_authorization")
        rig.remote["sh"]["1"]["ar"] = 2700
        with pytest.raises(ScheduleError, match="changed since review"):
            await rig.manager.async_acknowledge(
                "test-pending", result["revision"], source="websocket_authorization"
            )
        with pytest.raises(ScheduleError, match="No matching"):
            await rig.manager.async_acknowledge(
                "another-plan", result["revision"], source="websocket_authorization"
            )
        assert len(rig.messages) == 2 and rig.disk["pending"] is not None
        rig.manager.pending = None
        result = await rig.manager.async_read(source="websocket_authorization")
        assert result["status"] == "read_only"
        assert len(rig.messages) == 3

    asyncio.run(run())


@pytest.mark.parametrize("replaced", [False, True])
def test_ws_connection_change_during_notification_keeps_latch(replaced):
    async def run():
        rig = recovery_rig()
        baseline = deepcopy(rig.disk)

        async def notify(source):
            if replaced:
                rig.client._ws = SimpleNamespace(closed=False)
            else:
                rig.closed = True

        rig.client._notify_state = notify
        with pytest.raises(ScheduleError):
            await rig.manager.async_acknowledge(
                "test-pending",
                schedule_revision(rig.remote["sh"]),
                source="websocket_authorization",
            )
        assert rig.disk == baseline and rig.manager.pending is not None

    asyncio.run(run())


def test_controller_extra_fields_keep_exact_confirmation_and_recovery(monkeypatch):
    monkeypatch.setattr(schedules, "SCHEDULE_TIMEOUT", 0.001)

    async def run():
        rig = Rig()
        plan = await rig.create()
        send = rig.send_json

        async def extra_field(frame):
            if frame.get("action") == "subscribe":
                return await send(frame)
            frame = deepcopy(frame)
            frame["payload"]["state"]["desired"]["sh"]["add"]["vendor_extra"] = 1
            await send(frame)

        rig.send_json = extra_field
        with pytest.raises(api.TCXConnectionError):
            await rig.manager.async_apply(plan["plan_id"])
        assert rig.remote["sh"]["1"]["vendor_extra"] == 1
        assert rig.manager.pending is not None
        readback = await rig.manager.async_read(source="rest")
        await rig.manager.async_acknowledge(plan["plan_id"], readback["revision"], source="rest")
        assert len(rig.messages) == 1
        assert rig.disk["last_acknowledgement"]["source"] == "rest"

    asyncio.run(run())


@pytest.mark.parametrize("service", ["Authorization", "StateReported"])
def test_real_socket_handler_only_authorization_resolves_recovery(service, monkeypatch):
    monkeypatch.setattr(schedules, "WS_SNAPSHOT_TIMEOUT", 0.01)

    async def run():
        rig = recovery_rig()
        ready = asyncio.Event()

        class Socket:
            closed = False

            def __init__(self):
                self.queue = asyncio.Queue()

            def __aiter__(self):
                return self

            async def __anext__(self):
                return await self.queue.get()

            async def send_json(self, frame):
                assert frame["action"] == "subscribe"
                self.queue.put_nowait(
                    SimpleNamespace(
                        type=aiohttp.WSMsgType.TEXT,
                        data=json.dumps(
                            {"service": service, "payload": {"state": {"reported": rig.remote}}}
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
            if service == "Authorization":
                result = await rig.manager.async_read(source="websocket_authorization")
                assert result["snapshot_counts"]["websocket_authorization"] == 1
            else:
                with pytest.raises(ScheduleError, match="No new complete"):
                    await rig.manager.async_read(source="websocket_authorization")
                assert rig.manager._authorization_sequence == 0
            assert rig.client.ws_reported_messages_received == 1
            assert rig.manager.pending is not None
        finally:
            rig.client._stopping = True
            supervisor.cancel()
            await asyncio.gather(supervisor, return_exceptions=True)

    asyncio.run(run())
