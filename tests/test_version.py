from __future__ import annotations

from pathlib import Path

import pytest

from custom_components.tcx_direct.const import VERSION, VERSION_CODE, encode_version_code


def test_current_version_has_expected_sortable_code() -> None:
    assert VERSION == "0.2.11"
    assert VERSION_CODE == 2011


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        ("0.2.9", 2009),
        ("0.2.10", 2010),
        ("1.0.0", 1_000_000),
        ("2.15.123", 2_015_123),
    ],
)
def test_version_code_is_monotonically_sortable(version: str, expected: int) -> None:
    assert encode_version_code(version) == expected


@pytest.mark.parametrize(
    "version",
    ["0.2", "0.2.10.1", "v0.2.10", "0.two.10", "0.-1.0", "0.1000.0", "0.0.1000"],
)
def test_version_code_rejects_ambiguous_or_unsupported_versions(version: str) -> None:
    with pytest.raises(ValueError):
        encode_version_code(version)


def test_version_is_exposed_by_sensor_and_diagnostics() -> None:
    sensor = Path("custom_components/tcx_direct/sensor.py").read_text()
    diagnostics = Path("custom_components/tcx_direct/diagnostics.py").read_text()

    assert "_attr_native_value = VERSION" in sensor
    assert 'return {"version_code": VERSION_CODE}' in sensor
    assert '"version": VERSION' in diagnostics
    assert '"version_code": VERSION_CODE' in diagnostics
