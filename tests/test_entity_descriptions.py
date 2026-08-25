from __future__ import annotations

import ast
from pathlib import Path

ENTITY_FILES = (
    Path("custom_components/tcx_direct/sensor.py"),
    Path("custom_components/tcx_direct/binary_sensor.py"),
    Path("custom_components/tcx_direct/button.py"),
    Path("custom_components/tcx_direct/switch.py"),
    Path("custom_components/tcx_direct/number.py"),
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

    assert len(categories) == 17
    assert all(
        expression == "EntityCategory.DIAGNOSTIC"
        for _path, _line, expression in categories
    ), categories


def test_status_points_and_writable_controls_remain_separate() -> None:
    binary_sensor = Path("custom_components/tcx_direct/binary_sensor.py").read_text()
    switch = Path("custom_components/tcx_direct/switch.py").read_text()
    number = Path("custom_components/tcx_direct/number.py").read_text()

    assert 'key="pump"' in binary_sensor
    assert 'key="waterfall_status"' in binary_sensor
    assert "TCXPumpPowerSwitch" in switch
    assert "TCXPoolLightSwitch" in switch
    assert "TCXWaterfallSwitch" in switch
    assert "TCXPumpSpeedNumber" in number
