"""Experimental native TCX schedules; no timetable execution or automatic writes.

Only the captured pool schedule format is writable. Preview/apply is deliberate:
plans are single-use, short-lived, tied to a freshly read table, and never replayed.
"""

from __future__ import annotations

import asyncio
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

import aiohttp

from .protocol_helpers import (
    _coerce_number,
    _collect_reported,
    _find_filter_controllers,
    _find_pool_modes,
    _numeric_code,
    _pump_speed_limits,
)
from .redaction import sanitize_diagnostics

if TYPE_CHECKING:
    from .api import TCXClient

PLAN_TTL = 300
MAX_PLANS = 10
SCHEDULE_TIMEOUT = 45
WS_SNAPSHOT_TIMEOUT = 20
SCHEDULE_SNAPSHOT_SOURCE = "websocket_authorization"
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
    minimum, maximum = _coerce_number(minimum), _coerce_number(maximum)
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
            "snapshot_source": SCHEDULE_SNAPSHOT_SOURCE,
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
        self._rest_sequence = 0
        self._authorization_sequence = 0
        self._snapshot_request: tuple[Any, asyncio.Future[dict[str, Any]]] | None = None
        self._slot_sequence: dict[str, int] = {}
        self.last_observed_at: str | None = None
        self.storage_error: str | None = None
        self._history: deque[dict[str, Any]] = deque(maxlen=20)
        self.last_acknowledgement: dict[str, Any] | None = None

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
        acknowledgement = saved.get("last_acknowledgement") if saved else None
        if acknowledgement is not None and (
            not isinstance(acknowledgement, dict)
            or acknowledgement.get("source") not in ("rest", "websocket_authorization")
            or not isinstance(acknowledgement.get("revision"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", acknowledgement["revision"])
            or not all(
                isinstance(acknowledgement.get(key), str) and acknowledgement[key]
                for key in ("plan_id", "at")
            )
        ):
            raise ScheduleError("Invalid acknowledgement in schedule journal")
        self._save = save
        self.pending = deepcopy(saved.get("pending")) if saved else None
        self.last_acknowledgement = deepcopy(acknowledgement)

    def _journal(self, pending: dict[str, Any] | None) -> dict[str, Any]:
        result = {"pending": deepcopy(pending)}
        if self.last_acknowledgement is not None:
            result["last_acknowledgement"] = deepcopy(self.last_acknowledgement)
        return result

    def observe(
        self,
        reported: dict[str, Any],
        *,
        full: bool = False,
        source: str = "websocket",
        websocket: Any = None,
    ) -> None:
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
                if source == "rest":
                    self._rest_sequence += 1
                elif source == "websocket_authorization":
                    self._authorization_sequence += 1
                # Full snapshots replace schedule membership. A generic recursive
                # equipment merge would retain schedules absent after deletion.
                self.client.reported["sh"] = deepcopy(table)
            for key in keys:
                self._slot_sequence[key] = self._sequence
            request = self._snapshot_request
            if (
                full
                and source == "websocket_authorization"
                and request is not None
                and request[0] is websocket
                and self.client._ws is websocket
                and not request[1].done()
            ):
                request[1].set_result(deepcopy(table))

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
            "last_acknowledgement": sanitize_diagnostics(self.last_acknowledgement),
            "snapshot_counts": {
                "rest": self._rest_sequence,
                "websocket_authorization": self._authorization_sequence,
            },
            "recent_operations": list(self._history),
        }

    def _record(self, state: str, plan_id: str, operation: str) -> None:
        self._history.append(
            {"at": _now(), "state": state, "plan_id": plan_id, "operation": operation}
        )

    async def _fresh_rest(self) -> dict[str, Any]:
        """Explicit REST reads still require a table in this particular response."""
        response = await self.client.async_get_shadow()
        reported = _collect_reported(response)
        if reported is None or not isinstance(reported.get("sh"), dict):
            raise ScheduleError("Fresh REST shadow did not contain a complete schedule table")
        table = deepcopy(reported["sh"])
        await self.client._notify_state("shadow")
        if self.client.reported.get("sh") != table:
            raise ScheduleError("Schedules changed while reading the REST snapshot; read again")
        return table

    async def _fresh_websocket(self) -> dict[str, Any]:
        """Request a new complete schedule snapshot; never authorize from cached data."""
        self._require_connection()
        ws = self.client._ws
        if not self.client.device_id or self.client.user_id is None:
            raise ScheduleError("TCX identity is unavailable for a schedule snapshot")
        future = asyncio.get_running_loop().create_future()
        request = (ws, future)
        self._snapshot_request = request
        try:
            async with asyncio.timeout(WS_SNAPSHOT_TIMEOUT):
                await self.client._send_authorization_subscribe(ws)
                table = await future
            self._require_connection()
            if self.client._ws is not ws:
                raise ScheduleError("TCX connection changed during the schedule snapshot")
            await self.client._notify_state("websocket")
            self._require_connection()
            if self.client._ws is not ws:
                raise ScheduleError("TCX connection changed during the schedule snapshot")
            if self.client.reported.get("sh") != table:
                raise ScheduleError("Schedules changed during the snapshot; read again")
            return table
        except TimeoutError as err:
            raise ScheduleError(
                "No new complete WebSocket Authorization schedule snapshot"
            ) from err
        except (aiohttp.ClientError, ConnectionError, RuntimeError) as err:
            raise ScheduleError("Unable to obtain a WebSocket schedule snapshot") from err
        finally:
            if self._snapshot_request is request:
                self._snapshot_request = None
            if not future.done():
                future.cancel()

    async def _read_snapshot(self, source: str) -> dict[str, Any]:
        if source == "rest":
            return await self._fresh_rest()
        if source == "websocket_authorization":
            return await self._fresh_websocket()
        raise ScheduleError("Unknown schedule snapshot source")

    async def async_read(self, source: str = SCHEDULE_SNAPSHOT_SOURCE) -> dict[str, Any]:
        async with self.client._control_lock:
            table = await self._read_snapshot(source)
            return {
                **self.snapshot(),
                "revision": schedule_revision(table),
                "schedules": describe_schedules(table),
                "snapshot_source": source,
            }

    def _pool_context(self) -> tuple[str, dict[str, Any]]:
        pools = _find_pool_modes(self.client.reported)
        filters = _find_filter_controllers(self.client.reported)
        if len(pools) != 1 or len(filters) != 1:
            raise ScheduleError("A unique confirmed Pool Filtration controller is required")
        minimum, maximum = _pump_speed_limits(self.client.reported, filters[0][1])
        return pools[0][0], {"minSpd": minimum, "maxSpd": maximum}

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
            table = await self._fresh_websocket()
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
        if _numeric_code(self.client.reported.get("systemMode")) != 1:
            raise ScheduleError("A confirmed Auto controller mode is required")
        self._require_connection()

    def _require_connection(self) -> None:
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
            table = await self._fresh_websocket()
            self._require_writes()
            ws = self.client._ws
            if table != plan.before:
                raise ScheduleError("Schedules changed since preview; request a new preview")
            pool_key, limits = self._pool_context()
            target = plan.after if plan.after is not None else table.get(plan.schedule_id)
            if not isinstance(target, dict) or target.get("lc") != pool_key:
                raise ScheduleError("Pool Filtration equipment changed since preview")
            if plan.after is not None and plan.operation != "disable":
                self._validate_entry(plan.after, limits)
            if plan.schedule_id is not None and table.get(plan.schedule_id) == plan.after:
                return {
                    "result": "unchanged",
                    "snapshot_source": SCHEDULE_SNAPSHOT_SOURCE,
                    **self.snapshot(),
                }
            self.pending = {
                "plan_id": plan_id,
                "operation": plan.operation,
                "schedule_id": plan.schedule_id,
                "at": _now(),
                "desired": plan.desired,
                "state": "outcome_uncertain",
                "snapshot_source": SCHEDULE_SNAPSHOT_SOURCE,
            }
            assert self._save is not None
            try:
                # Finish disk persistence before a frame can leave HA. Cancellation
                # while saving is conservative: leave the latch, send nothing.
                await self._save(self._journal(self.pending))
                if (
                    not self.writes_enabled
                    or _numeric_code(self.client.reported.get("systemMode")) != 1
                ):
                    raise ScheduleError("Write permission or controller mode changed")
                if (
                    self.client._stopping
                    or not self.client.websocket_connected
                    or self.client._ws is not ws
                    or ws.closed
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
                self._require_connection()
                if self.client._ws is not ws:
                    raise ScheduleError("TCX connection changed after the schedule write")
                fresh = await self._fresh_websocket()
                if self.client._ws is not ws:
                    raise ScheduleError("TCX connection changed during schedule readback")
                if slot is None or self._matching_slot(plan, sequence) != slot:
                    raise ScheduleError("Schedule readback did not confirm the intended result")
                # No rollback: a concurrent app edit must not be silently overwritten.
                if {k: v for k, v in fresh.items() if k != slot} != {
                    k: v for k, v in plan.before.items() if k != slot
                }:
                    raise ScheduleError("Other schedules changed during the write; review required")
                await self._save(self._journal(None))
                self.pending = None
                self._record("confirmed", plan_id, plan.operation)
                return {
                    "result": "confirmed",
                    "schedule_id": slot,
                    "snapshot_source": SCHEDULE_SNAPSHOT_SOURCE,
                    **self.snapshot(),
                }
            except BaseException:
                self._record("outcome_uncertain", plan_id, plan.operation)
                raise
            finally:
                await self.client._notify_status()

    async def async_acknowledge(
        self, plan_id: str, revision: str, source: str = SCHEDULE_SNAPSHOT_SOURCE
    ) -> dict[str, Any]:
        """Revalidate a reviewed snapshot before clearing; never send an equipment write."""
        async with self.client._control_lock:
            if not self.pending or self.pending.get("plan_id") != plan_id:
                raise ScheduleError("No matching uncertain write")
            table = await self._read_snapshot(source)
            if schedule_revision(table) != revision:
                raise ScheduleError("Schedules changed since review; read them again")
            if self._save is None:
                raise ScheduleError("Durable schedule journal is unavailable")
            acknowledgement = {
                "at": _now(),
                "plan_id": plan_id,
                "source": source,
                "revision": revision,
            }
            await self._save({"pending": None, "last_acknowledgement": acknowledgement})
            self.last_acknowledgement = acknowledgement
            self.pending = None
            self._record("review_acknowledged", plan_id, "review")
            await self.client._notify_status()
            return self.snapshot()
