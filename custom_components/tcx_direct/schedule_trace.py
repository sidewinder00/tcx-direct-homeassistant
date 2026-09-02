"""Bounded, passive schedule evidence. Never used to authorize or send a command."""

from __future__ import annotations

import json
import math
from collections import deque
from copy import deepcopy
from datetime import datetime, timezone
from functools import wraps
from typing import Any

from .redaction import REDACTED, SENSITIVE_NORMALIZED_KEYS, normalize_key, safe_structure_key

TRACE_EVENTS = 20
TRACE_CHANGES = 20
FRAGMENT_BYTES = 2048
FRAGMENT_NODES = 256
MAX_DEPTH = 8
MAX_ITEMS = 32
MAX_STRING = 160
_MISSING = object()
_PRIVATE_KEYS = SENSITIVE_NORMALIZED_KEYS | {
    "id",
    "name",
    "label",
    "description",
    "authorization",
    "token",
    "apikey",
    "accesstoken",
    "cookie",
    "ssid",
    "ip",
    "ipaddress",
}


def _passive(method):
    """Bad diagnostic input must not break telemetry, confirmation, or controls."""

    @wraps(method)
    def capture(self, *args, **kwargs):
        try:
            return method(self, *args, **kwargs)
        except Exception:
            # Do not log exception text: it can include raw protocol data.
            self.capture_errors += 1
            return None

    return capture


def _mapping(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _fragment(value: Any) -> dict[str, Any]:
    """Distinguish missing/null/empty, and redact before retaining any values."""
    if value is _MISSING:
        return {"presence": "missing"}
    truncated = False
    nodes = 0

    def visit(item, depth=0):
        nonlocal nodes, truncated
        nodes += 1
        if nodes > FRAGMENT_NODES or depth > MAX_DEPTH:
            truncated = True
            return "<capture-limit>"
        if isinstance(item, dict):
            result = {}
            for index, (key, child) in enumerate(item.items()):
                if index >= MAX_ITEMS or nodes >= FRAGMENT_NODES:
                    truncated = True
                    break
                safe_key = safe_structure_key(key)
                if safe_key != str(key):
                    # Do not expose values beneath an identifier-shaped key either.
                    result[f"<redacted-key-{index}>"] = REDACTED
                elif len(safe_key) > MAX_STRING:
                    result[f"<omitted-key-{index}>"] = REDACTED
                    truncated = True
                else:
                    result[safe_key] = (
                        REDACTED if normalize_key(key) in _PRIVATE_KEYS else visit(child, depth + 1)
                    )
            return result
        if isinstance(item, list):
            result = []
            for index, child in enumerate(item):
                if index >= MAX_ITEMS or nodes >= FRAGMENT_NODES:
                    truncated = True
                    break
                result.append(visit(child, depth + 1))
            return result
        if isinstance(item, str):
            if len(item) > MAX_STRING:
                truncated = True
                return "<overlong-string>"
            return item
        if item is None or type(item) in (bool, int):
            if type(item) is int and item.bit_length() > 64:
                truncated = True
                return "<overlong-number>"
            return item
        if type(item) is float and math.isfinite(item):
            return item
        truncated = True
        return "<unsupported-value>"

    presence = (
        "null"
        if value is None
        else "object"
        if isinstance(value, dict)
        else "list"
        if isinstance(value, list)
        else "scalar"
    )
    result = visit(value)
    if len(json.dumps(result, ensure_ascii=True).encode()) > FRAGMENT_BYTES:
        result = "<fragment-size-limit>"
        truncated = True
    return {"presence": presence, "value": result, "truncated": truncated}


def _section(document: Any) -> dict[str, Any]:
    doc = _mapping(document)
    state = _mapping(doc.get("state"))
    metadata = _mapping(doc.get("metadata"))
    return {
        "presence": "missing"
        if document is _MISSING
        else "object"
        if isinstance(document, dict)
        else "null"
        if document is None
        else "invalid",
        "desired_sh": _fragment(_mapping(state.get("desired")).get("sh", _MISSING)),
        "reported_sh": _fragment(_mapping(state.get("reported")).get("sh", _MISSING)),
        "desired_sh_metadata": _fragment(_mapping(metadata.get("desired")).get("sh", _MISSING)),
        "reported_sh_metadata": _fragment(_mapping(metadata.get("reported")).get("sh", _MISSING)),
        "shadow_version": _fragment(doc.get("version", _MISSING)),
        "shadow_timestamp": _fragment(doc.get("timestamp", _MISSING)),
        "client_token_present": "clientToken" in doc,
    }


class NativeScheduleTrace:
    """Own no client/transport handles; retain redacted copies in memory only."""

    def __init__(self):
        self.capture_errors = 0
        self._sequence = 0
        self._events: deque[dict] = deque(maxlen=TRACE_EVENTS)
        self._changes: deque[dict] = deque(maxlen=TRACE_CHANGES)
        self._last_table: dict | None = None
        self._last_schedule_send: dict | None = None
        self._last_confirmed: dict | None = None
        self._last_authorization: dict | None = None
        self._last_rest: dict | None = None

    def _record(self, kind: str, **fields) -> dict:
        self._sequence += 1
        event = {
            "sequence": self._sequence,
            "at": datetime.now(timezone.utc).isoformat(),
            "kind": kind,
            **fields,
        }
        self._events.append(event)
        return event

    @_passive
    def received(self, data: Any, *, source: str, connection: int | None = None) -> None:
        """Capture raw documents before namespace flattening or reported merging."""
        root = _mapping(data)
        payload = _mapping(root.get("payload"))
        documents = {"root": data, "payload": root.get("payload", _MISSING)}
        # Keep namespace identity even if only metadata (or no sh at all) is present.
        for container_name, container in (("root", root), ("payload", payload)):
            for namespace in ("main", "sched"):
                documents[f"{container_name}.{namespace}"] = container.get(namespace, _MISSING)
        relevant = any(
            "sh" in _mapping(_mapping(_mapping(doc).get(group)).get(side))
            for doc in documents.values()
            for group in ("state", "metadata")
            for side in ("desired", "reported")
        )
        if source != "rest" and root.get("service") != "Authorization" and not relevant:
            return  # ordinary pump/temperature traffic must not crowd out schedule evidence
        event = self._record(
            "received",
            source=source,
            connection=connection,
            service=root.get("service")
            if root.get("service") in ("Authorization", "StateStreamer", "StateController")
            else "other_or_absent",
            event=root.get("event")
            if root.get("event") in ("StateReported", "StateAccepted", "StateRejected")
            else "other_or_absent",
            namespace=root.get("namespace")
            if root.get("namespace") in ("authorization", "main", "sched", "tcx")
            else "other_or_absent",
            documents={name: _section(value) for name, value in documents.items()},
        )
        if root.get("service") == "Authorization":
            self._last_authorization = deepcopy(event)
        if source == "rest":
            self._last_rest = deepcopy(event)

    @_passive
    def sending(self, frame: dict, *, connection: int, command_number: int | None = None) -> None:
        """A local send attempt, not proof of delivery or controller execution."""
        desired = _mapping(_mapping(_mapping(frame.get("payload")).get("state")).get("desired"))
        if frame.get("action") == "subscribe":
            self._record("subscription_send_attempt", connection=connection)
        elif "sh" in desired:
            event = self._record(
                "schedule_send_attempt",
                connection=connection,
                command_number=command_number,
                namespace=frame.get("namespace")
                if frame.get("namespace") in ("tcx", "main", "sched")
                else "other_or_absent",
                desired_sh=_fragment(desired["sh"]),
                client_token_present="clientToken" in _mapping(frame.get("payload")),
            )
            self._last_schedule_send = deepcopy(event)

    @_passive
    def operation(self, state: str, plan_id: str, operation: str) -> None:
        event = self._record("operation", state=state, plan_id=plan_id, operation=operation)
        if state == "confirmed":
            self._last_confirmed = deepcopy(event)

    @_passive
    def reported_table(
        self, table: Any, *, source: str, full: bool, connection: int | None = None
    ) -> None:
        """Track observed merged-table changes, not inferred intent or execution."""
        captured = _fragment(table)
        if captured["presence"] != "object" or captured["truncated"]:
            self._last_table = None  # never claim a complete diff from a partial capture
            self._record(
                "table_diff_unavailable",
                source=source,
                full=full,
                connection=connection,
                table=captured,
            )
            return
        current = captured["value"]
        previous = self._last_table
        if current == previous:
            return
        self._last_table = deepcopy(current)
        event = self._record(
            "reported_table_change" if previous is not None else "reported_table_baseline",
            source=source,
            full=full,
            connection=connection,
            added=sorted(set(current) - set(previous)) if previous is not None else [],
            removed=sorted(set(previous) - set(current)) if previous is not None else [],
            changed=sorted(k for k in current if k in previous and current[k] != previous[k])
            if previous is not None
            else [],
            table=captured,
            last_confirmed_operation=deepcopy(self._last_confirmed),
        )
        self._changes.append(deepcopy(event))

    def snapshot(self) -> dict[str, Any]:
        return deepcopy(
            {
                "schema_version": 1,
                "scope": "current_client_session_only",
                "capture_errors": self.capture_errors,
                "limits": {
                    "events": TRACE_EVENTS,
                    "reported_changes": TRACE_CHANGES,
                    "fragment_bytes": FRAGMENT_BYTES,
                    "fragment_nodes": FRAGMENT_NODES,
                    "container_items": MAX_ITEMS,
                    "depth": MAX_DEPTH,
                    "string_characters": MAX_STRING,
                },
                "total_events": self._sequence,
                "events": list(self._events),
                "reported_changes": list(self._changes),
                "last_schedule_send_attempt": self._last_schedule_send,
                "last_confirmed_operation": self._last_confirmed,
                "last_authorization_snapshot": self._last_authorization,
                "last_rest_response": self._last_rest,
            }
        )
