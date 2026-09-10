"""Clock-driven recovery regressions; no cloud or equipment requests."""

import asyncio

import pytest
from test_rest_pacing import Clock, Response, offline_client
from test_rest_pacing import no_network as no_network

from custom_components.tcx_direct import api


def test_successes_do_not_accelerate_before_hour_and_each_step_needs_another_hour(monkeypatch):
    async def run():
        clock = Clock()
        clock.install(monkeypatch)
        client, _ = offline_client([Response(429), *[Response() for _ in range(25)]])
        with pytest.raises(api.TCXRateLimited):
            await client.async_get_shadow()
        clock.advance(240)
        for _ in range(10):
            await client.async_get_shadow()
        assert client.shadow_poll_interval == 240
        clock.advance(3359)
        await client.async_get_shadow()
        assert client.shadow_poll_interval == 240
        clock.advance(1)
        await client.async_get_shadow()
        assert client.shadow_poll_interval == 180
        for _ in range(10):
            await client.async_get_shadow()
        assert client.shadow_poll_interval == 180
        clock.advance(3600)
        await client.async_get_shadow()
        assert client.shadow_poll_interval == 135
        clock.advance(3600)
        await client.async_get_shadow()
        assert client.shadow_poll_interval == 135  # still needs two consecutive successes
        await client.async_get_shadow()
        assert client.shadow_poll_interval == 120

    asyncio.run(run())


def test_new_rate_limit_restarts_recovery_hour(monkeypatch):
    async def run():
        clock = Clock()
        clock.install(monkeypatch)
        client, _ = offline_client(
            [Response(429), Response(), Response(429), Response(), Response(), Response()]
        )
        with pytest.raises(api.TCXRateLimited):
            await client.async_get_shadow()
        clock.advance(3500)
        await client.async_get_shadow()
        with pytest.raises(api.TCXRateLimited):
            await client.async_get_shadow()
        clock.advance(480)
        await client.async_get_shadow()
        clock.advance(3119)
        await client.async_get_shadow()
        assert client.shadow_poll_interval == 480
        clock.advance(1)
        await client.async_get_shadow()
        assert client.shadow_poll_interval == 360

    asyncio.run(run())


@pytest.mark.parametrize("retry_after", ["7200", "86400"])
def test_long_server_deadline_remains_binding_and_does_not_allow_catchup_steps(
    monkeypatch, retry_after
):
    async def run():
        clock = Clock()
        clock.install(monkeypatch)
        client, session = offline_client(
            [Response(429, retry_after), Response(), Response(), Response()]
        )
        with pytest.raises(api.TCXRateLimited):
            await client.async_get_shadow()
        clock.advance(int(retry_after) - 1)
        with pytest.raises(api.TCXShadowDeferred):
            await client.async_get_shadow()
        assert len(session.calls) == 1
        clock.advance(1)
        await client.async_get_shadow()
        assert client.shadow_poll_interval == 240
        await client.async_get_shadow()
        assert client.shadow_poll_interval == 180
        await client.async_get_shadow()
        assert client.shadow_poll_interval == 180

    asyncio.run(run())


def test_non_rate_error_requires_new_success_streak_after_hour(monkeypatch):
    async def run():
        clock = Clock()
        clock.install(monkeypatch)
        client, _ = offline_client(
            [Response(429), Response(), Response(500), Response(), Response()]
        )
        with pytest.raises(api.TCXRateLimited):
            await client.async_get_shadow()
        clock.advance(3600)
        await client.async_get_shadow()
        with pytest.raises(api.TCXConnectionError):
            await client.async_get_shadow()
        await client.async_get_shadow()
        assert client.shadow_poll_interval == 240
        await client.async_get_shadow()
        assert client.shadow_poll_interval == 180

    asyncio.run(run())


@pytest.mark.parametrize("healthy", [False, True])
def test_normal_background_polling_stays_two_minutes(monkeypatch, healthy):
    async def run():
        clock = Clock()
        clock.install(monkeypatch)
        client, session = offline_client([Response(), Response(), Response()])
        client.websocket_connected = healthy
        client.last_ws_reported_monotonic = clock.monotonic if healthy else None
        delays = []

        async def sleep(delay):
            delays.append(delay)
            clock.advance(delay)
            if len(session.calls) == 3:
                client._stopping = True

        monkeypatch.setattr(api.asyncio, "sleep", sleep)
        await client._shadow_loop()
        assert delays == [2, 120, 120, 120]
        assert client.shadow_poll_interval == 120
        assert client.shadow_rate_limit_count == 0

    asyncio.run(run())


def test_background_loop_uses_held_interval_until_recovery_is_eligible(monkeypatch):
    async def run():
        clock = Clock()
        clock.install(monkeypatch)
        client, session = offline_client([Response(429), *[Response() for _ in range(15)]])
        delays = []

        async def sleep(delay):
            delays.append(delay)
            clock.advance(delay)
            if len(session.calls) == 16:
                client._stopping = True

        monkeypatch.setattr(api.asyncio, "sleep", sleep)
        await client._shadow_loop()
        assert delays == [2, *[240] * 15, 180]
        assert client.shadow_success_count == 15
        assert client.shadow_rate_limit_count == 1

    asyncio.run(run())
