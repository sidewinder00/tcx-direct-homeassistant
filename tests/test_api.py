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
        "auxz0": {"st": 0, "currClr": 3},
        "fcr0": {"fr": "Waterfall", "app": "WF", "st": 0},
    }

    normalized = api.normalize_tcx_state(reported)

    assert normalized["pool_temperature"] == 89.1
    assert normalized["air_temperature"] == 87.1
    assert normalized["pool_temperature_setpoint"] == 79.9
    assert normalized["pump"] is True
    assert normalized["pump_rpm"] == 2600
    assert normalized["pump_preset"] == "Manual"
    assert normalized["light"] is False
    assert normalized["light_color_name"] == "Romance"
    assert normalized["swc_level"] is None


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
