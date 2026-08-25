from __future__ import annotations

import asyncio

import pytest

from custom_components.tcx_direct import api


def make_client() -> api.TCXClient:
    return api.TCXClient(object(), "user@example.com", "password", "device")


def test_collect_reported_merges_all_namespaces() -> None:
    payload = {
        "main": {"state": {"reported": {"water": {"value": 317}}}},
        "ecm": {
            "state": {
                "reported": {"ecm0": {"st": 1, "reqSpd": 2600}}
            }
        },
        "filt": {"state": {"reported": {"filt0": {"manSpd": 1100}}}},
    }

    assert api._collect_reported(payload) == {
        "water": {"value": 317},
        "ecm0": {"st": 1, "reqSpd": 2600},
        "filt0": {"manSpd": 1100},
    }


def test_normalize_observed_tcx_state() -> None:
    reported = {
        "water": {"value": 317, "us": 1},
        "air": {"value": 306, "us": 1},
        "TspBdy0": {"waterTempSet": 266},
        "connectionRSSI": -51,
        "ecm0": {
            "st": 1,
            "reqSpd": 2600,
            "minSpd": 600,
            "maxSpd": 3450,
            "spdList": [{"name": "Pool Filtration", "speed": 1100}],
        },
        "filt0": {
            "et": "F_CTRL",
            "app": "FILT",
            "manSpd": 2600,
        },
        "pool": {"et": "V_POS", "app": "POOL_M", "st": 1},
        "auxz0": {"et": "JL", "app": "POOL_LT", "st": 1, "currClr": 3},
        "fcr0": {"fr": "Waterfall", "et": "FRLY", "app": "WF", "st": 0},
    }

    normalized = api.normalize_tcx_state(reported)

    assert normalized["pool_temperature"] == 89.1
    assert normalized["air_temperature"] == 87.1
    assert normalized["pool_temperature_setpoint"] == 79.9
    assert normalized["pump"] is True
    assert normalized["pump_rpm"] == 2600
    assert normalized["pump_speed_setpoint"] == 2600
    assert normalized["pump_power_setpoint"] is True
    assert normalized["pump_power_control_supported"] is True
    assert normalized["pump_speed_control_supported"] is True
    assert normalized["pump_preset"] == "Manual"
    assert normalized["light"] is True
    assert normalized["light_power_setpoint"] is True
    assert normalized["light_control_supported"] is True
    assert normalized["light_color"] == 3
    assert normalized["light_color_name"] == "Romance"
    assert normalized["waterfall"] is False
    assert normalized["swc_level"] is None


def test_pump_priming_reports_commanded_rpm_and_requested_preset() -> None:
    normalized = api.normalize_tcx_state(
        {
            "ecm0": {
                "st": 1,
                "cmdSpd": 2500,
                "reqSpd": 2850,
                "manSpd": 2850,
                "prmSpd": 2500,
                "spdList": [{"name": "Waterfall", "speed": 2850}],
            }
        }
    )

    assert normalized["pump"] is True
    assert normalized["pump_rpm"] == 2500
    assert normalized["pump_speed_setpoint"] == 2850
    assert normalized["pump_preset"] == "Waterfall"


def test_light_color_clears_when_light_is_off() -> None:
    normalized = api.normalize_tcx_state(
        {
            "auxz0": {
                "et": "JL",
                "app": "POOL_LT",
                "st": 0,
                "currClr": 0,
                "cmdClr": 3,
                "svdClr": 3,
            }
        }
    )

    assert normalized["light"] is False
    assert normalized["light_color"] is None
    assert normalized["light_color_name"] is None


def test_pool_light_requires_confirmed_equipment_type() -> None:
    assert api.normalize_tcx_state(
        {"auxz0": {"et": "JL", "app": "POOL_LT", "st": 1}}
    )["light"] is True
    assert api.normalize_tcx_state(
        {"auxz0": {"et": "OTHER", "app": "POOL_LT", "st": 1}}
    )["light"] is None
    assert api.normalize_tcx_state(
        {"auxz0": {"et": "JL", "app": "OTHER", "st": 1}}
    )["light"] is None


def test_derived_values_clear_when_equipment_turns_off() -> None:
    current = {
        "light": True,
        "light_color": 3,
        "light_color_name": "Romance",
        "pump": True,
        "pump_preset": "Waterfall",
    }

    merged = api.merge_normalized_state(
        current,
        {
            "light": False,
            "light_color": None,
            "light_color_name": None,
            "pump": False,
            "pump_preset": None,
        },
    )

    assert merged["light"] is False
    assert "light_color" not in merged
    assert "light_color_name" not in merged
    assert merged["pump"] is False
    assert "pump_preset" not in merged


def test_waterfall_requires_confirmed_feature_type() -> None:
    assert api.normalize_tcx_state(
        {"fcr0": {"fr": "Waterfall", "et": "FRLY", "app": "WF", "st": 1}}
    )["waterfall"] is True
    assert api.normalize_tcx_state(
        {"fcr0": {"fr": "Waterfall", "et": "OTHER", "app": "WF", "st": 1}}
    )["waterfall"] is None
    assert api.normalize_tcx_state(
        {"fcr0": {"fr": "Waterfall", "et": "FRLY", "app": "SWC", "st": 1}}
    )["waterfall"] is None


def test_build_set_state_message_matches_zodiac_protocol() -> None:
    assert api.build_set_state_message(
        "device",
        "12345",
        "tcx",
        {"fcr0": {"st": 1}},
        client_token="12345|test-token",
    ) == {
        "action": "setState",
        "version": 1,
        "namespace": "tcx",
        "payload": {
            "state": {"desired": {"fcr0": {"st": 1}}},
            "clientToken": "12345|test-token",
        },
        "service": "StateController",
        "target": "device",
    }


def test_waterfall_control_waits_for_reported_confirmation() -> None:
    client = make_client()
    client.user_id = "12345"
    client.websocket_connected = True
    client.reported = {
        "fcr0": {"fr": "Waterfall", "et": "FRLY", "app": "WF", "st": 0}
    }

    class FakeWebSocket:
        closed = False

        def __init__(self) -> None:
            self.messages: list[dict[str, object]] = []

        async def send_json(self, message: dict[str, object]) -> None:
            self.messages.append(message)
            client.reported["fcr0"]["st"] = 1
            client._resolve_pending_control()

    websocket = FakeWebSocket()
    client._ws = websocket  # type: ignore[assignment]

    asyncio.run(client.async_set_waterfall(True))

    assert websocket.messages[0]["namespace"] == "tcx"
    assert websocket.messages[0]["payload"]["state"]["desired"] == {
        "fcr0": {"st": 1}
    }
    assert client.control_command_count == 1
    assert client.control_success_count == 1
    assert client.control_failure_count == 0
    assert client.last_control_error is None
    assert client.last_control_frame is not None
    assert client.last_control_frame["namespace"] == "tcx"
    assert client.last_control_frame["target"] == "**REDACTED**"
    assert client.last_control_frame["payload"]["clientToken"] == "**REDACTED**"


def test_pump_power_control_targets_confirmed_pool_mode() -> None:
    client = make_client()
    client.user_id = "12345"
    client.websocket_connected = True
    client.reported = {
        "pool": {"fr": "Pool Filtration", "et": "V_POS", "app": "POOL_M", "st": 0},
        "ecm0": {"st": 0},
    }

    class FakeWebSocket:
        closed = False

        def __init__(self) -> None:
            self.messages: list[dict[str, object]] = []

        async def send_json(self, message: dict[str, object]) -> None:
            self.messages.append(message)
            client.reported["pool"]["st"] = 1
            client.reported["ecm0"]["st"] = 1
            client._resolve_pending_control()

    websocket = FakeWebSocket()
    client._ws = websocket  # type: ignore[assignment]

    asyncio.run(client.async_set_pump_power(True))

    assert websocket.messages[0]["namespace"] == "tcx"
    assert websocket.messages[0]["payload"]["state"]["desired"] == {
        "pool": {"st": 1}
    }
    assert client.control_success_count == 1


def test_pool_light_control_targets_confirmed_light_and_waits_for_report() -> None:
    client = make_client()
    client.user_id = "12345"
    client.websocket_connected = True
    client.reported = {
        "auxz4": {
            "fr": "Pool Light",
            "et": "JL",
            "app": "POOL_LT",
            "st": 0,
            "currClr": 3,
        }
    }

    class FakeWebSocket:
        closed = False

        def __init__(self) -> None:
            self.messages: list[dict[str, object]] = []

        async def send_json(self, message: dict[str, object]) -> None:
            self.messages.append(message)
            client.reported["auxz4"]["st"] = 1
            client._resolve_pending_control()

    websocket = FakeWebSocket()
    client._ws = websocket  # type: ignore[assignment]

    asyncio.run(client.async_set_pool_light(True))

    assert websocket.messages[0]["namespace"] == "tcx"
    assert websocket.messages[0]["payload"]["state"]["desired"] == {
        "auxz4": {"st": 1}
    }
    assert client.control_command_counts["pool light state"] == 1
    assert client.control_success_counts["pool light state"] == 1


def test_pump_speed_control_targets_filter_controller_and_enforces_limits() -> None:
    client = make_client()
    client.user_id = "12345"
    client.websocket_connected = True
    client.reported = {
        "filt0": {
            "fr": "Filtration",
            "et": "F_CTRL",
            "app": "FILT",
            "manSpd": 1100,
            "minSpd": 600,
            "maxSpd": 3450,
        },
        "ecm0": {"minSpd": 600, "maxSpd": 3450},
    }

    class FakeWebSocket:
        closed = False

        def __init__(self) -> None:
            self.messages: list[dict[str, object]] = []

        async def send_json(self, message: dict[str, object]) -> None:
            self.messages.append(message)
            client.reported["filt0"]["manSpd"] = 2250
            client._resolve_pending_control()

    websocket = FakeWebSocket()
    client._ws = websocket  # type: ignore[assignment]

    asyncio.run(client.async_set_pump_speed(2250))

    assert websocket.messages[0]["namespace"] == "tcx"
    assert websocket.messages[0]["payload"]["state"]["desired"] == {
        "filt0": {"manSpd": 2250}
    }
    assert client.control_success_count == 1

    with pytest.raises(api.TCXControlUnsupported, match="between 600 and 3450"):
        asyncio.run(client.async_set_pump_speed(4000))


def test_stale_subscription_refreshes_without_reconnect() -> None:
    client = make_client()
    client.user_id = "12345"
    client.websocket_connected = True

    class FakeWebSocket:
        closed = False

        async def send_json(self, message: dict[str, object]) -> None:
            client._authorization_snapshot_event.set()

    client._ws = FakeWebSocket()  # type: ignore[assignment]

    assert asyncio.run(client._async_refresh_stale_subscription()) is True
    assert client.watchdog_resubscribe_count == 1
    assert client.watchdog_resubscribe_success_count == 1
    assert client.watchdog_resubscribe_failure_count == 0
    assert client.websocket_reconnect_count == 0


def test_shadow_rate_limit_backs_off_without_marking_live_socket_offline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = make_client()
    client.websocket_connected = True
    client.cloud_reachable = True
    delays: list[float] = []

    async def get_shadow() -> dict[str, object]:
        raise api.TCXRateLimited("HTTP 429", retry_after=180)

    async def sleep(delay: float) -> None:
        delays.append(delay)
        if delay != 2:
            client._stopping = True

    monkeypatch.setattr(client, "async_get_shadow", get_shadow)
    monkeypatch.setattr(api.asyncio, "sleep", sleep)

    asyncio.run(client._shadow_loop())

    assert delays == [2, 240]
    assert client.shadow_rate_limit_count == 1
    assert client.shadow_poll_interval == 240
    assert client.cloud_reachable is True
    assert client.last_error is None
    assert client.last_shadow_error == "HTTP 429"


def test_new_socket_does_not_inherit_previous_freshness(monkeypatch: pytest.MonkeyPatch) -> None:
    client = make_client()
    client.websocket_connected = True
    client.last_ws_reported_monotonic = 95
    monkeypatch.setattr(api.time, "monotonic", lambda: 100)
    assert client.websocket_stream_healthy is True

    client._mark_websocket_opened()

    assert client.websocket_stream_healthy is False
    assert client.last_ws_reported_monotonic is None
    assert client._ws_opened_monotonic == 100


def test_desired_payloads_are_deduplicated() -> None:
    client = make_client()
    data = {"service": "StateStreamer", "event": "StateReported"}

    client.last_ws_message_at = "first"
    client._record_desired_payload(data, {"timestamp": 1}, {"freezeSP": 33})
    client.last_ws_message_at = "second"
    client._record_desired_payload(data, {"timestamp": 2}, {"freezeSP": 33})
    client._record_desired_payload(data, {"timestamp": 3}, {"freezeSP": 34})

    records = client.recent_desired_payloads
    assert len(records) == 2
    assert records[0]["count"] == 2
    assert records[0]["first_seen"] == "first"
    assert records[0]["last_seen"] == "second"
    assert records[0]["timestamp"] == 2


def test_shadow_failures_are_counted(monkeypatch: pytest.MonkeyPatch) -> None:
    client = make_client()

    async def fail() -> dict[str, object]:
        raise api.TCXConnectionError("offline")

    monkeypatch.setattr(client, "_async_get_shadow", fail)

    with pytest.raises(api.TCXConnectionError):
        asyncio.run(client.async_get_shadow())

    assert client.shadow_request_count == 1
    assert client.shadow_success_count == 0
    assert client.shadow_failure_count == 1


def test_connection_failure_clears_cloud_reachability() -> None:
    client = make_client()
    client.cloud_reachable = True

    client._record_connection_failure("socket closed")

    assert client.cloud_reachable is False
    assert client.last_error == "socket closed"


def test_reconnect_reasons_are_counted() -> None:
    client = make_client()

    asyncio.run(client.async_force_reconnect("watchdog_stale_stream"))
    asyncio.run(client.async_force_reconnect("watchdog_session_rotation"))

    assert client.watchdog_reconnect_count == 2
    assert client.reconnect_reason_counts == {
        "watchdog_stale_stream": 1,
        "watchdog_session_rotation": 1,
    }


def test_shadow_poll_does_not_force_websocket_reconnect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = make_client()
    client.shadow_supported = True
    client.websocket_connected = True
    client.last_ws_device_timestamp = 100
    client.last_ws_reported_monotonic = 0
    reconnect_reasons: list[str] = []

    async def get_shadow() -> dict[str, object]:
        return {"timestamp": 200}

    async def force_reconnect(reason: str) -> None:
        reconnect_reasons.append(reason)

    async def sleep(delay: float) -> None:
        if delay == api.SHADOW_INTERVAL:
            client._stopping = True

    monkeypatch.setattr(client, "async_get_shadow", get_shadow)
    monkeypatch.setattr(client, "async_force_reconnect", force_reconnect)
    monkeypatch.setattr(api.asyncio, "sleep", sleep)
    monkeypatch.setattr(api.time, "monotonic", lambda: 1_000)

    asyncio.run(client._shadow_loop())

    assert reconnect_reasons == []
