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
        "auxz0": {"st": 1, "currClr": 3},
        "fcr0": {"fr": "Waterfall", "et": "FRLY", "app": "WF", "st": 0},
    }

    normalized = api.normalize_tcx_state(reported)

    assert normalized["pool_temperature"] == 89.1
    assert normalized["air_temperature"] == 87.1
    assert normalized["pool_temperature_setpoint"] == 79.9
    assert normalized["pump"] is True
    assert normalized["pump_rpm"] == 2600
    assert normalized["pump_preset"] == "Manual"
    assert normalized["light"] is True
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
    assert normalized["pump_preset"] == "Waterfall"


def test_light_color_clears_when_light_is_off() -> None:
    normalized = api.normalize_tcx_state(
        {
            "auxz0": {
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
            client._resolve_pending_waterfall_state()

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
