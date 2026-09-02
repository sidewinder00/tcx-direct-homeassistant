"""Offline REST pacing regressions: actual reader, fake HTTP and controllable clocks."""

from __future__ import annotations

import asyncio
import math
import socket
from copy import deepcopy
from datetime import datetime, timezone
from email.utils import format_datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiohttp
import pytest

from custom_components.tcx_direct import api
from custom_components.tcx_direct.rest_pacing import retry_after_seconds


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("REST pacing tests must not contact the network")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)
    monkeypatch.setattr(aiohttp.ClientSession, "_request", blocked)


class Clock:
    def __init__(self):
        self.monotonic = 100.0
        self.wall = datetime(2030, 1, 1, tzinfo=timezone.utc).timestamp()

    def install(self, monkeypatch):
        # Replace only api's reference, not asyncio's real timeout clock.
        monkeypatch.setattr(
            api, "time", SimpleNamespace(monotonic=lambda: self.monotonic, time=lambda: self.wall)
        )

    def advance(self, seconds):
        self.monotonic += seconds
        self.wall += seconds


class Response:
    def __init__(self, status=200, retry_after=None, reported=None):
        self.status = status
        self.headers = {} if retry_after is None else {"Retry-After": retry_after}
        self.reported = reported if reported is not None else {"systemMode": 1, "sh": {}}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def json(self, **kwargs):
        return {"state": {"reported": deepcopy(self.reported)}}


class Session:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        return next(self.responses)


def offline_client(responses):
    session = Session(responses)
    client = api.TCXClient(session, "test@example.invalid", "unused", "test-device")
    client.id_token = "test-token"
    client.user_id = "test-user"
    client.async_ensure_auth = AsyncMock()
    client.websocket_connected = True
    client.cloud_reachable = True
    return client, session


@pytest.mark.parametrize(
    "header, expected",
    [
        (None, None),
        ("", None),
        ("0", 0),
        ("120", 120),
        (" 3600 ", 3600),
        ("-1", None),
        ("+1", None),
        ("1.5", None),
        ("1e3", None),
        ("NaN", None),
        ("inf", None),
        ("Infinity", None),
        ("１２３", None),
        ("not a date", None),
        ("9" * 400, math.inf),
        ("Tue, 01 Jan 2030 00:10:00 GMT", 600),
        ("Tuesday, 01-Jan-30 00:10:00 GMT", 600),
        ("Tue Jan  1 00:10:00 2030", 600),
        ("Mon, 31 Dec 2029 23:59:00 GMT", 0),
    ],
)
def test_retry_after_seconds_and_http_dates(header, expected):
    assert retry_after_seconds(header, now=Clock().wall) == expected


def test_large_integer_delay_is_not_rounded_down():
    value = 2**53 + 1
    assert retry_after_seconds(str(value), now=0) >= value


def test_direct_429_counts_once_and_defers_all_readers_without_auth_or_http(monkeypatch):
    async def run():
        clock = Clock()
        clock.install(monkeypatch)
        client, session = offline_client([Response(429, "600"), Response()])
        with pytest.raises(api.TCXRateLimited):
            await client.async_get_shadow()
        assert client.shadow_cooldown_remaining == 600
        assert client.shadow_poll_interval == 240
        assert client.shadow_failure_count == client.shadow_rate_limit_count == 1
        for call in (
            client.async_get_shadow,
            lambda: client.schedules.async_read(source="rest"),
        ):
            with pytest.raises(api.TCXShadowDeferred, match="600 seconds") as caught:
                await asyncio.wait_for(call(), 0.2)
            assert isinstance(caught.value, api.TCXConnectionError)
            assert not isinstance(caught.value, api.TCXRateLimited)
        assert len(session.calls) == client.shadow_http_attempt_count == 1
        assert client.shadow_request_count == 3
        assert client.shadow_deferred_count == 2
        assert client.shadow_failure_count == client.shadow_rate_limit_count == 1
        client.async_ensure_auth.assert_awaited_once()
        assert client.websocket_connected and client.cloud_reachable
        assert client.last_error is None
        assert client.last_shadow_error.endswith("HTTP 429")
        clock.advance(600)
        await client.async_get_shadow()
        assert len(session.calls) == 2
        assert client.shadow_success_count == 1
        assert client.last_shadow_error is None

    asyncio.run(run())


def test_http_date_deadline_uses_monotonic_clock_after_receipt(monkeypatch):
    async def run():
        clock = Clock()
        clock.install(monkeypatch)
        header = format_datetime(
            datetime.fromtimestamp(clock.wall + 600, timezone.utc), usegmt=True
        )
        client, session = offline_client([Response(429, header), Response()])
        with pytest.raises(api.TCXRateLimited):
            await client.async_get_shadow()
        for jump in (36000, -72000):
            clock.wall += jump
            with pytest.raises(api.TCXShadowDeferred):
                await client.async_get_shadow()
            assert client.shadow_cooldown_remaining == 600
        clock.monotonic += 599
        with pytest.raises(api.TCXShadowDeferred):
            await client.async_get_shadow()
        clock.monotonic += 1
        await client.async_get_shadow()
        assert len(session.calls) == 2

    asyncio.run(run())


def test_server_hour_delay_is_not_shortened_by_local_cap(monkeypatch):
    async def run():
        clock = Clock()
        clock.install(monkeypatch)
        client, session = offline_client([Response(429, "3600"), Response()])
        delays = []

        async def sleep(delay):
            assert not client._shadow_lock.locked()
            delays.append(delay)
            if len(session.calls) == 2:
                client._stopping = True
            else:
                clock.advance(delay)

        monkeypatch.setattr(api.asyncio, "sleep", sleep)
        await client._shadow_loop()
        assert delays == [2, 1800, 1800, 240]
        assert clock.monotonic == 3702
        assert len(session.calls) == 2
        assert client.shadow_rate_limit_count == client.shadow_failure_count == 1
        assert client.shadow_deferred_count == 1
        assert client.shadow_success_count == 1
        assert client.cloud_reachable and client.websocket_connected

    asyncio.run(run())


def test_background_caller_honors_deadline_established_during_setup(monkeypatch):
    async def run():
        clock = Clock()
        clock.install(monkeypatch)
        client, session = offline_client([Response(429, "600")])
        with pytest.raises(api.TCXRateLimited):
            await client.async_get_shadow()
        delays = []

        async def sleep(delay):
            delays.append(delay)
            if len(delays) == 1:
                clock.advance(delay)
            else:
                client._stopping = True

        monkeypatch.setattr(api.asyncio, "sleep", sleep)
        await client._shadow_loop()
        assert delays == [2, 598]
        assert len(session.calls) == 1
        assert client.shadow_rate_limit_count == 1
        assert client.shadow_deferred_count == 1

    asyncio.run(run())


def test_unrepresentable_delay_defers_without_unbounded_sleep(monkeypatch):
    async def run():
        clock = Clock()
        clock.install(monkeypatch)
        client, session = offline_client([Response(429, "9" * 400)])
        delays = []

        async def sleep(delay):
            assert math.isfinite(delay)
            delays.append(delay)
            if len(delays) == 3:
                client._stopping = True
            clock.advance(delay)

        monkeypatch.setattr(api.asyncio, "sleep", sleep)
        await client._shadow_loop()
        assert delays == [2, 1800, 1800]
        with pytest.raises(api.TCXShadowDeferred, match="REST paused for this session"):
            await client.async_get_shadow()
        assert math.isinf(client.shadow_cooldown_remaining)
        assert len(session.calls) == client.shadow_rate_limit_count == 1

    asyncio.run(run())


@pytest.mark.parametrize("first_status", [200, 429])
def test_concurrent_readers_serialize_and_recheck_cooldown(monkeypatch, first_status):
    async def run():
        Clock().install(monkeypatch)
        started, release = asyncio.Event(), asyncio.Event()

        class BlockingResponse(Response):
            async def __aenter__(self):
                started.set()
                await release.wait()
                return self

        client, session = offline_client(
            [
                BlockingResponse(first_status, "600", {"marker": 1}),
                Response(reported={"marker": 2}),
            ]
        )
        first = asyncio.create_task(client.async_get_shadow())
        await started.wait()
        second = asyncio.create_task(client.async_get_shadow())
        await asyncio.sleep(0)
        assert len(session.calls) == 1
        release.set()
        results = await asyncio.gather(first, second, return_exceptions=True)
        if first_status == 429:
            assert isinstance(results[0], api.TCXRateLimited)
            assert isinstance(results[1], api.TCXShadowDeferred)
            assert len(session.calls) == 1
            assert client.shadow_rate_limit_count == client.shadow_deferred_count == 1
        else:
            assert results[0]["state"]["reported"]["marker"] == 1
            assert results[1]["state"]["reported"]["marker"] == 2
            assert len(session.calls) == 2  # distinct fresh reads, not cached reuse
        assert not client._shadow_lock.locked()

    asyncio.run(run())


@pytest.mark.parametrize("cancel_waiter", [False, True])
def test_cancelled_reader_does_not_strand_request_lock(cancel_waiter):
    async def run():
        started, release = asyncio.Event(), asyncio.Event()

        class BlockingResponse(Response):
            async def __aenter__(self):
                started.set()
                await release.wait()
                return self

        client, session = offline_client([BlockingResponse(), Response()])
        first = asyncio.create_task(client.async_get_shadow())
        await started.wait()
        if cancel_waiter:
            cancelled = asyncio.create_task(client.async_get_shadow())
            await asyncio.sleep(0)
        else:
            cancelled = first
        cancelled.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cancelled
        if cancel_waiter:
            assert client._shadow_lock.locked()
            release.set()
            await first
        assert not client._shadow_lock.locked()
        await client.async_get_shadow()
        assert len(session.calls) == 2
        assert client.shadow_failure_count == client.shadow_rate_limit_count == 0

    asyncio.run(run())


def test_recovery_requires_two_successes_and_preserves_pressure_after_one(monkeypatch):
    async def run():
        clock = Clock()
        clock.install(monkeypatch)
        client, _ = offline_client(
            [
                Response(429),
                Response(),
                Response(429),
                Response(),
                Response(),
                Response(),
                Response(),
            ]
        )
        with pytest.raises(api.TCXRateLimited):
            await client.async_get_shadow()
        assert client.shadow_poll_interval == 240
        clock.advance(client.shadow_cooldown_remaining)
        await client.async_get_shadow()
        assert client.shadow_poll_interval == 240
        with pytest.raises(api.TCXRateLimited):
            await client.async_get_shadow()
        assert client.shadow_poll_interval == 480
        clock.advance(client.shadow_cooldown_remaining)
        for expected in (480, 240, 240, 120):
            await client.async_get_shadow()
            assert client.shadow_poll_interval == expected

    asyncio.run(run())


def test_local_backoff_is_capped_and_non_rate_failure_breaks_success_streak(monkeypatch):
    async def run():
        clock = Clock()
        clock.install(monkeypatch)
        client, _ = offline_client(
            [
                *[Response(429) for _ in range(5)],
                Response(),
                Response(500),
                Response(),
                Response(),
            ]
        )
        client.websocket_connected = False
        for expected in (240, 480, 960, 1800, 1800):
            with pytest.raises(api.TCXRateLimited):
                await client.async_get_shadow()
            assert client.shadow_poll_interval == client.shadow_cooldown_remaining == expected
            clock.advance(expected)
        assert not client.cloud_reachable
        await client.async_get_shadow()
        with pytest.raises(api.TCXConnectionError):
            await client.async_get_shadow()
        await client.async_get_shadow()
        assert client.shadow_poll_interval == 1800
        await client.async_get_shadow()
        assert client.shadow_poll_interval == 900

    asyncio.run(run())


@pytest.mark.parametrize("already_cooling", [False, True])
def test_refresh_on_control_timeout_does_not_bypass_cooldown_or_resend(
    monkeypatch, already_cooling
):
    async def run():
        Clock().install(monkeypatch)
        client, session = offline_client([Response(429, "600")])
        client.reported = {"systemMode": 1, "pool": {"st": 0}}
        client._ws = SimpleNamespace(closed=False, send_json=AsyncMock())
        if already_cooling:
            with pytest.raises(api.TCXRateLimited):
                await client.async_get_shadow()
        for _ in range(2):
            async with client._control_lock:
                with pytest.raises(api.TCXConnectionError, match="did not confirm"):
                    await asyncio.wait_for(
                        client._async_send_control(
                            {"pool": {"st": 1}},
                            "pump power state",
                            lambda reported: reported["pool"]["st"] == 1,
                            confirmation_timeout=0.001,
                            refresh_on_timeout=True,
                        ),
                        0.2,
                    )
            assert client._pending_control is None
            assert not client._shadow_lock.locked()
        assert len(session.calls) == client.shadow_rate_limit_count == 1
        assert client.shadow_deferred_count == (2 if already_cooling else 1)
        assert client.control_confirmation_refresh_count == 2
        assert client.control_failure_count == client._ws.send_json.await_count == 2
        assert client.control_success_count == 0
        assert client.cloud_reachable and client.websocket_connected

        # A later WebSocket confirmation still succeeds during REST cooldown.
        async def confirm(frame):
            client.reported["pool"]["st"] = 1
            client._resolve_pending_control()

        client._ws.send_json = AsyncMock(side_effect=confirm)
        await client._async_send_control(
            {"pool": {"st": 1}},
            "pump power state",
            lambda reported: reported["pool"]["st"] == 1,
            refresh_on_timeout=True,
        )
        assert client.control_success_count == 1
        assert len(session.calls) == 1

    asyncio.run(run())


@pytest.mark.parametrize("background_status", [200, 429])
@pytest.mark.parametrize("cancel_refresh", [False, True])
def test_contended_timeout_refresh_holds_control_lock_until_read_or_cancellation(
    monkeypatch, background_status, cancel_refresh
):
    async def run():
        Clock().install(monkeypatch)
        background_started, release_background = asyncio.Event(), asyncio.Event()
        refresh_queued, equipment_queued = asyncio.Event(), asyncio.Event()

        class ObservedLock(asyncio.Lock):
            def __init__(self, contended):
                super().__init__()
                self.contended = contended

            async def acquire(self):
                if self.locked():
                    self.contended.set()
                return await super().acquire()

        class BlockingResponse(Response):
            async def __aenter__(self):
                background_started.set()
                await release_background.wait()
                return self

        client, session = offline_client([BlockingResponse(background_status, "600"), Response()])
        client._shadow_lock = ObservedLock(refresh_queued)
        client._control_lock = ObservedLock(equipment_queued)
        client.reported = {
            "systemMode": 1,
            "pool": {"et": "V_POS", "app": "POOL_M", "st": 0},
        }
        messages = []

        async def send(frame):
            messages.append(frame)
            # Do not confirm the first command; confirm only the separately
            # requested second equipment command once it can acquire the lock.
            if len(messages) == 2:
                client.reported["pool"]["st"] = 1
                client._resolve_pending_control()

        client._ws = SimpleNamespace(closed=False, send_json=send)

        async def timed_out_command():
            async with client._control_lock:
                await client._async_send_control(
                    {"pool": {"st": 1}},
                    "pump power state",
                    lambda reported: reported["pool"]["st"] == 1,
                    confirmation_timeout=0.001,
                    refresh_on_timeout=True,
                )

        # One background read holds the real request lock through HTTP I/O.
        background = asyncio.create_task(client.async_get_shadow())
        await background_started.wait()
        command = asyncio.create_task(timed_out_command())
        await refresh_queued.wait()
        equipment = asyncio.create_task(client.async_set_pump_power(True))
        await equipment_queued.wait()
        assert client._shadow_lock.locked() and client._control_lock.locked()
        assert client.control_confirmation_refresh_count == 1
        assert not command.done() and not equipment.done()
        assert len(session.calls) == len(messages) == 1

        if cancel_refresh:
            command.cancel()
            with pytest.raises(asyncio.CancelledError):
                await command
            await equipment
            # Cancelling the queued refresh releases equipment control even
            # while the background request continues holding the REST lock.
            assert not background.done()
            assert client._shadow_lock.locked() and not client._control_lock.locked()
        release_background.set()
        if background_status == 429:
            with pytest.raises(api.TCXRateLimited):
                await background
        else:
            await background
        if not cancel_refresh:
            with pytest.raises(api.TCXConnectionError, match="did not confirm.*0.001 seconds"):
                await command
            await equipment

        assert len(messages) == 2  # two explicit commands, never an automatic resend
        assert client.control_success_count == 1
        assert client.control_failure_count == (0 if cancel_refresh else 1)
        assert client._pending_control is None
        assert not client._shadow_lock.locked() and not client._control_lock.locked()
        expected_http = 2 if background_status == 200 and not cancel_refresh else 1
        assert client.shadow_http_attempt_count == len(session.calls) == expected_http
        assert client.shadow_rate_limit_count == int(background_status == 429)
        assert client.shadow_deferred_count == int(background_status == 429 and not cancel_refresh)
        assert client.websocket_connected and client.cloud_reachable

    async def bounded_run():
        # A regression must fail promptly rather than hang CI on either lock.
        await asyncio.wait_for(run(), 2)

    asyncio.run(bounded_run())


@pytest.mark.parametrize("last_status", [200, 429])
def test_http_attempt_count_includes_fallback_and_429_never_falls_through(last_status):
    async def run():
        client, session = offline_client([Response(404), Response(last_status)])
        if last_status == 429:
            with pytest.raises(api.TCXRateLimited):
                await client.async_get_shadow()
            assert client.shadow_rate_limit_count == 1
        else:
            await client.async_get_shadow()
            assert client.shadow_success_count == 1
        assert client.shadow_request_count == 1
        assert client.shadow_http_attempt_count == len(session.calls) == 2

    asyncio.run(run())


@pytest.mark.parametrize("status", [401, 403, 500])
def test_other_errors_preserve_type_and_do_not_create_rate_cooldown(status):
    async def run():
        client, session = offline_client([Response(status)])
        expected = api.TCXAuthError if status in (401, 403) else api.TCXConnectionError
        with pytest.raises(expected):
            await client.async_get_shadow()
        assert client.shadow_failure_count == 1
        assert client.shadow_cooldown_remaining == client.shadow_rate_limit_count == 0
        assert not client._shadow_lock.locked()
        assert len(session.calls) == 1
        if status in (401, 403):
            assert client.id_token is None

    asyncio.run(run())


def test_unsupported_endpoint_still_stops_http_polling(monkeypatch):
    async def run():
        client, session = offline_client([Response(404), Response(405)])
        delays = []

        async def sleep(delay):
            delays.append(delay)
            if delay == api.MAX_WEBSOCKET_SESSION:
                client._stopping = True

        monkeypatch.setattr(api.asyncio, "sleep", sleep)
        await client._shadow_loop()
        assert delays == [2, 120, api.MAX_WEBSOCKET_SESSION]
        assert client.shadow_supported is False
        assert client.shadow_request_count == client.shadow_failure_count == 1
        assert client.shadow_rate_limit_count == client.shadow_deferred_count == 0
        assert len(session.calls) == client.shadow_http_attempt_count == 2
        assert client.cloud_reachable and client.websocket_connected

    asyncio.run(run())


def test_shutdown_cancels_background_cooldown_without_more_http(monkeypatch):
    async def run():
        Clock().install(monkeypatch)
        client, session = offline_client([Response(429, "3600")])
        sleeping = asyncio.Event()

        async def sleep(delay):
            if delay == 2:
                return
            assert not client._shadow_lock.locked()
            sleeping.set()
            await asyncio.get_running_loop().create_future()

        monkeypatch.setattr(api.asyncio, "sleep", sleep)
        background = asyncio.create_task(client._shadow_loop())
        client._tasks = [background]
        await asyncio.wait_for(sleeping.wait(), 1)
        await asyncio.wait_for(client.async_stop(), 1)
        assert background.cancelled()
        assert not client._tasks and not client._shadow_lock.locked()
        assert client.shadow_rate_limit_count == len(session.calls) == 1

    asyncio.run(run())
