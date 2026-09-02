"""Synthetic diagnostic evidence only; no private captures or live equipment."""

from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiohttp
import pytest
from test_schedules import Rig, entry

from custom_components.tcx_direct import api
from custom_components.tcx_direct.redaction import REDACTED
from custom_components.tcx_direct.schedule_trace import (
    FRAGMENT_BYTES,
    TRACE_CHANGES,
    TRACE_EVENTS,
    NativeScheduleTrace,
    _fragment,
)


def authorization(main=None, sched=None):
    return {
        "service": "Authorization",
        "namespace": "authorization",
        "payload": {"main": main or {}, "sched": sched or {}},
    }


def test_separate_raw_namespaces_missing_null_empty_and_metadata():
    trace = NativeScheduleTrace()
    packet = authorization(
        {
            "state": {"desired": {"sh": {"add": entry()}}},
            "metadata": {"desired": {"sh": {"add": {"ar": {"timestamp": 123}}}}},
        },
        {"state": {"desired": {"sh": {"add": None}}, "reported": {"sh": {}}}},
    )
    original = deepcopy(packet)
    trace.received(packet, source="websocket", connection=1)
    snapshot = trace.snapshot()
    main = snapshot["events"][0]["documents"]["payload.main"]
    sched = snapshot["events"][0]["documents"]["payload.sched"]
    assert main["desired_sh"]["value"]["add"]["ar"] == 2650
    assert main["reported_sh"] == {"presence": "missing"}
    assert main["desired_sh_metadata"]["value"]["add"]["ar"]["timestamp"] == 123
    assert sched["desired_sh"]["value"] == {"add": None}
    assert sched["reported_sh"] == {"presence": "object", "value": {}, "truncated": False}
    assert snapshot["events"][0]["documents"]["root.sched"]["presence"] == "missing"
    assert packet == original  # redaction cannot mutate the actual receiver data
    packet["payload"]["main"]["state"]["desired"]["sh"]["add"]["ar"] = 1000
    assert main["desired_sh"]["value"]["add"]["ar"] == 2650
    main["desired_sh"]["value"]["add"]["ar"] = -1
    assert (
        trace.snapshot()["events"][0]["documents"]["payload.main"]["desired_sh"]["value"]["add"][
            "ar"
        ]
        == 2650
    )


@pytest.mark.parametrize(
    "value, presence", [(None, "null"), ({}, "object"), ([], "list"), (False, "scalar")]
)
def test_explicit_sh_value_is_not_conflated_with_absence(value, presence):
    trace = NativeScheduleTrace()
    trace.received({"state": {"reported": {"sh": value}}}, source="rest")
    doc = trace.snapshot()["events"][0]["documents"]["root"]
    assert doc["reported_sh"]["presence"] == presence
    assert doc["reported_sh"]["value"] == value
    assert doc["desired_sh"]["presence"] == "missing"


def test_metadata_only_does_not_claim_active_desired_command():
    trace = NativeScheduleTrace()
    trace.received(
        authorization({"metadata": {"desired": {"sh": {"add": {"ar": {"timestamp": 1}}}}}}),
        source="websocket",
    )
    main = trace.snapshot()["events"][0]["documents"]["payload.main"]
    assert main["desired_sh"]["presence"] == "missing"
    assert main["desired_sh_metadata"]["presence"] == "object"


def test_unnamespaced_stream_is_not_mislabeled_as_main_or_sched():
    trace = NativeScheduleTrace()
    trace.received(
        {
            "service": "StateStreamer",
            "payload": {"state": {"desired": {"sh": {"add": None}}}},
        },
        source="websocket",
        connection=2,
    )
    event = trace.snapshot()["events"][0]
    assert event["namespace"] == "other_or_absent"
    assert event["connection"] == 2
    assert event["documents"]["payload"]["desired_sh"]["value"] == {"add": None}
    assert event["documents"]["payload.main"]["presence"] == "missing"
    assert event["documents"]["payload.sched"]["presence"] == "missing"


def test_redaction_at_retention_excludes_envelopes_labels_and_dynamic_identifiers():
    trace = NativeScheduleTrace()
    packet = authorization(
        sched={
            "state": {
                "reported": {
                    "sh": {
                        "1": entry(
                            id="private-label",
                            token="secret-token",
                            device_id="secret-device",
                            extra={"email": "someone@example.test", "password": "secret-password"},
                        ),
                        "abcd1234abcd1234": {"secret": "hidden-child"},
                    }
                }
            },
            "clientToken": "secret-client-token",
        }
    )
    packet["target"] = "secret-target"
    packet["payload"]["unrelated"] = {"password": "not-a-schedule"}
    trace.received(packet, source="websocket")
    event = trace._events[0]  # secrets must never enter retained history, not just export
    text = json.dumps(event)
    for secret in (
        "private-label",
        "secret-token",
        "secret-device",
        "someone@example.test",
        "secret-password",
        "abcd1234abcd1234",
        "hidden-child",
        "secret-client-token",
        "secret-target",
        "not-a-schedule",
    ):
        assert secret not in text
    value = event["documents"]["payload.sched"]["reported_sh"]["value"]
    assert value["1"]["id"] == REDACTED
    assert value["1"]["ar"] == 2650
    assert event["documents"]["payload.sched"]["client_token_present"] is True


@pytest.mark.parametrize(
    "value",
    [
        {str(i): entry() for i in range(200)},
        {"1": {str(i): "x" * 150 for i in range(30)}},
        {"1": {"extra": [[[[[[[[[[1]]]]]]]]]]}},
        {"1": {"extra": "x" * 10000}},
        {"1": {"extra": float("inf")}},
        {"1": {"extra": 1 << 10000}},
    ],
)
def test_large_and_malformed_fragments_are_explicitly_bounded(value):
    result = _fragment(value)
    assert result["truncated"] is True
    assert len(json.dumps(result["value"]).encode()) <= FRAGMENT_BYTES


def test_cyclic_or_unsupported_input_cannot_escape_to_control_code():
    class Broken(dict):
        def items(self):
            raise ValueError("must-not-be-logged")

    trace = NativeScheduleTrace()
    trace.received({"state": {"reported": {"sh": Broken()}}}, source="rest")
    assert trace.capture_errors == 1
    assert "must-not-be-logged" not in json.dumps(trace.snapshot())
    cyclic = {}
    cyclic["loop"] = cyclic
    assert _fragment(cyclic)["truncated"] is True
    trace.received([], source="rest")
    assert trace.snapshot()["last_rest_response"]["documents"]["root"]["presence"] == "invalid"


def test_no_diff_inferred_across_truncated_table_and_removal_is_distinct_from_null():
    trace = NativeScheduleTrace()
    trace.reported_table({"1": entry()}, source="rest", full=True)
    trace.reported_table({str(i): entry() for i in range(200)}, source="rest", full=True)
    trace.reported_table({"2": entry()}, source="rest", full=True)
    assert trace.snapshot()["reported_changes"][-1]["kind"] == "reported_table_baseline"
    trace.reported_table({"2": None}, source="rest", full=True)
    assert trace.snapshot()["reported_changes"][-1]["changed"] == ["2"]
    trace.reported_table({}, source="rest", full=True)
    assert trace.snapshot()["reported_changes"][-1]["removed"] == ["2"]


def test_ring_limits_and_important_summaries_survive_routine_reads():
    trace = NativeScheduleTrace()
    trace.sending(
        api.build_set_state_message(
            "private-target", "private-user", "tcx", {"sh": {"add": entry()}}
        ),
        connection=1,
        command_number=1,
    )
    trace.operation("confirmed", "local-plan", "create")
    trace.received(
        authorization(sched={"state": {"reported": {"sh": {"1": entry()}}}}),
        source="websocket",
        connection=1,
    )
    for index in range(100):
        trace.received({"state": {"reported": {"pool": {"st": 1}}}}, source="rest")
        trace.reported_table({"1": entry(ar=1000 + index)}, source="rest", full=True)
    snapshot = trace.snapshot()
    assert len(snapshot["events"]) == TRACE_EVENTS
    assert len(snapshot["reported_changes"]) == TRACE_CHANGES
    assert snapshot["last_schedule_send_attempt"]["command_number"] == 1
    assert snapshot["last_confirmed_operation"]["plan_id"] == "local-plan"
    assert snapshot["last_authorization_snapshot"]["connection"] == 1
    assert (
        snapshot["last_rest_response"]["documents"]["root"]["reported_sh"]["presence"] == "missing"
    )
    assert "private-target" not in json.dumps(snapshot)
    assert "private-user" not in json.dumps(snapshot)
    assert not NativeScheduleTrace().snapshot()["events"]  # no persistence across clients


def test_unrelated_stream_does_not_fill_history_or_transmit_anything():
    trace = NativeScheduleTrace()
    for _ in range(100):
        trace.received(
            {
                "service": "StateStreamer",
                "payload": {"state": {"reported": {"ecm0": {"rpm": 2000}}}},
            },
            source="websocket",
        )
    assert trace.snapshot()["total_events"] == 0


def test_late_duplicates_are_visible_without_replaying_or_changing_the_latch():
    async def run():
        rig = Rig({})
        plan = await rig.create()
        result = await rig.manager.async_apply(plan["plan_id"])
        assert result["result"] == "confirmed"
        disk = deepcopy(rig.disk)
        for slot in ("2", "3"):
            patch = {"sh": {slot: entry()}}
            api._deep_merge(rig.client.reported, patch)
            rig.manager.observe(patch)
        trace = rig.client.schedule_trace.snapshot()
        late = trace["reported_changes"][-2:]
        assert [event["added"] for event in late] == [["2"], ["3"]]
        assert all(
            event["last_confirmed_operation"]["plan_id"] == plan["plan_id"] for event in late
        )
        assert all(
            event["sequence"] > event["last_confirmed_operation"]["sequence"] for event in late
        )
        assert rig.client.control_command_count == 1 and len(rig.messages) == 1
        assert rig.disk == disk and rig.manager.pending is None
        assert not rig.manager._plans
        assert "native_schedule_trace" not in rig.manager.snapshot()
        sends = [e for e in trace["events"] if e["kind"] == "schedule_send_attempt"]
        assert len(sends) == 1 and sends[0]["desired_sh"]["value"]["add"]["ar"] == 2650

    asyncio.run(run())


def test_capture_failure_does_not_prevent_the_existing_control_send(monkeypatch):
    import custom_components.tcx_direct.schedule_trace as module

    async def run():
        rig = Rig({})
        plan = await rig.create()

        def broken(value):
            raise ValueError("synthetic diagnostic failure")

        monkeypatch.setattr(module, "_fragment", broken)
        assert (await rig.manager.async_apply(plan["plan_id"]))["result"] == "confirmed"
        assert len(rig.messages) == 1
        assert rig.client.schedule_trace.capture_errors > 0

    asyncio.run(run())


def test_real_rest_hook_records_unmerged_namespaces_without_extra_requests():
    async def run():
        rig = Rig({})
        packet = {
            "main": {"state": {"desired": {"sh": {"add": entry()}}}},
            "sched": {"state": {"reported": {"sh": {"1": entry()}}}},
        }

        class Response:
            status = 200

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return None

            async def json(self, **kwargs):
                return deepcopy(packet)

        requests = []

        class Session:
            def get(self, *args, **kwargs):
                requests.append(1)
                return Response()

        rig.client._session = Session()
        rig.client.async_ensure_auth = AsyncMock()
        rig.client.id_token = "synthetic"
        response = await api.TCXClient.async_get_shadow(rig.client)
        assert response == packet and len(requests) == 1
        assert not rig.messages and not rig.subscriptions
        event = rig.client.schedule_trace.snapshot()["last_rest_response"]
        assert event["documents"]["root.main"]["desired_sh"]["value"]["add"]["ar"] == 2650
        assert event["documents"]["root.sched"]["reported_sh"]["value"]["1"]["en"] == 0
        assert rig.client.reported["sh"] == {"1": entry()}

    asyncio.run(run())


def test_real_socket_records_raw_authorization_and_each_late_report_with_writes_off():
    async def run():
        rig = Rig({})
        rig.manager.writes_enabled = False
        ready = asyncio.Event()

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
                self.frames.append(deepcopy(frame))
                self.inject(
                    authorization(
                        {"state": {"desired": {"sh": {"add": entry()}}}},
                        {
                            "state": {
                                "desired": {"sh": {"add": None}},
                                "reported": {"sh": {"1": entry()}},
                            }
                        },
                    )
                )

            def inject(self, data):
                self.queue.put_nowait(
                    SimpleNamespace(type=aiohttp.WSMsgType.TEXT, data=json.dumps(data))
                )

            async def close(self):
                self.closed = True

        socket = Socket()

        async def open_socket():
            await rig.client._send_authorization_subscribe(socket)
            return socket

        rig.client._open_websocket = open_socket
        rig.client._bootstrap_resubscribe = AsyncMock()

        async def state(*args):
            ready.set()

        rig.client._state_callback = state
        supervisor = asyncio.create_task(rig.client._socket_supervisor())
        try:
            await asyncio.wait_for(ready.wait(), 1)
            for slot in ("2", "3"):
                ready.clear()
                socket.inject(
                    {
                        "service": "StateStreamer",
                        "payload": {
                            "state": {
                                "reported": {"sh": {slot: entry()}},
                                "desired": {"sh": {"add": None}},
                            }
                        },
                    }
                )
                await asyncio.wait_for(ready.wait(), 1)
            snapshot = rig.client.schedule_trace.snapshot()
            incoming = [e for e in snapshot["events"] if e["kind"] == "received"]
            assert len(incoming) == 3 and all(e["connection"] == 1 for e in incoming)
            assert (
                incoming[0]["documents"]["payload.main"]["desired_sh"]["value"]["add"]["ar"] == 2650
            )
            assert incoming[-1]["documents"]["payload"]["reported_sh"]["value"]["3"]["en"] == 0
            assert snapshot["events"][0]["kind"] == "subscription_send_attempt"
            assert snapshot["events"][0]["connection"] == 1
            assert len(socket.frames) == 1 and socket.frames[0]["action"] == "subscribe"
            assert "state" not in socket.frames[0]["payload"]
            assert rig.client.control_command_count == 0
            assert rig.manager.pending is None and not rig.manager.writes_enabled
            assert set(rig.client.reported["sh"]) == {"1", "2", "3"}
        finally:
            rig.client._stopping = True
            supervisor.cancel()
            await asyncio.gather(supervisor, return_exceptions=True)

    asyncio.run(run())
