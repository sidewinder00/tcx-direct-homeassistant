from __future__ import annotations

import ast
from pathlib import Path

ENTITY_FILES = (
    Path("custom_components/tcx_direct/sensor.py"),
    Path("custom_components/tcx_direct/binary_sensor.py"),
    Path("custom_components/tcx_direct/button.py"),
    Path("custom_components/tcx_direct/switch.py"),
    Path("custom_components/tcx_direct/number.py"),
    Path("custom_components/tcx_direct/select.py"),
)


def test_entity_categories_use_home_assistant_enum() -> None:
    """Guard against strings rejected by Home Assistant's entity registry."""
    categories: list[tuple[Path, int, str]] = []

    for path in ENTITY_FILES:
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.keyword) or node.arg != "entity_category":
                continue
            categories.append((path, node.lineno, ast.unparse(node.value)))

    assert len(categories) == 22
    assert all(
        expression == "EntityCategory.DIAGNOSTIC" for _path, _line, expression in categories
    ), categories


def test_status_points_and_writable_controls_remain_separate() -> None:
    binary_sensor = Path("custom_components/tcx_direct/binary_sensor.py").read_text()
    switch = Path("custom_components/tcx_direct/switch.py").read_text()
    number = Path("custom_components/tcx_direct/number.py").read_text()
    select = Path("custom_components/tcx_direct/select.py").read_text()

    assert 'key="pump"' in binary_sensor
    assert 'key="light"' in binary_sensor
    assert 'key="waterfall_status"' in binary_sensor
    assert 'key="live_data"' in binary_sensor
    sensor = Path("custom_components/tcx_direct/sensor.py").read_text()
    assert 'key="controller_mode"' in sensor
    assert 'key="pump_operating_phase"' in sensor
    assert 'key="pump_requested_rpm"' in sensor
    assert 'key="control_status"' in sensor
    assert 'key="freeze_protection_setpoint"' in sensor
    assert 'key="last_reported_equipment_state"' in sensor
    assert 'key="integration_version"' in sensor
    assert "TCXIntegrationVersionSensor" in sensor
    assert '}_integration_version"' in sensor
    assert "TCXPumpPowerSwitch" in switch
    assert "TCXPoolLightSwitch" in switch
    assert "TCXWaterfallSwitch" in switch
    assert "TCXPumpSpeedNumber" in number
    assert "TCXPoolFiltrationPresetNumber" in number
    assert "TCXWaterfallRPMNumber" in number
    assert "TCXPoolLightColorSelect" in select
    assert '_attr_name = "Pump Manual Speed"' in number
    assert '_attr_name = "Pool Filtration Preset"' in number
    assert '_attr_name = "Waterfall RPM"' in number
    assert '_attr_name = "Pool Light Color"' in select
