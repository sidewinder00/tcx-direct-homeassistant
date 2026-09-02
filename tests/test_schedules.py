"""Synthetic protocol tests. No credentials, private captures, or real equipment."""

from __future__ import annotations

import asyncio
from copy import deepcopy

import pytest

from custom_components.tcx_direct import api, schedules
from custom_components.tcx_direct.schedules import ScheduleError, describe_schedules


def entry(**changes):
    return {
        "lc": "pool",
        "id": "Filter Pump",
        "on": "T=11:00",
        "of": "T=11:15",
        "dw": [5],
        "en": 0,
        "ar": 2650,
        **changes,
    }


class Rig:
    def __init__(self, table=None, disk=None):
        self.client = api.TCXClient(object(), "test@example.com", "unused", "test-controller")
        self.client.user_id = "test-user"
        self.client.websocket_connected = True
        self.remote = {
            "systemMode": 1,
            "pool": {"et": "V_POS", "app": "POOL_M", "st": 0},
            "filt0": {"et": "F_CTRL", "app": "FILT", "minSpd": 600, "maxSpd": 3450, "manSpd": 2600},
            "ecm0": {"st": 0, "spdList": [{"app": "BD1_F", "speed": 2600}]},
            "sh": deepcopy(table if table is not None else {"1": None, "2": None}),
        }
        self.client.reported = deepcopy(self.remote)
        self.disk = disk if disk is not None else {"pending": None}
        self.messages = []
        self.receive = True
        self.apply_remote = True
        self.mode_on_save = None
        self.fail_save = False
        self.cancel_save = False
        self.closed = False
        self.client._ws = self
        self.client.async_get_shadow = self.refresh
        self.manager = self.client.schedules
        self.manager.configure_storage(self.disk, self.save)
        self.manager.writes_enabled = True

    async def save(self, data):
        if self.fail_save:
            raise OSError("disk failure")
        self.disk.clear()
        self.disk.update(deepcopy(data))
        if self.mode_on_save is not None:
            self.client.reported["systemMode"] = self.mode_on_save
        if self.cancel_save:
            raise asyncio.CancelledError

    async def refresh(self):
        self.client.reported = deepcopy(self.remote)
        self.manager.observe(self.remote, full=True, source="rest")
        return {"state": {"reported": deepcopy(self.remote)}}

    async def send_json(self, frame):
        assert self.disk["pending"] is not None  # durable BEFORE transmission
        self.messages.append(deepcopy(frame))
        desired = frame["payload"]["state"]["desired"]
        assert set(desired) == {"sh"}
        key, value = next(iter(desired["sh"].items()))
        if key == "add":
            key = next((str(i) for i in range(1, 100) if self.remote["sh"].get(str(i)) is None))
        patch = {"sh": {key: None if value == "[DELETED]" else deepcopy(value)}}
        if self.apply_remote:
            api._deep_merge(self.remote, patch)
        if self.receive:
            api._deep_merge(self.client.reported, patch)
            self.manager.observe(patch)
            self.client._resolve_pending_control()

    async def create(self, **changes):
        return await self.manager.async_preview(
            "create", start="11:00", end="11:15", weekday_codes=[5], rpm=2650, **changes
        )


@pytest.fixture(autouse=True)
def quick_timeouts(monkeypatch):
    monkeypatch.setattr(schedules, "SCHEDULE_TIMEOUT", 0.01)


def test_captured_create_update_disable_delete_sequence():
    async def run():
        rig = Rig()
        baseline = deepcopy(rig.remote)
        plan = await rig.create()
        assert not rig.messages
        assert plan["after"] == entry()
        assert plan["desired"] == {"sh": {"add": entry()}}
        response = await rig.manager.async_apply(plan["plan_id"])
        assert response["schedule_id"] == "1"
        assert rig.disk == {"pending": None}
        assert rig.remote["sh"]["1"] == entry()

        for operation, changes, expected in [
            ("enable", {}, entry(en=1)),
            ("update", {"rpm": 2700}, entry(en=1, ar=2700)),
            ("disable", {}, entry(ar=2700)),
            ("delete", {}, None),
        ]:
            plan = await rig.manager.async_preview(operation, "1", **changes)
            await rig.manager.async_apply(plan["plan_id"])
            assert rig.remote["sh"]["1"] == expected
        assert rig.messages[-1]["payload"]["state"]["desired"] == {"sh": {"1": "[DELETED]"}}
        assert rig.remote == baseline
        assert rig.manager.snapshot()["pending_write"] is None

    asyncio.run(run())


def test_preserve_unknown_fields_unrelated_schedule_and_dynamic_pool_key():
    async def run():
        rig = Rig(
            {
                "7": entry(lc="pool3", vendor_extra={"a": 1}),
                "8": {"lc": "aux9", "unknown": "preserve"},
            }
        )
        rig.remote["pool3"] = rig.remote.pop("pool")
        rig.remote["filt4"] = rig.remote.pop("filt0")
        plan = await rig.manager.async_preview("update", "7", rpm=2800)
        await rig.manager.async_apply(plan["plan_id"])
        assert rig.remote["sh"]["7"]["vendor_extra"] == {"a": 1}
        assert rig.remote["sh"]["8"] == {"lc": "aux9", "unknown": "preserve"}
        with pytest.raises(ScheduleError, match="Pool Filtration"):
            await rig.manager.async_preview("delete", "8")

    asyncio.run(run())


@pytest.mark.parametrize("rpm", [0, -1, 599, 3451, True, 2650.2, "2650", float("nan")])
def test_reject_invalid_or_default_rpm(rpm):
    async def run():
        rig = Rig()
        with pytest.raises(ScheduleError, match="RPM"):
            await rig.manager.async_preview(
                "create", start="11:00", end="11:15", weekday_codes=[5], rpm=rpm
            )
        assert not rig.messages

    asyncio.run(run())


@pytest.mark.parametrize(
    "changes",
    [
        {"start": "24:00"},
        {"start": "9:00"},
        {"start": "11:00:00"},
        {"end": "11:00"},
        {"weekday_codes": []},
        {"weekday_codes": [5, 5]},
        {"weekday_codes": [7]},
        {"weekday_codes": [True]},
        {"enabled": 1},
        {"unexpected": 1},
    ],
)
def test_reject_invalid_schedule_fields(changes):
    async def run():
        rig = Rig({"1": entry()})
        with pytest.raises(ScheduleError):
            await rig.manager.async_preview("update", "1", **changes)
        assert not rig.messages

    asyncio.run(run())


def test_default_speed_is_readable_and_can_be_disabled_or_deleted_not_enabled():
    async def run():
        rig = Rig({"1": entry(ar=0, en=1)})
        item = (await rig.manager.async_read())["schedules"][0]
        assert item["rpm"] is None
        assert item["speed_mode"] == "unconfirmed_default"
        for operation in ("enable", "update"):
            with pytest.raises(ScheduleError, match="explicit"):
                await rig.manager.async_preview(operation, "1")
        plan = await rig.manager.async_preview("disable", "1")
        await rig.manager.async_apply(plan["plan_id"])
        assert rig.remote["sh"]["1"]["ar"] == 0
        plan = await rig.manager.async_preview("delete", "1")
        await rig.manager.async_apply(plan["plan_id"])

    asyncio.run(run())


def test_adjacent_windows_allowed_overlap_and_enabled_overnight_blocked():
    async def run():
        rig = Rig({"1": entry(en=1)})
        await rig.manager.async_preview(
            "create", start="11:15", end="11:30", weekday_codes=[5], rpm=2700, enabled=True
        )
        with pytest.raises(ScheduleError, match="overlap"):
            await rig.manager.async_preview(
                "create", start="11:14", end="11:30", weekday_codes=[5], rpm=2700, enabled=True
            )
        await rig.manager.async_preview(
            "create", start="11:00", end="11:15", weekday_codes=[4], rpm=2700, enabled=True
        )
        plan = await rig.manager.async_preview(
            "create", start="23:00", end="00:00", weekday_codes=[5], rpm=2700
        )
        await rig.manager.async_apply(plan["plan_id"])
        with pytest.raises(ScheduleError, match="Overnight"):
            await rig.manager.async_preview("enable", "2")

    asyncio.run(run())


def test_duplicate_create_and_reused_plan_never_send_another_add():
    async def run():
        rig = Rig()
        plan = await rig.create()
        await rig.manager.async_apply(plan["plan_id"])
        with pytest.raises(ScheduleError, match="already used"):
            await rig.manager.async_apply(plan["plan_id"])
        with pytest.raises(ScheduleError, match="identical"):
            await rig.create()
        assert len(rig.messages) == 1

    asyncio.run(run())


def test_preview_expires_and_is_bound_to_entry(monkeypatch):
    async def run():
        rig = Rig()
        plan = await rig.create()
        with pytest.raises(ScheduleError):
            await Rig().manager.async_apply(plan["plan_id"])
        monkeypatch.setattr(schedules, "PLAN_TTL", -1)
        with pytest.raises(ScheduleError, match="expired"):
            await rig.manager.async_apply(plan["plan_id"])
        assert not rig.messages

    asyncio.run(run())


@pytest.mark.parametrize("mode", [None, 0, 2, 3, 4, 5, 99, True])
def test_unknown_and_non_auto_modes_block_writes(mode):
    async def run():
        rig = Rig()
        plan = await rig.create()
        rig.client.reported["systemMode"] = mode
        with pytest.raises(ScheduleError, match="Auto"):
            await rig.manager.async_apply(plan["plan_id"])
        assert not rig.messages

    asyncio.run(run())


def test_opt_in_and_durable_storage_required():
    async def run():
        rig = Rig()
        plan = await rig.create()
        rig.manager.writes_enabled = False
        with pytest.raises(ScheduleError, match="disabled"):
            await rig.manager.async_apply(plan["plan_id"])
        rig.manager.writes_enabled = True
        rig.manager._save = None
        with pytest.raises(ScheduleError, match="journal"):
            await rig.manager.async_apply(plan["plan_id"])
        assert not rig.messages

    asyncio.run(run())


def test_concurrent_app_edit_before_apply_rejects_without_rollback():
    async def run():
        rig = Rig({"1": entry()})
        plan = await rig.manager.async_preview("update", "1", rpm=2700)
        rig.remote["sh"]["1"]["ar"] = 2800
        with pytest.raises(ScheduleError, match="changed since preview"):
            await rig.manager.async_apply(plan["plan_id"])
        assert not rig.messages
        assert rig.remote["sh"]["1"]["ar"] == 2800

    asyncio.run(run())


def test_mode_change_during_save_does_not_send():
    async def run():
        rig = Rig()
        plan = await rig.create()
        rig.mode_on_save = 3
        with pytest.raises(ScheduleError, match="mode changed"):
            await rig.manager.async_apply(plan["plan_id"])
        assert not rig.messages

    asyncio.run(run())


@pytest.mark.parametrize("applied", [True, False])
def test_timeout_restart_and_manual_acknowledgement_never_replay(applied):
    async def run():
        rig = Rig()
        plan = await rig.create()
        rig.receive = False
        rig.apply_remote = applied
        with pytest.raises(api.TCXConnectionError):
            await rig.manager.async_apply(plan["plan_id"])
        assert rig.disk["pending"]["plan_id"] == plan["plan_id"]
        restarted = Rig(rig.remote["sh"], disk=rig.disk)
        with pytest.raises(ScheduleError, match="[Rr]eview"):
            await restarted.create()
        with pytest.raises(ScheduleError, match="review"):
            await restarted.manager.async_apply(plan["plan_id"])
        readback = await restarted.manager.async_read()
        assert readback["status"] == "needs_review"
        with pytest.raises(ScheduleError, match="changed since review"):
            await restarted.manager.async_acknowledge(plan["plan_id"], "wrong-revision")
        await restarted.manager.async_acknowledge(plan["plan_id"], readback["revision"])
        assert not restarted.messages
        assert len(rig.messages) == 1
        assert restarted.disk["pending"] is None

    asyncio.run(run())


@pytest.mark.parametrize("cancel", [False, True])
def test_disk_failure_or_cancellation_never_sends(cancel):
    async def run():
        rig = Rig()
        plan = await rig.create()
        rig.fail_save = not cancel
        rig.cancel_save = cancel
        with pytest.raises(asyncio.CancelledError if cancel else OSError):
            await rig.manager.async_apply(plan["plan_id"])
        assert not rig.messages
        assert rig.manager.pending is not None

    asyncio.run(run())


def test_no_stale_cache_or_desired_echo_confirmation():
    async def run():
        rig = Rig()

        async def cached_only():
            return {"state": {"reported": {"ecm0": {"st": 0}}}}

        rig.client.async_get_shadow = cached_only
        with pytest.raises(ScheduleError, match="complete schedule table"):
            await rig.create()
        assert not rig.messages
        plan = schedules.SchedulePlan("x", "update", "1", {}, entry(), 0)
        rig.client.reported["sh"]["1"] = entry()
        rig.manager.observe({"ecm0": {"st": 0}})
        assert rig.manager._matching_slot(plan, 0) is None

    asyncio.run(run())


def test_full_snapshot_removes_stale_members_but_deltas_do_not():
    rig = Rig({"1": entry(), "2": entry(ar=2700)})
    rig.manager.observe(rig.remote, full=True)
    patch = {"sh": {"1": {"en": 1}}}
    api._deep_merge(rig.client.reported, patch)
    rig.manager.observe(patch)
    assert rig.client.reported["sh"]["2"] == entry(ar=2700)
    rig.manager.observe({"sh": {}}, full=True)
    assert rig.client.reported["sh"] == {}
    assert rig.manager.snapshot()["schedules"] == []


def test_multiple_calls_serialized_and_second_preview_becomes_stale():
    async def run():
        rig = Rig()
        first = await rig.create()
        second = await rig.manager.async_preview(
            "create", start="12:00", end="12:15", weekday_codes=[5], rpm=2700
        )
        outcomes = await asyncio.gather(
            rig.manager.async_apply(first["plan_id"]),
            rig.manager.async_apply(second["plan_id"]),
            return_exceptions=True,
        )
        assert outcomes[0]["result"] == "confirmed"
        assert isinstance(outcomes[1], ScheduleError)
        assert len(rig.messages) == 1

    asyncio.run(run())


def test_unknown_read_entries_and_bounded_history():
    rig = Rig({"1": {"en": "future-value", "ar": 0}, "2": "unknown-format"})
    assert len(describe_schedules(rig.remote["sh"])) == 2
    for i in range(25):
        rig.manager._record("test", str(i), "test")
    assert len(rig.manager.snapshot()["recent_operations"]) == 20
    with pytest.raises(ScheduleError, match="journal"):
        rig.manager.configure_storage({"unexpected": "data"}, rig.save)


@pytest.mark.parametrize(
    "pending",
    [
        {},
        {"plan_id": "x"},
        "broken",
        {"plan_id": "", "operation": "create", "state": "outcome_uncertain"},
    ],
)
def test_invalid_pending_journal_is_not_silently_discarded(pending):
    rig = Rig()
    rig.manager._save = None
    with pytest.raises(ScheduleError, match="journal"):
        rig.manager.configure_storage({"pending": pending}, rig.save)
    assert rig.manager._save is None


def test_duplicate_identity_ignores_label_enable_state_and_weekday_order():
    async def run():
        rig = Rig({"1": entry(en=1, dw=[5, 4], id="Existing pump program")})
        with pytest.raises(ScheduleError, match="identical"):
            await rig.manager.async_preview(
                "create", start="11:00", end="11:15", weekday_codes=[4, 5], rpm=2650
            )
        assert not rig.messages

    asyncio.run(run())


@pytest.mark.parametrize("disconnect", [False, True])
def test_disconnect_or_cancellation_after_transmission_keeps_latch(disconnect):
    async def run():
        rig = Rig()
        plan = await rig.create()

        async def interrupted_send(frame):
            rig.messages.append(frame)
            if disconnect:
                raise ConnectionError("connection lost after send")
            raise asyncio.CancelledError

        rig.send_json = interrupted_send
        with pytest.raises(api.TCXConnectionError if disconnect else asyncio.CancelledError):
            await rig.manager.async_apply(plan["plan_id"])
        assert len(rig.messages) == 1
        assert rig.disk["pending"] is not None

    asyncio.run(run())


def test_concurrent_unrelated_edit_after_send_is_not_rolled_back():
    async def run():
        rig = Rig({"1": None, "2": {"lc": "aux0", "en": 1}})
        plan = await rig.create()
        send = rig.send_json

        async def concurrent_edit(frame):
            await send(frame)
            rig.remote["sh"]["2"]["en"] = 0

        rig.send_json = concurrent_edit
        with pytest.raises(ScheduleError, match="Other schedules changed"):
            await rig.manager.async_apply(plan["plan_id"])
        assert len(rig.messages) == 1
        assert rig.remote["sh"]["2"]["en"] == 0
        assert rig.disk["pending"] is not None

    asyncio.run(run())


def test_target_readback_mismatch_blocks_further_writes():
    async def run():
        rig = Rig()
        plan = await rig.create()
        send = rig.send_json

        async def inconsistent_readback(frame):
            await send(frame)
            rig.remote["sh"]["1"]["ar"] = 2750

        rig.send_json = inconsistent_readback
        with pytest.raises(ScheduleError, match="readback"):
            await rig.manager.async_apply(plan["plan_id"])
        assert len(rig.messages) == 1
        assert rig.disk["pending"] is not None

    asyncio.run(run())


def test_schedule_changed_during_disk_save_is_not_written():
    async def run():
        rig = Rig()
        plan = await rig.create()

        async def changed_during_save(data):
            await rig.save(data)
            rig.client.reported["sh"]["2"] = entry(ar=2800)

        rig.manager._save = changed_during_save
        with pytest.raises(ScheduleError, match="changed while preparing"):
            await rig.manager.async_apply(plan["plan_id"])
        assert not rig.messages

    asyncio.run(run())


def test_changed_pump_limits_and_missing_transport_fail_closed():
    async def run():
        rig = Rig()
        plan = await rig.create()
        rig.remote["filt0"]["maxSpd"] = 2500
        with pytest.raises(ScheduleError, match="limits"):
            await rig.manager.async_apply(plan["plan_id"])
        rig.remote["filt0"]["maxSpd"] = 3450
        plan = await rig.create()
        rig.closed = True
        with pytest.raises(ScheduleError, match="WebSocket"):
            await rig.manager.async_apply(plan["plan_id"])
        assert not rig.messages

    asyncio.run(run())


def test_real_rest_receive_hook_replaces_schedule_membership():
    async def run():
        rig = Rig({"1": entry()})

        class Response:
            status = 200

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return None

            async def json(self, **kwargs):
                return {"state": {"reported": {"sh": {}}}}

        class Session:
            def get(self, *args, **kwargs):
                return Response()

        rig.client._session = Session()
        rig.client.id_token = "synthetic"

        async def auth():
            pass

        rig.client.async_ensure_auth = auth
        rig.client.async_get_shadow = lambda: api.TCXClient.async_get_shadow(rig.client)
        result = await rig.manager.async_read()
        assert result["schedules"] == []
        assert rig.client.reported["sh"] == {}
        assert rig.manager._rest_sequence == 1
        assert rig.manager._authorization_sequence == 0

    asyncio.run(run())


@pytest.mark.parametrize("changes", [{"on": "10:00"}, {"of": "10:15"}, {"en": False}, {"en": 2}])
def test_unknown_existing_schedule_format_blocks_enabling(changes):
    async def run():
        rig = Rig({"1": entry(en=1, on="T=10:00", of="T=10:15") | changes})
        with pytest.raises(ScheduleError, match="cannot be checked for overlap"):
            await rig.create(enabled=True)
        assert not rig.messages

    asyncio.run(run())


def test_shutdown_during_disk_save_does_not_send():
    async def run():
        rig = Rig()
        plan = await rig.create()

        async def stopping_during_save(data):
            await rig.save(data)
            rig.client._stopping = True

        rig.manager._save = stopping_during_save
        with pytest.raises(ScheduleError, match="connection changed"):
            await rig.manager.async_apply(plan["plan_id"])
        assert not rig.messages
        assert rig.disk["pending"] is not None

    asyncio.run(run())
