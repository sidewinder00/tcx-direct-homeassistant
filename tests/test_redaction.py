from __future__ import annotations

from custom_components.tcx_direct.redaction import (
    REDACTED,
    safe_structure_key,
    sanitize_diagnostics,
)


def test_sensitive_tcx_identifiers_are_redacted() -> None:
    payload = {
        "zig": {"euid": "F4CE36BBBF99AF99"},
        "auxz0": {"ei": "BC33ACFFFED21F3E", "ni": "3ECD", "st": 0},
        "equipmentId": "controller-1",
        "water": {"value": 317},
    }

    sanitized = sanitize_diagnostics(payload)

    assert sanitized["zig"]["euid"] == REDACTED
    assert sanitized["auxz0"]["ei"] == REDACTED
    assert sanitized["auxz0"]["ni"] == REDACTED
    assert sanitized["equipmentId"] == REDACTED
    assert sanitized["water"]["value"] == 317


def test_identifier_shaped_structure_keys_are_hidden() -> None:
    assert safe_structure_key("F4CE36BBBF99AF99") == "<redacted-key>"
    assert (
        safe_structure_key("550e8400-e29b-41d4-a716-446655440000")
        == "<redacted-key>"
    )
    assert safe_structure_key("ecm0") == "ecm0"


def test_structure_paths_do_not_retain_dynamic_identifiers() -> None:
    from custom_components.tcx_direct.api import _structure_paths

    paths = _structure_paths({"F4CE36BBBF99AF99": {"value": 1}})

    assert paths == ["<redacted-key>.value"]
