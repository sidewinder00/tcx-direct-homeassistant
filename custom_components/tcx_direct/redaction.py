from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

REDACTED = "**REDACTED**"

SENSITIVE_NORMALIZED_KEYS = {
    "authenticationtoken",
    "clienttoken",
    "deviceid",
    "ei",
    "email",
    "equipmentid",
    "euid",
    "idtoken",
    "latitude",
    "longitude",
    "macaddr",
    "ni",
    "password",
    "refreshtoken",
    "serial",
    "serialnumber",
    "sessionid",
    "sn",
    "target",
    "userid",
    "username",
}

_HEX_IDENTIFIER = re.compile(r"(?i)^[0-9a-f]{8,}$")
_UUID_IDENTIFIER = re.compile(
    r"(?i)^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


def normalize_key(value: Any) -> str:
    return "".join(ch for ch in str(value).casefold() if ch.isalnum())


def sanitize_diagnostics(value: Any) -> Any:
    """Recursively redact sensitive identifiers while preserving JSON shape."""
    if isinstance(value, dict):
        output: dict[str, Any] = {}
        for key, item in value.items():
            if normalize_key(key) in SENSITIVE_NORMALIZED_KEYS:
                output[str(key)] = REDACTED
            else:
                output[str(key)] = sanitize_diagnostics(item)
        return output
    if isinstance(value, list):
        return [sanitize_diagnostics(item) for item in value]
    return deepcopy(value)


def safe_structure_key(value: Any) -> str:
    """Hide identifier-shaped dynamic JSON keys in schema diagnostics."""
    text = str(value)
    if (
        _HEX_IDENTIFIER.fullmatch(text)
        or _UUID_IDENTIFIER.fullmatch(text)
        or "@" in text
    ):
        return "<redacted-key>"
    return text
