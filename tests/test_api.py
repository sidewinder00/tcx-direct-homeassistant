from __future__ import annotations

import asyncio

import pytest

from custom_components.tcx_direct import api
from custom_components.tcx_direct.const import (
    CONTROL_CONFIRM_TIMEOUT,
    POOL_FILTRATION_CONFIRM_TIMEOUT,
    PUMP_POWER_CONFIRM_TIMEOUT,
)


def make_client() -> api.TCXClient:
    return api.TCXClient(object(), "user@example.com", "password", "device")


def post_prime_client(filter_key: str = "filt0") -> api.TCXClient:
    """Return a client waiting in a confirmed TCX priming state."""
    client = make_client()
    client.reported = {
        "pool": {"et": "V_POS", "app": "POOL_M", "st": 1},
        filter_key: {
            "et": "F_CTRL",
            "app": "FILT",
            "manSpd": 2575,
            "minSpd": 600,
            "maxSpd": 3450,
        },
        "ecm0": {
            "st": 1,
            "manSpd": 2575,
            "reqSpd": 2600,
            "cmdSpd": 2500,
            "prmSpd": 2500,
            "minSpd": 600,
            "maxSpd": 3450,
        },
        "fcr0": {"et": "FRLY", "app": "WF", "st": 0},
    }
    return client


def test_collect_reported_merges_all_namespaces() -> None:
    payload = {
        "main": {"state": {"reported": {"water": {"value": 317}}}},
        "ecm": {"state": {"reported": {"ecm0": {"st": 1, "reqSpd": 2600}}}},
        "filt": {"state": {"reported": {"filt0": {"manSpd": 1100}}}},
    }

    assert api._collect_reported(payload) == {
        "water": {"value": 317},
        "ecm0": {"st": 1, "reqSpd": 2600},
        "filt0": {"manSpd": 1100},
    }


def test_normalize_observed_tcx_state() -> None:
    reported = {
        "systemMode": 1,
        "freezeSP": 33,
        "water": {"value": 317, "us": 1},
        "air": {"value": 306, "us": 1},
        "TspBdy0": {"waterTempSet": 266},
        "connectionRSSI": -51,
        "ecm0": {
            "st": 1,
            "reqSpd": 2600,
            "minSpd": 600,
            "maxSpd": 3450,
            "spdList": [
                {
                    "name": "Pool Filtration",
                    "speed": 1100,
                    "app": "BD1_F",
                    "ar": 1,
                },
                {
                    "name": "Spa Filtration",
                    "speed": 2750,
                    "app": "BD2_F",
                    "ar": 2,
                },
                {"name": "Waterfall", "speed": 2850, "app": "WF", "ar": 3},
            ],
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
    assert normalized["pump_requested_rpm"] == 2600
    assert normalized["pump_operating_phase"] == "Running"
    assert normalized["pump_speed_setpoint"] == 2600
    assert normalized["pool_filtration_preset"] == 1100
    assert normalized["pool_filtration_preset_control_supported"] is True
    assert normalized["pump_power_setpoint"] is True
    assert normalized["pump_power_control_supported"] is True
    assert normalized["pump_speed_control_supported"] is True
    assert normalized["pump_preset"] == "Manual"
    assert normalized["light"] is True
    assert normalized["light_power_setpoint"] is True
    assert normalized["light_control_supported"] is True
    assert normalized["light_color_control_supported"] is True
    assert normalized["light_color"] == 3
    assert normalized["light_color_name"] == "Cobalt Blue"
    assert normalized["waterfall"] is False
    assert normalized["controller_mode"] == "Auto"
    assert normalized["system_mode_code"] == 1
    assert normalized["remote_control_available"] is True
    assert normalized["freeze_protection_setpoint"] == 33
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
    assert normalized["pump_requested_rpm"] == 2850
    assert normalized["pump_operating_phase"] == "Priming"
    assert normalized["pump_speed_setpoint"] == 2850
    assert normalized["pump_preset"] == "Waterfall"


def test_unknown_controller_mode_preserves_raw_code() -> None:
    normalized = api.normalize_tcx_state({"systemMode": 7})

    assert normalized["controller_mode"] == "Unknown (code 7)"
    assert normalized["system_mode_code"] == 7
    assert normalized["remote_control_available"] is False


@pytest.mark.parametrize(
    ("code", "name"),
    [
        (1, "Auto"),
        (2, "Quick Clean"),
        (3, "Service"),
        (4, "Time Out"),
        (5, "Transitioning"),
    ],
)
def test_confirmed_controller_modes(code: int, name: str) -> None:
    normalized = api.normalize_tcx_state({"systemMode": code})

    assert normalized["controller_mode"] == name
    assert normalized["system_mode_code"] == code
    assert normalized["remote_control_available"] is (code == 1)


@pytest.mark.parametrize(
    ("reported", "expected"),
    [
        ({"ecm0": {"st": 0, "cmdSpd": 0, "reqSpd": 0}}, "Off"),
        (
            {
                "ecm0": {"st": 1, "cmdSpd": 2500, "reqSpd": 1100, "prmSpd": 2500},
            },
            "Priming",
        ),
        (
            {
                "ecm0": {"st": 1, "cmdSpd": 1600, "reqSpd": 1100, "prmSpd": 2500},
            },
            "Transitioning",
        ),
        (
            {
                "ecm0": {"st": 1, "cmdSpd": 2850, "reqSpd": 2850, "prmSpd": 2500},
                "fcr0": {"et": "FRLY", "app": "WF", "st": 1},
            },
            "Waterfall",
        ),
    ],
)
def test_pump_operating_phase(reported: dict[str, object], expected: str) -> None:
    assert api.normalize_tcx_state(reported)["pump_operating_phase"] == expected


@pytest.mark.parametrize(
    ("requested", "commanded", "priming", "observed", "expected"),
    [
        (2600, 2500, 2500, False, False),
        (2600, 2600, 2500, False, True),
        (2575, 2575, 2500, False, False),
        (2575, 2575, 2500, True, True),
    ],
)
def test_post_prime_sync_readiness(
    requested: float,
    commanded: float,
    priming: float,
    observed: bool,
    expected: bool,
) -> None:
    assert api._post_prime_sync_ready(2600, requested, commanded, priming, observed) is expected


def test_pump_manual_speed_remains_separate_from_commanded_speed() -> None:
    normalized = api.normalize_tcx_state(
        {
            "ecm0": {
                "st": 1,
                "cmdSpd": 2650,
                "reqSpd": 2650,
                "manSpd": 2650,
                "minSpd": 600,
                "maxSpd": 3450,
            },
            "filt0": {
                "et": "F_CTRL",
                "app": "FILT",
                "manSpd": 1100,
            },
        }
    )

    assert normalized["pump_rpm"] == 2650
    assert normalized["pump_speed_setpoint"] == 1100


def test_pump_requested_rpm_does_not_fall_back_to_manual_speed() -> None:
    normalized = api.normalize_tcx_state({"ecm0": {"st": 1, "cmdSpd": 1200, "manSpd": 1200}})

    assert normalized["pump_requested_rpm"] is None
    assert normalized["pump_operating_phase"] == "Running"


def test_pump_manual_speed_ignores_priming_and_runtime_changes() -> None:
    priming = api.normalize_tcx_state(
        {
            "ecm0": {
                "st": 1,
                "cmdSpd": 2500,
                "reqSpd": 1100,
                "manSpd": 1100,
                "minSpd": 600,
                "maxSpd": 3450,
            },
            "filt0": {
                "et": "F_CTRL",
                "app": "FILT",
                "manSpd": 2000,
            },
        }
    )
    runtime_change = api.normalize_tcx_state(
        {
            "ecm0": {
                "st": 1,
                "cmdSpd": 2650,
                "reqSpd": 2650,
                "manSpd": 2650,
                "minSpd": 600,
                "maxSpd": 3450,
            },
            "filt0": {
                "et": "F_CTRL",
                "app": "FILT",
                "manSpd": 2000,
            },
        }
    )

    assert priming["pump_rpm"] == 2500
    assert runtime_change["pump_rpm"] == 2650
    assert priming["pump_speed_setpoint"] == 2000
    assert runtime_change["pump_speed_setpoint"] == 2000


def test_stopped_pump_speed_control_value_stays_within_writable_range() -> None:
    normalized = api.normalize_tcx_state(
        {
            "ecm0": {
                "st": 0,
                "cmdSpd": 0,
                "reqSpd": 0,
                "manSpd": 1100,
                "minSpd": 600,
                "maxSpd": 3450,
            },
            "filt0": {
                "et": "F_CTRL",
                "app": "FILT",
                "manSpd": 1100,
            },
        }
    )

    assert normalized["pump_rpm"] == 0
    assert normalized["pump_speed_setpoint"] == 1100


def test_transient_zero_is_suppressible_while_filtration_is_requested() -> None:
    reported = {
        "ecm0": {
            "st": 0,
            "cmdSpd": 0,
            "reqSpd": 2725,
            "manSpd": 2725,
        },
        "filt0": {
            "et": "F_CTRL",
            "app": "FILT",
            "st": 1,
            "manSpd": 2725,
        },
        "pool": {"et": "V_POS", "app": "POOL_M", "st": 1},
    }
    parsed = api.normalize_tcx_state(reported)

    assert parsed["pump_rpm"] == 0
    assert api.should_suppress_transient_pump_zero(2650, parsed, reported) is True


def test_actual_pump_off_zero_is_not_suppressed() -> None:
    reported = {
        "ecm0": {"st": 0, "cmdSpd": 0, "reqSpd": 0, "manSpd": 1100},
        "filt0": {
            "et": "F_CTRL",
            "app": "FILT",
            "st": 0,
            "manSpd": 1100,
        },
        "pool": {"et": "V_POS", "app": "POOL_M", "st": 0},
        "fcr0": {"fr": "Waterfall", "et": "FRLY", "app": "WF", "st": 0},
    }
    parsed = api.normalize_tcx_state(reported)

    assert api.should_suppress_transient_pump_zero(2650, parsed, reported) is False


def test_light_color_clears_when_light_is_off() -> None:
    normalized = api.normalize_tcx_state(
        {
            "auxz0": {
                "et": "JL",
                "app": "POOL_LT",
                "st": 0,
                "currClr": 3,
                "cmdClr": 3,
                "svdClr": 3,
            }
        }
    )

    assert normalized["light"] is False
    assert normalized["light_color"] is None
    assert normalized["light_color_name"] is None


@pytest.mark.parametrize(
    ("code", "name"),
    [
        (1, "Alpine White"),
        (2, "Sky Blue"),
        (3, "Cobalt Blue"),
        (4, "Caribbean Blue"),
        (5, "Spring Green"),
        (6, "Emerald Green"),
        (7, "Emerald Rose"),
        (8, "Magenta"),
        (9, "Violet"),
        (10, "Slow Color Splash"),
        (11, "Fast Color Splash"),
        (12, "America The Beautiful"),
        (13, "Fat Tuesday"),
        (14, "Disco Tech"),
    ],
)
def test_confirmed_pool_light_color_names(code: int, name: str) -> None:
    normalized = api.normalize_tcx_state(
        {
            "auxz0": {
                "et": "JL",
                "app": "POOL_LT",
                "st": 1,
                "cmdClr": code,
                "currClr": 99,
            }
        }
    )

    assert normalized["light_color"] == code
    assert normalized["light_color_name"] == name


def test_pool_light_requires_confirmed_equipment_type() -> None:
    assert (
        api.normalize_tcx_state({"auxz0": {"et": "JL", "app": "POOL_LT", "st": 1}})["light"] is True
    )
    assert (
        api.normalize_tcx_state({"auxz0": {"et": "OTHER", "app": "POOL_LT", "st": 1}})["light"]
        is None
    )
    assert (
        api.normalize_tcx_state({"auxz0": {"et": "JL", "app": "OTHER", "st": 1}})["light"] is None
    )


def test_derived_values_clear_when_equipment_turns_off() -> None:
    current = {
        "light": True,
        "light_color": 3,
        "light_color_name": "Cobalt Blue",
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
    assert (
        api.normalize_tcx_state({"fcr0": {"fr": "Waterfall", "et": "FRLY", "app": "WF", "st": 1}})[
            "waterfall"
        ]
        is True
    )
    assert (
        api.normalize_tcx_state({"fcr0": {"fr": "Waterfall", "et": "OTHER", "app": "WF", "st": 1}})[
            "waterfall"
        ]
        is None
    )
    assert (
        api.normalize_tcx_state({"fcr0": {"fr": "Waterfall", "et": "FRLY", "app": "SWC", "st": 1}})[
            "waterfall"
        ]
        is None
    )


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


def test_failed_control_publishes_failed_status() -> None:
    client = make_client()
    client.user_id = "12345"
    client.websocket_connected = True
    published_statuses: list[str] = []

    class FakeWebSocket:
        closed = False

        async def send_json(self, _message: dict[str, object]) -> None:
            return

    async def handle_state(_reported: dict[str, object], _source: str) -> None:
        return

    async def handle_status() -> None:
        published_statuses.append(client.control_status)

    client._ws = FakeWebSocket()  # type: ignore[assignment]
    client.set_callbacks(handle_state, handle_status)  # type: ignore[arg-type]

    with pytest.raises(api.TCXConnectionError, match="did not confirm test command"):
        asyncio.run(
            client._async_send_control(
                {"pool": {"st": 1}},
                "test command",
                lambda _reported: False,
                confirmation_timeout=0,
            )
        )

    assert client.control_status == "Failed"
    assert published_statuses == ["Failed"]


def test_waterfall_control_waits_for_reported_confirmation() -> None:
    client = make_client()
    client.user_id = "12345"
    client.websocket_connected = True
    client.reported = {"fcr0": {"fr": "Waterfall", "et": "FRLY", "app": "WF", "st": 0}}

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
    assert websocket.messages[0]["payload"]["state"]["desired"] == {"fcr0": {"st": 1}}
    assert client.control_command_count == 1
    assert client.control_success_count == 1
    assert client.control_failure_count == 0
    assert client.control_status == "Confirmed"
    assert client.last_control_error is None
    assert client.last_control_frame is not None
    assert client.last_control_frame["namespace"] == "tcx"
    assert client.last_control_frame["target"] == "**REDACTED**"
    assert client.last_control_frame["payload"]["clientToken"] == "**REDACTED**"


def test_waterfall_with_speed_confirms_relay_then_sets_manual_rpm() -> None:
    client = make_client()
    client.user_id = "12345"
    client.websocket_connected = True
    client.reported = {
        "fcr0": {"fr": "Waterfall", "et": "FRLY", "app": "WF", "st": 0},
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
            desired = message["payload"]["state"]["desired"]
            if "fcr0" in desired:
                client.reported["fcr0"]["st"] = desired["fcr0"]["st"]
            if "filt0" in desired:
                client.reported["filt0"]["manSpd"] = desired["filt0"]["manSpd"]
            client._resolve_pending_control()

    websocket = FakeWebSocket()
    client._ws = websocket  # type: ignore[assignment]

    asyncio.run(client.async_set_waterfall_with_speed(2850))

    desired_frames = [message["payload"]["state"]["desired"] for message in websocket.messages]
    assert desired_frames == [
        {"fcr0": {"st": 1}},
        {"filt0": {"manSpd": 2850}},
    ]
    assert client.control_command_counts["waterfall state"] == 1
    assert client.control_command_counts["pump speed"] == 1
    assert client.control_success_count == 2


def test_waterfall_off_restores_pool_filtration_preset_with_dynamic_filter_key() -> None:
    client = make_client()
    client.user_id = "12345"
    client.websocket_connected = True
    client.reported = {
        "pool": {"et": "V_POS", "app": "POOL_M", "st": 1},
        "fcr0": {"fr": "Waterfall", "et": "FRLY", "app": "WF", "st": 1},
        "filt3": {
            "et": "F_CTRL",
            "app": "FILT",
            "manSpd": 2850,
            "minSpd": 600,
            "maxSpd": 3450,
            "spdList": [{"name": "Pool Filtration", "speed": 1100, "app": "BD1_F", "ar": 1}],
        },
        "ecm0": {"st": 1, "minSpd": 600, "maxSpd": 3450},
    }

    class FakeWebSocket:
        closed = False

        def __init__(self) -> None:
            self.messages: list[dict[str, object]] = []

        async def send_json(self, message: dict[str, object]) -> None:
            self.messages.append(message)
            desired = message["payload"]["state"]["desired"]
            if "fcr0" in desired:
                client.reported["fcr0"]["st"] = desired["fcr0"]["st"]
            if "filt3" in desired:
                client.reported["filt3"]["manSpd"] = desired["filt3"]["manSpd"]
            client._resolve_pending_control()

    websocket = FakeWebSocket()
    client._ws = websocket  # type: ignore[assignment]

    asyncio.run(client.async_set_waterfall(False))

    desired_frames = [message["payload"]["state"]["desired"] for message in websocket.messages]
    assert desired_frames == [
        {"fcr0": {"st": 0}},
        {"filt3": {"manSpd": 1100}},
    ]
    assert client.control_command_counts["waterfall state"] == 1
    assert client.control_command_counts["waterfall speed restore"] == 1
    assert client.control_success_count == 2
    assert client.reported["fcr0"]["st"] == 0
    assert client.reported["filt3"]["manSpd"] == 1100


@pytest.mark.parametrize(
    ("pool_running", "motor_running"),
    [(False, True), (True, False)],
)
def test_waterfall_off_does_not_restore_speed_when_filtration_is_not_running(
    pool_running: bool,
    motor_running: bool,
) -> None:
    client = make_client()
    client.user_id = "12345"
    client.websocket_connected = True
    client.reported = {
        "pool": {"et": "V_POS", "app": "POOL_M", "st": int(pool_running)},
        "fcr0": {"et": "FRLY", "app": "WF", "st": 1},
        "filt0": {
            "et": "F_CTRL",
            "app": "FILT",
            "manSpd": 2850,
            "minSpd": 600,
            "maxSpd": 3450,
        },
        "ecm0": {
            "st": int(motor_running),
            "minSpd": 600,
            "maxSpd": 3450,
            "spdList": [{"speed": 1100, "app": "BD1_F"}],
        },
    }

    class FakeWebSocket:
        closed = False

        def __init__(self) -> None:
            self.messages: list[dict[str, object]] = []

        async def send_json(self, message: dict[str, object]) -> None:
            self.messages.append(message)
            desired = message["payload"]["state"]["desired"]
            client.reported["fcr0"]["st"] = desired["fcr0"]["st"]
            client._resolve_pending_control()

    websocket = FakeWebSocket()
    client._ws = websocket  # type: ignore[assignment]

    asyncio.run(client.async_set_waterfall(False))

    assert [message["payload"]["state"]["desired"] for message in websocket.messages] == [
        {"fcr0": {"st": 0}}
    ]
    assert client.control_command_count == 1


def test_waterfall_off_skips_restore_when_pool_preset_is_unavailable(caplog) -> None:
    client = make_client()
    client.user_id = "12345"
    client.websocket_connected = True
    client.reported = {
        "pool": {"et": "V_POS", "app": "POOL_M", "st": 1},
        "fcr0": {"et": "FRLY", "app": "WF", "st": 1},
        "filt0": {
            "et": "F_CTRL",
            "app": "FILT",
            "manSpd": 2850,
            "minSpd": 600,
            "maxSpd": 3450,
        },
        "ecm0": {"st": 1, "minSpd": 600, "maxSpd": 3450},
    }

    class FakeWebSocket:
        closed = False

        def __init__(self) -> None:
            self.messages: list[dict[str, object]] = []

        async def send_json(self, message: dict[str, object]) -> None:
            self.messages.append(message)
            client.reported["fcr0"]["st"] = 0
            client._resolve_pending_control()

    websocket = FakeWebSocket()
    client._ws = websocket  # type: ignore[assignment]

    asyncio.run(client.async_set_waterfall(False))

    assert [message["payload"]["state"]["desired"] for message in websocket.messages] == [
        {"fcr0": {"st": 0}}
    ]
    assert "did not report a BD1_F Pool Filtration preset" in caplog.text


def test_waterfall_off_restore_failure_leaves_relay_off() -> None:
    client = make_client()
    client.user_id = "12345"
    client.websocket_connected = True
    client.reported = {
        "pool": {"et": "V_POS", "app": "POOL_M", "st": 1},
        "fcr0": {"et": "FRLY", "app": "WF", "st": 1},
        "filt0": {
            "et": "F_CTRL",
            "app": "FILT",
            "manSpd": 2850,
            "minSpd": 600,
            "maxSpd": 3450,
        },
        "ecm0": {
            "st": 1,
            "minSpd": 600,
            "maxSpd": 3450,
            "spdList": [{"speed": 1100, "app": "BD1_F"}],
        },
    }

    class FakeWebSocket:
        closed = False

        def __init__(self) -> None:
            self.messages: list[dict[str, object]] = []

        async def send_json(self, message: dict[str, object]) -> None:
            self.messages.append(message)
            desired = message["payload"]["state"]["desired"]
            if "fcr0" in desired:
                client.reported["fcr0"]["st"] = 0
                client._resolve_pending_control()
                return
            raise RuntimeError("simulated restore failure")

    websocket = FakeWebSocket()
    client._ws = websocket  # type: ignore[assignment]

    with pytest.raises(api.TCXConnectionError, match="simulated restore failure"):
        asyncio.run(client.async_set_waterfall(False))

    assert client.reported["fcr0"]["st"] == 0
    assert client.control_success_counts["waterfall state"] == 1
    assert client.control_failure_counts["waterfall speed restore"] == 1


def test_waterfall_off_is_idempotent_when_relay_is_already_off() -> None:
    client = make_client()
    client.reported = {
        "fcr0": {"et": "FRLY", "app": "WF", "st": 0},
        "pool": {"et": "V_POS", "app": "POOL_M", "st": 1},
        "filt0": {
            "et": "F_CTRL",
            "app": "FILT",
            "manSpd": 1100,
            "minSpd": 600,
            "maxSpd": 3450,
        },
        "ecm0": {
            "st": 1,
            "reqSpd": 1100,
            "cmdSpd": 1100,
            "prmSpd": 2500,
            "spdList": [{"speed": 1100, "app": "BD1_F"}],
        },
    }

    async def run_scenario() -> None:
        client._schedule_post_prime_sync(1100)
        pending = client._post_prime_sync_task
        assert pending is not None
        await client.async_set_waterfall(False)
        assert client._post_prime_sync_task is pending
        assert pending.done() is False
        await client._async_cancel_post_prime_sync("test_complete")

    asyncio.run(run_scenario())

    assert client.control_command_count == 0


def test_pump_power_control_uses_extended_confirmation_timeout(monkeypatch) -> None:
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

    captured_timeouts: list[float | None] = []
    original_wait_for = asyncio.wait_for

    async def capture_wait_for(awaitable, timeout=None):
        captured_timeouts.append(timeout)
        return await original_wait_for(awaitable, timeout=timeout)

    monkeypatch.setattr(asyncio, "wait_for", capture_wait_for)

    asyncio.run(client.async_set_pump_power(True))

    assert websocket.messages[0]["namespace"] == "tcx"
    assert websocket.messages[0]["payload"]["state"]["desired"] == {"pool": {"st": 1}}
    assert client.control_success_count == 1
    assert captured_timeouts == [PUMP_POWER_CONFIRM_TIMEOUT]
    assert PUMP_POWER_CONFIRM_TIMEOUT > CONTROL_CONFIRM_TIMEOUT


def test_start_pump_at_speed_sets_pool_preset_then_starts_normally(monkeypatch) -> None:
    client = make_client()
    client.user_id = "12345"
    client.websocket_connected = True
    client.reported = {
        "pool": {"fr": "Pool Filtration", "et": "V_POS", "app": "POOL_M", "st": 0},
        "filt0": {
            "fr": "Filtration",
            "et": "F_CTRL",
            "app": "FILT",
            "manSpd": 1100,
            "minSpd": 600,
            "maxSpd": 3450,
        },
        "ecm0": {
            "st": 0,
            "minSpd": 600,
            "maxSpd": 3450,
            "spdList": [
                {
                    "name": "Pool Filtration",
                    "speed": 1100,
                    "app": "BD1_F",
                    "ar": 1,
                },
                {
                    "name": "Spa Filtration",
                    "speed": 2750,
                    "app": "BD2_F",
                    "ar": 2,
                },
                {"name": "Waterfall", "speed": 2850, "app": "WF", "ar": 3},
            ],
        },
    }

    class FakeWebSocket:
        closed = False

        def __init__(self) -> None:
            self.messages: list[dict[str, object]] = []

        async def send_json(self, message: dict[str, object]) -> None:
            self.messages.append(message)
            desired = message["payload"]["state"]["desired"]
            if "ecm0" in desired:
                client.reported["ecm0"]["spdList"] = desired["ecm0"]["spdList"]
            if "pool" in desired:
                client.reported["pool"]["st"] = desired["pool"]["st"]
                client.reported["ecm0"]["st"] = desired["pool"]["st"]
                client.reported["ecm0"]["reqSpd"] = 2575
                client.reported["ecm0"]["cmdSpd"] = 2500
            client._resolve_pending_control()

    websocket = FakeWebSocket()
    client._ws = websocket  # type: ignore[assignment]

    captured_timeouts: list[float | None] = []
    original_wait_for = asyncio.wait_for

    async def capture_wait_for(awaitable, timeout=None):
        captured_timeouts.append(timeout)
        return await original_wait_for(awaitable, timeout=timeout)

    monkeypatch.setattr(asyncio, "wait_for", capture_wait_for)

    asyncio.run(client.async_start_pump_at_speed(2575))

    desired_frames = [message["payload"]["state"]["desired"] for message in websocket.messages]
    assert desired_frames == [
        {
            "ecm0": {
                "spdList": [
                    {
                        "name": "Pool Filtration",
                        "speed": 2575,
                        "app": "BD1_F",
                        "ar": 1,
                    },
                    {
                        "name": "Spa Filtration",
                        "speed": 2750,
                        "app": "BD2_F",
                        "ar": 2,
                    },
                    {
                        "name": "Waterfall",
                        "speed": 2850,
                        "app": "WF",
                        "ar": 3,
                    },
                ]
            }
        },
        {"pool": {"st": 1}},
    ]
    assert captured_timeouts == [
        POOL_FILTRATION_CONFIRM_TIMEOUT,
        PUMP_POWER_CONFIRM_TIMEOUT,
    ]
    assert client.control_command_counts["pool filtration preset"] == 1
    assert client.control_command_counts["pump power state"] == 1
    assert client.control_success_counts["pool filtration preset"] == 1
    assert client.control_success_counts["pump power state"] == 1
    assert client.post_prime_sync_scheduled_count == 1


def test_running_schedule_change_syncs_preset_and_manual_speed_without_power() -> None:
    client = make_client()
    client.user_id = "12345"
    client.websocket_connected = True
    client.reported = {
        "pool": {"fr": "Pool Filtration", "et": "V_POS", "app": "POOL_M", "st": 1},
        "filt0": {
            "fr": "Filtration",
            "et": "F_CTRL",
            "app": "FILT",
            "manSpd": 1100,
            "minSpd": 600,
            "maxSpd": 3450,
        },
        "ecm0": {
            "st": 1,
            "reqSpd": 1100,
            "cmdSpd": 1100,
            "minSpd": 600,
            "maxSpd": 3450,
            "spdList": [
                {"name": "Pool Filtration", "speed": 1100, "app": "BD1_F", "ar": 1},
                {"name": "Spa Filtration", "speed": 2525, "app": "BD2_F", "ar": 2},
                {"name": "Waterfall", "speed": 2850, "app": "WF", "ar": 3},
            ],
        },
    }

    class FakeWebSocket:
        closed = False

        def __init__(self) -> None:
            self.messages: list[dict[str, object]] = []

        async def send_json(self, message: dict[str, object]) -> None:
            self.messages.append(message)
            desired = message["payload"]["state"]["desired"]
            if "ecm0" in desired:
                client.reported["ecm0"]["spdList"] = desired["ecm0"]["spdList"]
            if "filt0" in desired:
                client.reported["filt0"]["manSpd"] = desired["filt0"]["manSpd"]
            client._resolve_pending_control()

    websocket = FakeWebSocket()
    client._ws = websocket  # type: ignore[assignment]

    asyncio.run(client.async_start_pump_at_speed(2600))

    desired_frames = [message["payload"]["state"]["desired"] for message in websocket.messages]
    assert desired_frames == [
        {
            "ecm0": {
                "spdList": [
                    {
                        "name": "Pool Filtration",
                        "speed": 2600,
                        "app": "BD1_F",
                        "ar": 1,
                    },
                    {
                        "name": "Spa Filtration",
                        "speed": 2525,
                        "app": "BD2_F",
                        "ar": 2,
                    },
                    {
                        "name": "Waterfall",
                        "speed": 2850,
                        "app": "WF",
                        "ar": 3,
                    },
                ]
            }
        },
        {"filt0": {"manSpd": 2600}},
    ]
    assert client.post_prime_sync_scheduled_count == 0


def test_cold_start_aligns_manual_speed_only_after_priming(monkeypatch) -> None:
    client = make_client()
    client.user_id = "12345"
    client.websocket_connected = True
    client.reported = {
        "pool": {"fr": "Pool Filtration", "et": "V_POS", "app": "POOL_M", "st": 0},
        "filt0": {
            "fr": "Filtration",
            "et": "F_CTRL",
            "app": "FILT",
            "manSpd": 1100,
            "minSpd": 600,
            "maxSpd": 3450,
        },
        "ecm0": {
            "st": 0,
            "minSpd": 600,
            "maxSpd": 3450,
            "spdList": [
                {"name": "Pool Filtration", "speed": 1100, "app": "BD1_F", "ar": 1},
                {"name": "Spa Filtration", "speed": 2525, "app": "BD2_F", "ar": 2},
                {"name": "Waterfall", "speed": 2850, "app": "WF", "ar": 3},
            ],
        },
        "fcr0": {"fr": "Waterfall", "et": "FRLY", "app": "WF", "st": 0},
    }

    class FakeWebSocket:
        closed = False

        def __init__(self) -> None:
            self.messages: list[dict[str, object]] = []

        async def send_json(self, message: dict[str, object]) -> None:
            self.messages.append(message)
            desired = message["payload"]["state"]["desired"]
            if "ecm0" in desired:
                client.reported["ecm0"]["spdList"] = desired["ecm0"]["spdList"]
            if "pool" in desired:
                client.reported["pool"]["st"] = 1
                client.reported["ecm0"].update({"st": 1, "reqSpd": 2575, "cmdSpd": 2500})
            if "filt0" in desired:
                client.reported["filt0"]["manSpd"] = desired["filt0"]["manSpd"]
            client._resolve_pending_control()

    websocket = FakeWebSocket()
    client._ws = websocket  # type: ignore[assignment]
    monkeypatch.setattr(api, "POST_PRIME_SYNC_INTERVAL", 0)

    async def run_scenario() -> None:
        await client.async_start_pump_at_speed(2575)
        assert [message["payload"]["state"]["desired"] for message in websocket.messages][-1] == {
            "pool": {"st": 1}
        }
        client.reported["ecm0"].update({"reqSpd": 2575, "cmdSpd": 2575})
        task = client._post_prime_sync_task
        assert task is not None
        await task

    asyncio.run(run_scenario())

    desired_frames = [message["payload"]["state"]["desired"] for message in websocket.messages]
    assert desired_frames[-1] == {"filt0": {"manSpd": 2575}}
    assert client.post_prime_sync_success_count == 1
    assert client.last_post_prime_sync_result == "manual_speed_aligned"


def test_cold_start_replaces_controller_restored_stale_manual_speed(monkeypatch) -> None:
    client = make_client()
    client.user_id = "12345"
    client.websocket_connected = True
    client.reported = {
        "pool": {"fr": "Pool Filtration", "et": "V_POS", "app": "POOL_M", "st": 0},
        "filt0": {
            "fr": "Filtration",
            "et": "F_CTRL",
            "app": "FILT",
            "manSpd": 1100,
            "minSpd": 600,
            "maxSpd": 3450,
        },
        "ecm0": {
            "st": 0,
            "prmSpd": 2500,
            "minSpd": 600,
            "maxSpd": 3450,
            "spdList": [
                {"name": "Pool Filtration", "speed": 2600, "app": "BD1_F", "ar": 1},
                {"name": "Spa Filtration", "speed": 2525, "app": "BD2_F", "ar": 2},
                {"name": "Waterfall", "speed": 2850, "app": "WF", "ar": 3},
            ],
        },
        "fcr0": {"fr": "Waterfall", "et": "FRLY", "app": "WF", "st": 0},
    }

    class FakeWebSocket:
        closed = False

        def __init__(self) -> None:
            self.messages: list[dict[str, object]] = []

        async def send_json(self, message: dict[str, object]) -> None:
            self.messages.append(message)
            desired = message["payload"]["state"]["desired"]
            if "pool" in desired:
                client.reported["pool"]["st"] = 1
                client.reported["ecm0"].update({"st": 1, "reqSpd": 2600, "cmdSpd": 2500})
            if "filt0" in desired:
                client.reported["filt0"]["manSpd"] = desired["filt0"]["manSpd"]
            client._resolve_pending_control()

    websocket = FakeWebSocket()
    client._ws = websocket  # type: ignore[assignment]
    monkeypatch.setattr(api, "POST_PRIME_SYNC_INTERVAL", 0)

    async def run_scenario() -> None:
        await client.async_start_pump_at_speed(2600)
        assert [message["payload"]["state"]["desired"] for message in websocket.messages] == [
            {"pool": {"st": 1}}
        ]

        # Let the deferred task observe the distinct 2500 RPM priming state.
        for _ in range(10):
            context = client._post_prime_sync_context
            if context is not None and context.priming_observed:
                break
            await asyncio.sleep(0)
        assert context is not None
        assert context.priming_observed is True

        # TCX can restore an older manual value as priming ends even though
        # the persistent Pool Filtration preset already contains the target.
        client.reported["filt0"]["manSpd"] = 2575
        client.reported["ecm0"].update({"reqSpd": 2575, "cmdSpd": 2575})
        task = client._post_prime_sync_task
        assert task is not None
        await task

    asyncio.run(run_scenario())

    desired_frames = [message["payload"]["state"]["desired"] for message in websocket.messages]
    assert desired_frames == [
        {"pool": {"st": 1}},
        {"filt0": {"manSpd": 2600}},
    ]
    assert client.post_prime_sync_success_count == 1
    assert client.post_prime_sync_skip_count == 0
    assert client.last_post_prime_sync_result == "manual_speed_aligned"


def test_live_external_manual_speed_desired_cancels_matching_generation() -> None:
    client = post_prime_client("filt3")

    async def run_scenario() -> None:
        client._schedule_post_prime_sync(2600)
        task = client._post_prime_sync_task
        context = client._post_prime_sync_context
        assert task is not None
        assert context is not None
        assert context.filter_key == "filt3"

        client._handle_post_prime_desired(
            {"filt3": {"manSpd": 1800}},
            observed_at="external-command",
            device_timestamp=123,
        )
        await task

    asyncio.run(run_scenario())

    assert client.post_prime_sync_cancel_count == 1
    assert client.post_prime_sync_success_count == 0
    assert client.post_prime_sync_state == "cancelled"
    assert client.last_post_prime_sync_result == "external_manual_speed_commanded"
    assert client.last_post_prime_sync_external_override_rpm == 1800
    assert client.last_post_prime_sync_external_override_at == "external-command"
    assert client.recent_post_prime_transitions[-2]["decision"] == ("external_manual_speed_desired")
    assert client.recent_post_prime_transitions[-2]["device_timestamp"] == 123
    assert client.recent_post_prime_transitions[-1]["decision"] == (
        "cancelled:external_manual_speed_commanded"
    )


def test_post_prime_desired_requires_exact_dynamic_key_and_conflicting_speed() -> None:
    client = post_prime_client("filt3")

    async def run_scenario() -> None:
        client._schedule_post_prime_sync(2600)
        context = client._post_prime_sync_context
        assert context is not None

        client._handle_post_prime_desired({"pool": {"st": 1}})
        client._handle_post_prime_desired({"filt0": {"manSpd": 1800}})
        client._handle_post_prime_desired({"other": {"manSpd": 1800}})
        client._handle_post_prime_desired({"filt3": {"manSpd": 2600}})

        assert context.override_event.is_set() is False
        assert client.post_prime_sync_state == "waiting"
        await client._async_cancel_post_prime_sync("test_complete")

    asyncio.run(run_scenario())

    decisions = [item["decision"] for item in client.recent_post_prime_transitions]
    assert "matching_manual_speed_desired" in decisions
    assert "external_manual_speed_desired" not in decisions


def test_retained_desired_history_is_not_used_to_cancel_post_prime_sync() -> None:
    client = post_prime_client()
    data = {"service": "StateStreamer", "event": "StateReported"}
    client._record_desired_payload(data, {"timestamp": 1}, {"filt0": {"manSpd": 1800}})

    async def run_scenario() -> None:
        client._schedule_post_prime_sync(2600)
        context = client._post_prime_sync_context
        assert context is not None
        await asyncio.sleep(0)
        assert context.override_event.is_set() is False
        assert client.post_prime_sync_state == "waiting"
        await client._async_cancel_post_prime_sync("test_complete")

    asyncio.run(run_scenario())


def test_superseded_post_prime_generation_cannot_cancel_replacement() -> None:
    client = post_prime_client()

    async def run_scenario() -> None:
        client._schedule_post_prime_sync(2600)
        old_context = client._post_prime_sync_context
        assert old_context is not None
        await client._async_cancel_post_prime_sync("superseded_by_schedule")

        client._schedule_post_prime_sync(2700)
        new_context = client._post_prime_sync_context
        new_task = client._post_prime_sync_task
        assert new_context is not None
        assert new_task is not None
        assert new_context.generation == old_context.generation + 1

        old_context.override_event.set()
        await asyncio.sleep(0)
        assert client._post_prime_sync_context is new_context
        assert new_context.override_event.is_set() is False
        assert new_task.done() is False
        await client._async_cancel_post_prime_sync("test_complete")

    asyncio.run(run_scenario())


def test_post_prime_transition_history_is_bounded_and_deduplicated() -> None:
    client = post_prime_client()

    async def run_scenario() -> None:
        client._schedule_post_prime_sync(2600)
        context = client._post_prime_sync_context
        assert context is not None
        client._recent_post_prime_transitions.clear()

        client._record_post_prime_transition(context, "unchanged", observed_at="first")
        client._record_post_prime_transition(context, "unchanged", observed_at="second")
        records = client.recent_post_prime_transitions
        assert len(records) == 1
        assert records[0]["count"] == 2
        assert records[0]["first_seen"] == "first"
        assert records[0]["last_seen"] == "second"
        assert records[0]["filter_manual_rpm"] == 2575
        assert records[0]["commanded_rpm"] == 2500
        assert records[0]["phase"] == "Priming"

        for index in range(25):
            client._record_post_prime_transition(context, f"decision-{index}")
        assert len(client.recent_post_prime_transitions) == 20
        assert client.recent_post_prime_transitions[0]["decision"] == "decision-5"
        await client._async_cancel_post_prime_sync("test_complete")

    asyncio.run(run_scenario())


def test_manual_speed_command_cancels_pending_post_prime_sync(monkeypatch) -> None:
    client = make_client()
    client.user_id = "12345"
    client.websocket_connected = True
    client.reported = {
        "pool": {"fr": "Pool Filtration", "et": "V_POS", "app": "POOL_M", "st": 0},
        "filt0": {
            "fr": "Filtration",
            "et": "F_CTRL",
            "app": "FILT",
            "manSpd": 1100,
            "minSpd": 600,
            "maxSpd": 3450,
        },
        "ecm0": {
            "st": 0,
            "prmSpd": 2500,
            "minSpd": 600,
            "maxSpd": 3450,
            "spdList": [{"name": "Pool Filtration", "speed": 2600, "app": "BD1_F", "ar": 1}],
        },
    }

    class FakeWebSocket:
        closed = False

        def __init__(self) -> None:
            self.messages: list[dict[str, object]] = []

        async def send_json(self, message: dict[str, object]) -> None:
            self.messages.append(message)
            desired = message["payload"]["state"]["desired"]
            if "pool" in desired:
                client.reported["pool"]["st"] = 1
                client.reported["ecm0"].update({"st": 1, "reqSpd": 2600, "cmdSpd": 2500})
            if "filt0" in desired:
                client.reported["filt0"]["manSpd"] = desired["filt0"]["manSpd"]
            client._resolve_pending_control()

    websocket = FakeWebSocket()
    client._ws = websocket  # type: ignore[assignment]
    monkeypatch.setattr(api, "POST_PRIME_SYNC_INTERVAL", 0)

    async def run_scenario() -> None:
        await client.async_start_pump_at_speed(2600)
        await client.async_set_pump_speed(1800)

    asyncio.run(run_scenario())

    desired_frames = [message["payload"]["state"]["desired"] for message in websocket.messages]
    assert desired_frames == [
        {"pool": {"st": 1}},
        {"filt0": {"manSpd": 1800}},
    ]
    assert client.post_prime_sync_cancel_count == 1
    assert client.post_prime_sync_success_count == 0
    assert client.last_post_prime_sync_result == "manual_speed_commanded"


def test_schedule_refresh_during_priming_defers_manual_speed(monkeypatch) -> None:
    client = make_client()
    client.user_id = "12345"
    client.websocket_connected = True
    client.reported = {
        "pool": {"fr": "Pool Filtration", "et": "V_POS", "app": "POOL_M", "st": 1},
        "filt0": {
            "fr": "Filtration",
            "et": "F_CTRL",
            "app": "FILT",
            "manSpd": 1100,
            "minSpd": 600,
            "maxSpd": 3450,
        },
        "ecm0": {
            "st": 1,
            "reqSpd": 2575,
            "cmdSpd": 2500,
            "minSpd": 600,
            "maxSpd": 3450,
            "spdList": [
                {"name": "Pool Filtration", "speed": 2575, "app": "BD1_F", "ar": 1},
                {"name": "Spa Filtration", "speed": 2525, "app": "BD2_F", "ar": 2},
                {"name": "Waterfall", "speed": 2850, "app": "WF", "ar": 3},
            ],
        },
        "fcr0": {"fr": "Waterfall", "et": "FRLY", "app": "WF", "st": 0},
    }

    class FakeWebSocket:
        closed = False

        def __init__(self) -> None:
            self.messages: list[dict[str, object]] = []

        async def send_json(self, message: dict[str, object]) -> None:
            self.messages.append(message)
            desired = message["payload"]["state"]["desired"]
            if "filt0" in desired:
                client.reported["filt0"]["manSpd"] = desired["filt0"]["manSpd"]
            client._resolve_pending_control()

    websocket = FakeWebSocket()
    client._ws = websocket  # type: ignore[assignment]
    monkeypatch.setattr(api, "POST_PRIME_SYNC_INTERVAL", 0)

    async def run_scenario() -> None:
        await client.async_start_pump_at_speed(2575)
        assert websocket.messages == []
        client.reported["ecm0"]["cmdSpd"] = 2575
        task = client._post_prime_sync_task
        assert task is not None
        await task

    asyncio.run(run_scenario())

    assert [message["payload"]["state"]["desired"] for message in websocket.messages] == [
        {"filt0": {"manSpd": 2575}}
    ]
    assert client.post_prime_sync_success_count == 1


def test_pool_preset_timeout_recovers_from_fresh_shadow_before_power(monkeypatch) -> None:
    client = make_client()
    client.user_id = "12345"
    client.websocket_connected = True
    client.reported = {
        "pool": {"fr": "Pool Filtration", "et": "V_POS", "app": "POOL_M", "st": 0},
        "filt0": {
            "fr": "Filtration",
            "et": "F_CTRL",
            "app": "FILT",
            "manSpd": 1100,
            "minSpd": 600,
            "maxSpd": 3450,
        },
        "ecm0": {
            "st": 0,
            "minSpd": 600,
            "maxSpd": 3450,
            "spdList": [
                {"name": "Pool Filtration", "speed": 1100, "app": "BD1_F", "ar": 1},
                {"name": "Spa Filtration", "speed": 2525, "app": "BD2_F", "ar": 2},
                {"name": "Waterfall", "speed": 2850, "app": "WF", "ar": 3},
            ],
        },
    }

    class FakeWebSocket:
        closed = False

        def __init__(self) -> None:
            self.messages: list[dict[str, object]] = []

        async def send_json(self, message: dict[str, object]) -> None:
            self.messages.append(message)
            desired = message["payload"]["state"]["desired"]
            if "pool" in desired:
                client.reported["pool"]["st"] = 1
                client.reported["ecm0"].update({"st": 1, "reqSpd": 2600, "cmdSpd": 2500})
                client._resolve_pending_control()

    websocket = FakeWebSocket()
    client._ws = websocket  # type: ignore[assignment]
    monkeypatch.setattr(api, "POOL_FILTRATION_CONFIRM_TIMEOUT", 0.001)

    async def fake_shadow() -> dict[str, object]:
        desired = websocket.messages[0]["payload"]["state"]["desired"]
        client.reported["ecm0"]["spdList"] = desired["ecm0"]["spdList"]
        return {}

    client.async_get_shadow = fake_shadow  # type: ignore[method-assign]

    async def run_scenario() -> None:
        await client.async_start_pump_at_speed(2600)
        await client._async_cancel_post_prime_sync("test_complete")

    asyncio.run(run_scenario())

    desired_frames = [message["payload"]["state"]["desired"] for message in websocket.messages]
    assert desired_frames[-1] == {"pool": {"st": 1}}
    assert client.control_confirmation_refresh_count == 1
    assert client.control_late_confirmation_count == 1
    assert client.control_failure_count == 0


def test_start_pump_at_speed_enforces_reported_limits() -> None:
    client = make_client()
    client.user_id = "12345"
    client.websocket_connected = True
    client.reported = {
        "pool": {"fr": "Pool Filtration", "et": "V_POS", "app": "POOL_M", "st": 0},
        "filt0": {
            "fr": "Filtration",
            "et": "F_CTRL",
            "app": "FILT",
            "manSpd": 1100,
            "minSpd": 600,
            "maxSpd": 3450,
        },
        "ecm0": {
            "st": 0,
            "minSpd": 600,
            "maxSpd": 3450,
            "spdList": [
                {
                    "name": "Pool Filtration",
                    "speed": 1100,
                    "app": "BD1_F",
                    "ar": 1,
                }
            ],
        },
    }

    with pytest.raises(api.TCXControlUnsupported, match="between 600 and 3450"):
        asyncio.run(client.async_start_pump_at_speed(4000))


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
    assert websocket.messages[0]["payload"]["state"]["desired"] == {"auxz4": {"st": 1}}
    assert client.control_command_counts["pool light state"] == 1
    assert client.control_success_counts["pool light state"] == 1


def test_pool_light_color_control_targets_cmdclr_and_waits_for_report() -> None:
    client = make_client()
    client.user_id = "12345"
    client.websocket_connected = True
    client.reported = {
        "systemMode": 1,
        "auxz4": {
            "fr": "Pool Light",
            "et": "JL",
            "app": "POOL_LT",
            "st": 1,
            "cmdClr": 3,
            "currClr": 3,
        },
    }

    class FakeWebSocket:
        closed = False

        def __init__(self) -> None:
            self.messages: list[dict[str, object]] = []

        async def send_json(self, message: dict[str, object]) -> None:
            self.messages.append(message)
            client.reported["auxz4"]["cmdClr"] = 12
            client._resolve_pending_control()

    websocket = FakeWebSocket()
    client._ws = websocket  # type: ignore[assignment]

    asyncio.run(client.async_set_pool_light_color(12))

    assert websocket.messages[0]["namespace"] == "tcx"
    assert websocket.messages[0]["payload"]["state"]["desired"] == {"auxz4": {"cmdClr": 12}}
    assert client.control_command_counts["pool light color"] == 1
    assert client.control_success_counts["pool light color"] == 1


def test_pool_light_color_control_requires_light_on() -> None:
    client = make_client()
    client.reported = {
        "systemMode": 1,
        "auxz0": {"et": "JL", "app": "POOL_LT", "st": 0, "cmdClr": 3},
    }

    with pytest.raises(api.TCXControlUnsupported, match="Turn on the pool light"):
        asyncio.run(client.async_set_pool_light_color(4))

    assert client.control_command_count == 0


def test_non_auto_mode_blocks_control_before_transmission() -> None:
    client = make_client()
    client.user_id = "12345"
    client.websocket_connected = True
    client.reported = {"systemMode": 3}

    class FakeWebSocket:
        closed = False

        def __init__(self) -> None:
            self.messages: list[dict[str, object]] = []

        async def send_json(self, message: dict[str, object]) -> None:
            self.messages.append(message)

    websocket = FakeWebSocket()
    client._ws = websocket  # type: ignore[assignment]

    with pytest.raises(api.TCXControlUnsupported, match="Service mode.*requires Auto mode"):
        asyncio.run(client._async_send_control({}, "test command", lambda _reported: True))

    assert websocket.messages == []
    assert client.control_command_count == 0
    assert client.control_failure_count == 0


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
    assert websocket.messages[0]["payload"]["state"]["desired"] == {"filt0": {"manSpd": 2250}}
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


def test_controller_mode_transitions_are_retained_and_deduplicated() -> None:
    client = make_client()

    client._record_controller_mode(
        {"systemMode": 1}, "shadow", observed_at="first", device_timestamp=10
    )
    client._record_controller_mode({"systemMode": 1}, "websocket", observed_at="duplicate")
    client._record_controller_mode(
        {"systemMode": 3}, "websocket", observed_at="second", device_timestamp=20
    )
    client._record_controller_mode({"systemMode": 5}, "websocket", observed_at="third")

    assert client.recent_controller_mode_transitions == [
        {
            "observed_at": "first",
            "source": "shadow",
            "code": 1,
            "mode": "Auto",
            "device_timestamp": 10,
        },
        {
            "observed_at": "second",
            "source": "websocket",
            "code": 3,
            "mode": "Service",
            "device_timestamp": 20,
        },
        {
            "observed_at": "third",
            "source": "websocket",
            "code": 5,
            "mode": "Transitioning",
        },
    ]


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
