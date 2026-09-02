"""Experimental native TCX schedules; no timetable execution or automatic writes.

Only the captured pool schedule format is writable. Preview/apply is deliberate:
plans are single-use, short-lived, tied to a freshly read table, and never replayed.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
import uuid
from collections import deque
from collections.abc import Awaitable, Callable
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from .redaction import sanitize_diagnostics

if TYPE_CHECKING:
    from .api import TCXClient

PLAN_TTL = 300
MAX_PLANS = 10
SCHEDULE_TIMEOUT = 45
OPERATIONS = ("create", "update", "enable", "disable", "delete")
_SLOT = re.compile(r"[1-9][0-9]*\Z")
_TIME = re.compile(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]\Z")


class ScheduleError(ValueError):
    """A schedule request cannot safely proceed."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def schedule_revision(table: dict[str, Any]) -> str:
    """Local optimistic-concurrency fingerprint, NOT a vendor CAS token."""
    return hashlib.sha256(json.dumps(table, sort_keys=True).encode()).hexdigest()


def _identity(entry: Any) -> tuple[Any, ...] | None:
    """Ignore labels, enable state and weekday ordering when detecting duplicates."""
    if not isinstance(entry, dict):
        return None
    try:
        days = tuple(_days(entry.get("dw")))
    except ScheduleError:
        return None
    return (entry.get("lc"), entry.get("on"), entry.get("of"), entry.get("ar"), days)


def _time(value: Any) -> str:
    if not isinstance(value, str) or not _TIME.fullmatch(value):
        raise ScheduleError("Times must use HH:MM (00:00 through 23:59)")
    return value


def _days(value: Any) -> list[int]:
    if (
        not isinstance(value, list)
        or not value
        or any(type(day) is not int or not 0 <= day <= 6 for day in value)
        or len(set(value)) != len(value)
    ):
        raise ScheduleError("weekday_codes must be distinct integers from 0 to 6")
    return sorted(value)


def _rpm(value: Any, minimum: Any, maximum: Any) -> int:
    if (
        type(value) is not int
        or type(minimum) not in (int, float)
        or type(maximum) not in (int, float)
        or not math.isfinite(minimum)
        or not math.isfinite(maximum)
        or not 0 < minimum <= value <= maximum
    ):
        raise ScheduleError("An explicit integer RPM within reported pump limits is required")
    return value


def describe_schedules(table: Any) -> list[dict[str, Any]]:
    """Expose unfamiliar schedules too, without guessing defaults or weekday names."""
    if not isinstance(table, dict):
        return []
    result = []
    for slot, entry in table.items():
        if entry is None:
            continue
        item: dict[str, Any] = {"schedule_id": slot, "raw": sanitize_diagnostics(entry)}
        if isinstance(entry, dict):
            item.update(
                name=entry.get("id"),
                equipment=entry.get("lc"),
                start=entry.get("on"),
                end=entry.get("of"),
                weekday_codes=entry.get("dw"),
                enabled={0: False, 1: True}.get(entry.get("en"))
                if type(entry.get("en")) is int
                else None,
                rpm=entry.get("ar") if type(entry.get("ar")) is int and entry["ar"] > 0 else None,
                speed_mode="explicit"
                if type(entry.get("ar")) is int and entry["ar"] > 0
                else "unconfirmed_default"
                if entry.get("ar") == 0
                else "unknown",
            )
        result.append(item)
    return result


@dataclass
class SchedulePlan:
    plan_id: str
    operation: str
    schedule_id: str | None
    before: dict[str, Any]
    after: dict[str, Any] | None
    created: float

    @property
    def desired(self) -> dict[str, Any]:
        return {
            "sh": {
                self.schedule_id or "add": "[DELETED]"
                if self.operation == "delete"
                else deepcopy(self.after)
            }
        }

    def response(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "operation": self.operation,
            "schedule_id": self.schedule_id,
            "revision": schedule_revision(self.before),
            "expires_in_seconds": max(0, int(PLAN_TTL - (time.monotonic() - self.created))),
            "before": sanitize_diagnostics(self.before.get(self.schedule_id)),
            "after": sanitize_diagnostics(self.after),
            "desired": sanitize_diagnostics(self.desired),
        }


class TCXSchedules:
    """Serialize schedule changes with equipment controls, backed by a durable latch."""

    def __init__(self, client: TCXClient) -> None:
        self.client = client
        self.writes_enabled = False
        self.pending: dict[str, Any] | None = None
        self._save: Callable[[dict[str, Any]], Awaitable[None]] | None = None
        self._plans: dict[str, SchedulePlan] = {}
        self._sequence = 0
        self._full_sequence = 0
        self._slot_sequence: dict[str, int] = {}
        self.last_observed_at: str | None = None
        self.storage_error: str | None = None
        self._history: deque[dict[str, Any]] = deque(maxlen=20)

    def configure_storage(
        self, saved: Any, save: Callable[[dict[str, Any]], Awaitable[None]]
    ) -> None:
        if saved is not None and (
            not isinstance(saved, dict)
            or "pending" not in saved
            or (
                saved["pending"] is not None
                and (
                    not isinstance(saved["pending"], dict)
                    or not isinstance(saved["pending"].get("plan_id"), str)
                    or not saved["pending"]["plan_id"]
                    or saved["pending"].get("operation") not in OPERATIONS
                    or saved["pending"].get("state") != "outcome_uncertain"
                )
            )
        ):
            raise ScheduleError("Invalid schedule journal; refusing to discard recovery state")
        self._save = save
        self.pending = deepcopy(saved.get("pending")) if saved else None

    def observe(self, reported: dict[str, Any], *, full: bool = False) -> None:
        """Called only on newly received reported data, never on cache load or desired echoes."""
        if "sh" not in reported:
            return
        table = reported["sh"]
        self._sequence += 1
        self.last_observed_at = _now()
        if isinstance(table, dict):
            keys = set(table)
            if full:
                keys |= set(self._slot_sequence)
                self._full_sequence += 1
                # Full snapshots replace schedule membership. A generic recursive
                # equipment merge would retain schedules absent after deletion.
                self.client.reported["sh"] = deepcopy(table)
            for key in keys:
                self._slot_sequence[key] = self._sequence

    def snapshot(self) -> dict[str, Any]:
        table = self.client.reported.get("sh")
        return {
            "available": isinstance(table, dict),
            "revision": schedule_revision(table) if isinstance(table, dict) else None,
            "schedules": describe_schedules(table),
            "last_observed_at": self.last_observed_at,
            "writes_enabled": self.writes_enabled,
            "storage_error": self.storage_error,
            "status": "storage_error"
            if self.storage_error
            else "unknown"
            if not isinstance(table, dict)
            else "needs_review"
            if self.pending
            else "ready"
            if self.writes_enabled
            else "read_only",
            "pending_write": sanitize_diagnostics(self.pending),
            "recent_operations": list(self._history),
        }

    def _record(self, state: str, plan_id: str, operation: str) -> None:
        self._history.append(
            {"at": _now(), "state": state, "plan_id": plan_id, "operation": operation}
        )

    async def _fresh(self) -> dict[str, Any]:
        previous = self._full_sequence
        await self.client.async_get_shadow()
        if self._full_sequence == previous:
            raise ScheduleError("Fresh REST shadow did not contain a complete schedule table")
        table = self.client.reported.get("sh")
        if not isinstance(table, dict):
            raise ScheduleError("TCX did not report a schedule table")
        await self.client._notify_state("shadow")
        return deepcopy(table)

    async def async_read(self) -> dict[str, Any]:
        async with self.client._control_lock:
            await self._fresh()
            return self.snapshot()

    def _pool_context(self) -> tuple[str, dict[str, Any]]:
        pools = [
            key
            for key, item in self.client.reported.items()
            if isinstance(item, dict) and item.get("et") == "V_POS" and item.get("app") == "POOL_M"
        ]
        filters = [
            item
            for item in self.client.reported.values()
            if isinstance(item, dict) and item.get("et") == "F_CTRL" and item.get("app") == "FILT"
        ]
        if len(pools) != 1 or len(filters) != 1:
            raise ScheduleError("A unique confirmed Pool Filtration controller is required")
        return pools[0], filters[0]

    def _validate_entry(self, entry: dict[str, Any], limits: dict[str, Any]) -> None:
        _rpm(entry.get("ar"), limits.get("minSpd"), limits.get("maxSpd"))
        _days(entry.get("dw"))
        for field in ("on", "of"):
            value = entry.get(field)
            if not isinstance(value, str) or not value.startswith("T="):
                raise ScheduleError("Only fixed clock-time schedules are writable")
            _time(value[2:])
        if type(entry.get("en")) is not int or entry["en"] not in (0, 1):
            raise ScheduleError("Unrecognized schedule enable value")
        if entry["on"] == entry["of"]:
            raise ScheduleError("Equal start and end times have unconfirmed semantics")
        if entry["en"] and entry["of"] <= entry["on"]:
            raise ScheduleError("Overnight schedules may be stored disabled, but not enabled yet")

    def _validate_overlap(
        self, table: dict[str, Any], slot: str | None, entry: dict[str, Any]
    ) -> None:
        if not entry["en"]:
            return
        for key, other in table.items():
            if key == slot or not isinstance(other, dict) or other.get("lc") != entry["lc"]:
                continue
            try:
                if type(other.get("en")) is not int or other["en"] not in (0, 1):
                    raise ScheduleError("Unrecognized schedule enable value")
                if other["en"] == 0:
                    continue
                days = _days(other.get("dw"))
                for field in ("on", "of"):
                    if not isinstance(other.get(field), str) or not other[field].startswith("T="):
                        raise ScheduleError("Unrecognized schedule time format")
                start = _time(other["on"][2:])
                end = _time(other["of"][2:])
            except (ScheduleError, KeyError, AttributeError):
                raise ScheduleError(
                    "An existing pool schedule cannot be checked for overlap"
                ) from None
            if end <= start:
                raise ScheduleError("Resolve existing enabled overnight schedules before enabling")
            if set(days) & set(entry["dw"]) and start < entry["of"][2:] and entry["on"][2:] < end:
                raise ScheduleError("Enabled pool schedules must not overlap")

    async def async_preview(
        self, operation: str, schedule_id: str | None = None, **changes: Any
    ) -> dict[str, Any]:
        async with self.client._control_lock:
            if operation not in OPERATIONS:
                raise ScheduleError("Unknown schedule operation")
            if self.pending:
                raise ScheduleError("Review the uncertain schedule write before planning changes")
            table = await self._fresh()
            pool_key, limits = self._pool_context()
            if operation == "create":
                if schedule_id is not None:
                    raise ScheduleError("TCX assigns the new schedule ID")
                after = {"lc": pool_key, "id": "Filter Pump", "en": 0}
            else:
                if not isinstance(schedule_id, str) or not _SLOT.fullmatch(schedule_id):
                    raise ScheduleError("A positive numbered schedule_id is required")
                existing = table.get(schedule_id)
                if not isinstance(existing, dict) or existing.get("lc") != pool_key:
                    raise ScheduleError("Only an existing Pool Filtration schedule may be changed")
                after = deepcopy(existing)
            allowed = {"start", "end", "weekday_codes", "rpm", "enabled"}
            if set(changes) - allowed or (operation not in ("create", "update") and changes):
                raise ScheduleError("Unexpected fields for this schedule operation")
            for key, value in changes.items():
                if key in ("start", "end"):
                    after["on" if key == "start" else "of"] = "T=" + _time(value)
                elif key == "weekday_codes":
                    after["dw"] = _days(value)
                elif key == "rpm":
                    after["ar"] = _rpm(value, limits.get("minSpd"), limits.get("maxSpd"))
                else:
                    if type(value) is not bool:
                        raise ScheduleError("enabled must be a boolean")
                    after["en"] = int(value)
            if operation in ("enable", "disable"):
                after["en"] = int(operation == "enable")
            if operation == "delete":
                after = None
            elif operation != "disable":
                self._validate_entry(after, limits)
                self._validate_overlap(table, schedule_id, after)
            if operation == "create" and any(
                _identity(value) == _identity(after) for value in table.values()
            ):
                raise ScheduleError("An identical schedule already exists; edit it instead")
            now = time.monotonic()
            self._plans = {
                key: plan for key, plan in self._plans.items() if now - plan.created < PLAN_TTL
            }
            if len(self._plans) >= MAX_PLANS:
                self._plans.pop(next(iter(self._plans)))
            plan = SchedulePlan(uuid.uuid4().hex, operation, schedule_id, table, after, now)
            self._plans[plan.plan_id] = plan
            return plan.response()

    def _require_writes(self) -> None:
        if not self.writes_enabled:
            raise ScheduleError("Experimental native schedule writes are disabled in options")
        if self.pending:
            raise ScheduleError("An earlier write needs review; it will not be retried")
        if self._save is None:
            raise ScheduleError("Durable schedule journal is unavailable")
        if (
            type(self.client.reported.get("systemMode")) is not int
            or self.client.reported["systemMode"] != 1
        ):
            raise ScheduleError("A confirmed Auto controller mode is required")
        if (
            self.client._stopping
            or not self.client.websocket_connected
            or self.client._ws is None
            or self.client._ws.closed
        ):
            raise ScheduleError("A connected TCX WebSocket is required")

    def _matching_slot(self, plan: SchedulePlan, sequence: int) -> str | None:
        table = self.client.reported.get("sh")
        if not isinstance(table, dict):
            return None
        if plan.operation == "create":
            matches = [
                key
                for key, value in table.items()
                if _SLOT.fullmatch(key)
                and plan.before.get(key) is None
                and value == plan.after
                and self._slot_sequence.get(key, 0) > sequence
            ]
            return matches[0] if len(matches) == 1 else None
        key = plan.schedule_id
        if self._slot_sequence.get(key, 0) > sequence and table.get(key) == plan.after:
            return key
        return None

    async def async_apply(self, plan_id: str) -> dict[str, Any]:
        async with self.client._control_lock:
            self._require_writes()
            plan = self._plans.pop(plan_id, None)  # single-use, including failures
            if plan is None or time.monotonic() - plan.created >= PLAN_TTL:
                raise ScheduleError(
                    "Preview expired, was already used, or belongs to another entry"
                )
            table = await self._fresh()
            self._require_writes()
            if table != plan.before:
                raise ScheduleError("Schedules changed since preview; request a new preview")
            pool_key, limits = self._pool_context()
            target = plan.after if plan.after is not None else table.get(plan.schedule_id)
            if not isinstance(target, dict) or target.get("lc") != pool_key:
                raise ScheduleError("Pool Filtration equipment changed since preview")
            if plan.after is not None and plan.operation != "disable":
                self._validate_entry(plan.after, limits)
            if plan.schedule_id is not None and table.get(plan.schedule_id) == plan.after:
                return {"result": "unchanged", **self.snapshot()}
            self.pending = {
                "plan_id": plan_id,
                "operation": plan.operation,
                "schedule_id": plan.schedule_id,
                "at": _now(),
                "desired": plan.desired,
                "state": "outcome_uncertain",
            }
            assert self._save is not None
            try:
                # Finish disk persistence before a frame can leave HA. Cancellation
                # while saving is conservative: leave the latch, send nothing.
                await self._save({"pending": deepcopy(self.pending)})
                if (
                    not self.writes_enabled
                    or type(self.client.reported.get("systemMode")) is not int
                    or self.client.reported.get("systemMode") != 1
                ):
                    raise ScheduleError("Write permission or controller mode changed")
                if (
                    self.client._stopping
                    or not self.client.websocket_connected
                    or self.client._ws is None
                    or self.client._ws.closed
                ):
                    raise ScheduleError("TCX connection changed while preparing the write")
                if time.monotonic() - plan.created >= PLAN_TTL:
                    raise ScheduleError("Preview expired while preparing the write")
                if self.client.reported.get("sh") != table:
                    raise ScheduleError("Schedules changed while preparing the write")
                sequence = self._sequence
                self._record("sending", plan_id, plan.operation)
                await self.client._async_send_control(
                    plan.desired,
                    f"native schedule {plan.operation}",
                    lambda reported: self._matching_slot(plan, sequence) is not None,
                    confirmation_timeout=SCHEDULE_TIMEOUT,
                )
                slot = self._matching_slot(plan, sequence)
                fresh = await self._fresh()
                if slot is None or self._matching_slot(plan, sequence) != slot:
                    raise ScheduleError("Schedule readback did not confirm the intended result")
                # No rollback: a concurrent app edit must not be silently overwritten.
                if {k: v for k, v in fresh.items() if k != slot} != {
                    k: v for k, v in plan.before.items() if k != slot
                }:
                    raise ScheduleError("Other schedules changed during the write; review required")
                await self._save({"pending": None})
                self.pending = None
                self._record("confirmed", plan_id, plan.operation)
                return {"result": "confirmed", "schedule_id": slot, **self.snapshot()}
            except BaseException:
                self._record("outcome_uncertain", plan_id, plan.operation)
                raise
            finally:
                await self.client._notify_status()

    async def async_acknowledge(self, plan_id: str, revision: str) -> dict[str, Any]:
        """Clear the latch only after explicit review; never send a controller command."""
        async with self.client._control_lock:
            if not self.pending or self.pending.get("plan_id") != plan_id:
                raise ScheduleError("No matching uncertain write")
            table = await self._fresh()
            if schedule_revision(table) != revision:
                raise ScheduleError("Schedules changed since review; read them again")
            if self._save is None:
                raise ScheduleError("Durable schedule journal is unavailable")
            await self._save({"pending": None})
            self.pending = None
            self._record("review_acknowledged", plan_id, "review")
            await self.client._notify_status()
            return self.snapshot()
