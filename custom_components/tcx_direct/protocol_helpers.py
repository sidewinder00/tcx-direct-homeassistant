"""Shared protocol parsing; no Home Assistant or transport dependencies."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


def _norm(text: str) -> str:
    return "".join(ch for ch in text.casefold() if ch.isalnum())


def _coerce_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = value.strip().replace("%", "")
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


def _numeric_code(value: Any) -> int | float | None:
    """Return a stable integer code when the reported number is integral."""
    number = _coerce_number(value)
    if number is None:
        return None
    return int(number) if number.is_integer() else number


def _find_equipment(
    reported: dict[str, Any], equipment_type: str, application: str
) -> list[tuple[str, dict[str, Any]]]:
    return [
        (str(key), value)
        for key, value in reported.items()
        if isinstance(value, dict)
        and _norm(str(value.get("et", ""))) == equipment_type
        and _norm(str(value.get("app", ""))) == application
    ]


def _find_pool_modes(reported: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    return _find_equipment(reported, "vpos", "poolm")


def _find_filter_controllers(reported: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    return _find_equipment(reported, "fctrl", "filt")


def _pump_speed_limits(
    reported: dict[str, Any], controller: dict[str, Any]
) -> tuple[float | None, float | None]:
    """Use motor limits first, with the existing per-field filter fallback."""
    motor = reported.get("ecm0")
    motor = motor if isinstance(motor, dict) else {}
    minimum = _coerce_number(motor.get("minSpd"))
    if minimum is None:
        minimum = _coerce_number(controller.get("minSpd"))
    maximum = _coerce_number(motor.get("maxSpd"))
    if maximum is None:
        maximum = _coerce_number(controller.get("maxSpd"))
    return minimum, maximum


def _deep_merge(target: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge source into target and return target."""
    for key, value in source.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_merge(target[key], value)
        else:
            target[key] = deepcopy(value)
    return target


def _collect_reported(data: Any) -> dict[str, Any] | None:
    """Merge all reported namespace documents from this particular response."""
    merged: dict[str, Any] = {}

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            state = value.get("state")
            if isinstance(state, dict) and isinstance(state.get("reported"), dict):
                _deep_merge(merged, state["reported"])
            for child in value.values():
                if isinstance(child, (dict, list)):
                    walk(child)
        elif isinstance(value, list):
            for child in value:
                if isinstance(child, (dict, list)):
                    walk(child)

    walk(data)
    return merged or None
