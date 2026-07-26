"""Unit tests for Cluster C event-monitor reconnect supervisor."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from helpers_endpoints import fake_endpoint_factory
from zencontrol.api.event_router import ZenEventReceiver
from zencontrol.api.types import Transport
from zencontrol.interface.interface import ZenControl
from zencontrol.io.event import EventConst


@pytest.fixture(autouse=True)
def _noop_cleanup():
    yield


@pytest.mark.asyncio
async def test_supervisor_restores_session_after_listener_death() -> None:
    """I10: leases survive; receiver re-opens; on_connect is not re-fired."""
    zen = ZenControl()
    zen.reconnect_min_delay = 0.05
    zen.reconnect_max_delay = 0.05
    zen.reconnect_healthy_seconds = 3600

    open_count = 0
    base = fake_endpoint_factory()

    async def counting_factory(**kwargs):
        nonlocal open_count
        open_count += 1
        return await base(**kwargs)

    zen.event_receiver._endpoint_factory = counting_factory
    zen.commands.set_tpi_event_unicast_address = AsyncMock()
    zen.commands.tpi_event_emit = AsyncMock(return_value=True)

    on_connect = AsyncMock()
    on_disconnect = AsyncMock()
    on_resync = AsyncMock()
    zen.on_connect = on_connect
    zen.on_disconnect = on_disconnect

    await zen.start()
    assert open_count == 1
    on_connect.assert_awaited_once()
    assert zen._wiring is not None
    zen._wiring.on_resync = on_resync

    task = zen.event_receiver.consumer_task
    assert task is not None
    task.cancel()
    try:
        await asyncio.wait_for(task, timeout=1.0)
    except asyncio.CancelledError:
        pass

    for _ in range(50):
        live = zen.event_receiver.consumer_task
        if live is not None and not live.done() and live is not task and open_count >= 2:
            break
        await asyncio.sleep(0.05)
    else:
        pytest.fail(
            f"session restore did not complete: opens={open_count} "
            f"connect={on_connect.await_count} disconnect={on_disconnect.await_count}"
        )

    assert open_count >= 2
    on_connect.assert_awaited_once()
    on_disconnect.assert_not_awaited()
    on_resync.assert_awaited()

    await zen.stop()


@pytest.mark.asyncio
async def test_recover_does_not_flap_open_transport_on_retry() -> None:
    """Partial unicast failure must not close/reopen a working multicast each backoff."""
    receiver = ZenEventReceiver(unicast_listen_ip="127.0.0.1", unicast_port=0)
    mcast_opens = 0
    ucast_opens = 0
    mcast_closes = 0

    async def factory(**kwargs):
        nonlocal mcast_opens, ucast_opens, mcast_closes
        if kwargs.get("unicast"):
            ucast_opens += 1
            raise OSError("unicast bind failed")
        mcast_opens += 1
        ep = MagicMock()
        ep.is_open.return_value = True
        ep.bound_port = EventConst.MULTICAST_PORT
        ep.listen_port = EventConst.MULTICAST_PORT

        async def close() -> None:
            nonlocal mcast_closes
            mcast_closes += 1
            ep.is_open.return_value = False

        ep.close = close
        return ep

    receiver._endpoint_factory = factory
    mlease = await receiver.acquire(Transport.MULTICAST)
    # Hold a unicast lease without an open endpoint (partial failure after crash).
    receiver._refcounts[Transport.UNICAST] = 1
    assert mcast_opens == 1
    assert receiver.is_transport_open(Transport.MULTICAST)

    real_sleep = asyncio.sleep

    async def short_sleep(delay: float, *a, **k):
        await real_sleep(min(float(delay), 0.02))

    with patch("zencontrol.api.event_router.asyncio.sleep", side_effect=short_sleep):
        recover = asyncio.create_task(receiver._recover_session())
        for _ in range(50):
            if ucast_opens >= 3:
                break
            await real_sleep(0.02)
        receiver._stopping = True
        recover.cancel()
        try:
            await recover
        except asyncio.CancelledError:
            pass

    assert receiver.is_transport_open(Transport.MULTICAST)
    # One close at recover start, then multicast stays up — not re-opened each retry.
    assert mcast_closes == 1
    assert mcast_opens == 2  # initial acquire + one recover reopen
    assert ucast_opens >= 3

    receiver._stopping = False
    await mlease.release()
    receiver._refcounts[Transport.UNICAST] = 0
    await receiver.close()


@pytest.mark.asyncio
async def test_recover_skips_zombie_consumer_until_bind_succeeds() -> None:
    """Partial bind failure must not start a consumer on an unfed queue."""
    zen = ZenControl()
    zen.reconnect_min_delay = 0.05
    zen.reconnect_max_delay = 0.05
    zen.reconnect_healthy_seconds = 3600

    open_count = 0
    base = fake_endpoint_factory()

    async def flaky_factory(**kwargs):
        nonlocal open_count
        open_count += 1
        # Initial start succeeds; first recover attempt fails; later succeeds.
        if open_count == 2:
            raise OSError("Cannot assign requested address")
        return await base(**kwargs)

    zen.event_receiver._endpoint_factory = flaky_factory
    zen.commands.set_tpi_event_unicast_address = AsyncMock()
    zen.commands.tpi_event_emit = AsyncMock(return_value=True)

    on_resync = AsyncMock()
    await zen.start()
    assert zen.is_event_monitoring_active()
    assert zen._wiring is not None
    zen._wiring.on_resync = on_resync

    dead = zen.event_receiver.consumer_task
    assert dead is not None
    dead.cancel()
    try:
        await asyncio.wait_for(dead, timeout=1.0)
    except asyncio.CancelledError:
        pass

    # While bind is failing, do not treat a lone consumer (or lease count) as healthy.
    for _ in range(20):
        if open_count >= 2:
            break
        await asyncio.sleep(0.05)
    assert open_count >= 2
    assert not zen.event_receiver.leased_transports_open()
    live = zen.event_receiver.consumer_task
    # Either still the dead task, or None — never a new consumer without sockets.
    if live is not None and live is not dead:
        assert live.done()
    assert not zen.is_event_monitoring_active()
    on_resync.assert_not_awaited()

    for _ in range(80):
        if (
            zen.event_receiver.leased_transports_open()
            and zen.is_event_monitoring_active()
            and on_resync.await_count >= 1
        ):
            break
        await asyncio.sleep(0.05)
    else:
        pytest.fail(
            f"recover did not complete after bind recovered: opens={open_count} "
            f"active={zen.is_event_monitoring_active()} resync={on_resync.await_count}"
        )

    assert open_count >= 3
    assert zen.event_receiver.is_transport_open(Transport.MULTICAST)
    await zen.stop()


@pytest.mark.asyncio
async def test_wait_for_session_restore_requires_open_transport() -> None:
    """A live consumer without open transports is not a restored session."""
    zen = ZenControl()
    zen.event_receiver._endpoint_factory = fake_endpoint_factory()
    zen.commands.set_tpi_event_unicast_address = AsyncMock()
    zen.commands.tpi_event_emit = AsyncMock(return_value=True)

    await zen.start()
    dead = zen.event_receiver.consumer_task
    assert dead is not None

    # Simulate the recovery hole: consumer running, lease held, socket missing.
    zombie = asyncio.create_task(asyncio.sleep(3600))
    zen.event_receiver._consumer_task = zombie
    for transport in list(zen.event_receiver._endpoints):
        await zen.event_receiver._close_endpoint(transport)

    assert zen.event_receiver.lease_count(Transport.MULTICAST) > 0
    assert not zen.event_receiver.is_transport_open(Transport.MULTICAST)
    assert not zen.is_event_monitoring_active()
    assert not await zen._wait_for_session_restore(dead, timeout=0.2)

    zombie.cancel()
    try:
        await zombie
    except asyncio.CancelledError:
        pass
    await zen.stop()


@pytest.mark.asyncio
async def test_wait_for_session_restore_awaits_event_not_poll() -> None:
    """Restore wait is signaled — no 50ms polling during an outage."""
    zen = ZenControl()
    zen.event_receiver._endpoint_factory = fake_endpoint_factory()
    zen.commands.set_tpi_event_unicast_address = AsyncMock()
    zen.commands.tpi_event_emit = AsyncMock(return_value=True)

    await zen.start()
    dead = zen.event_receiver.consumer_task
    assert dead is not None
    # Leave leases/endpoints up; suppress recover so the wait must use the Event.
    zen.event_receiver.on_unexpected_exit = None
    zen.event_receiver._schedule_recover = lambda: None  # type: ignore[method-assign]
    dead.cancel()
    try:
        await asyncio.wait_for(dead, timeout=1.0)
    except asyncio.CancelledError:
        pass
    zen.event_receiver._consumer_task = dead

    sleep_calls: list[float] = []
    real_sleep = asyncio.sleep

    async def tracking_sleep(delay, *a, **k):
        sleep_calls.append(float(delay))
        return await real_sleep(delay, *a, **k)

    async def restore_later():
        await real_sleep(0.05)
        # Mimic recover: new consumer + open transports + session hook.
        zen.event_receiver._consumer_task = asyncio.create_task(real_sleep(3600))
        await zen._on_session_restored()

    with patch("zencontrol.interface.interface.asyncio.sleep", side_effect=tracking_sleep):
        restore_task = asyncio.create_task(restore_later())
        assert await zen._wait_for_session_restore(dead, timeout=1.0)
        await restore_task

    assert sleep_calls == [], f"restore wait polled via sleep: {sleep_calls}"

    live = zen.event_receiver.consumer_task
    if live is not None and not live.done():
        live.cancel()
        try:
            await live
        except asyncio.CancelledError:
            pass
    await zen.stop()


@pytest.mark.asyncio
async def test_supervisor_waits_without_stalled_backoff_while_receiver_recovers() -> None:
    """Receiver owns retry — supervisor must not spam restore-stalled warnings."""
    zen = ZenControl()
    zen.reconnect_min_delay = 0.05
    zen.reconnect_max_delay = 0.05
    warnings: list[str] = []

    open_count = 0
    base = fake_endpoint_factory()

    async def slow_unicast_factory(**kwargs):
        nonlocal open_count
        open_count += 1
        # After the consumer dies, first few recover opens fail so restore
        # takes longer than the old 5s supervisor timeout would have.
        if open_count >= 2 and open_count <= 4:
            raise OSError("Cannot assign requested address")
        return await base(**kwargs)

    zen.event_receiver._endpoint_factory = slow_unicast_factory
    zen.commands.set_tpi_event_unicast_address = AsyncMock()
    zen.commands.tpi_event_emit = AsyncMock(return_value=True)

    real_warning = zen.logger.warning

    def capture_warning(msg, *args, **kwargs):
        text = msg % args if args else str(msg)
        warnings.append(text)
        return real_warning(msg, *args, **kwargs)

    zen.logger.warning = capture_warning  # type: ignore[method-assign]

    # Shorten receiver recover sleeps so the test finishes quickly.
    real_sleep = asyncio.sleep

    async def short_sleep(delay: float, *a, **k):
        await real_sleep(min(float(delay), 0.02))

    with patch("zencontrol.api.event_router.asyncio.sleep", side_effect=short_sleep):
        await zen.start()
        dead = zen.event_receiver.consumer_task
        assert dead is not None
        dead.cancel()
        try:
            await asyncio.wait_for(dead, timeout=1.0)
        except asyncio.CancelledError:
            pass

        for _ in range(100):
            if zen.is_event_monitoring_active() and zen.event_receiver.consumer_task is not dead:
                if zen.event_receiver.consumer_task and not zen.event_receiver.consumer_task.done():
                    break
            await real_sleep(0.05)
        else:
            pytest.fail(f"session never restored: opens={open_count} warnings={warnings}")

    stalled = [w for w in warnings if "restore stalled" in w]
    assert stalled == [], f"supervisor duplicated backoff: {stalled}"
    await zen.stop()


@pytest.mark.asyncio
async def test_supervisor_exits_when_unexpected_death_has_no_leases() -> None:
    """Zero leases after consumer death is terminal — no busy-spin / log flood."""
    zen = ZenControl()
    zen.event_receiver._endpoint_factory = fake_endpoint_factory()
    zen.commands.set_tpi_event_unicast_address = AsyncMock()
    zen.commands.tpi_event_emit = AsyncMock(return_value=True)

    errors: list[str] = []
    real_error = zen.logger.error

    def capture_error(msg, *args, **kwargs):
        text = msg % args if args else str(msg)
        errors.append(text)
        return real_error(msg, *args, **kwargs)

    zen.logger.error = capture_error  # type: ignore[method-assign]

    await zen.start()
    supervisor = zen._supervisor_task
    dead = zen.event_receiver.consumer_task
    assert supervisor is not None and dead is not None

    # Race shape: last lease already gone; consumer exit is still unexpected.
    zen.event_receiver._refcounts[Transport.MULTICAST] = 0
    zen.event_receiver._refcounts[Transport.UNICAST] = 0
    dead.cancel()
    try:
        await asyncio.wait_for(dead, timeout=1.0)
    except asyncio.CancelledError:
        pass

    await asyncio.wait_for(supervisor, timeout=1.0)
    assert supervisor.done()
    cancelled_logs = [
        e for e in errors if "cancelled unexpectedly" in e or "Event monitor task error" in e
    ]
    assert len(cancelled_logs) <= 1, f"supervisor spun: {cancelled_logs}"
    assert zen.event_task is None
    await zen.aclose()


@pytest.mark.asyncio
async def test_supervisor_exits_when_last_lease_releases_cleanly() -> None:
    """Intentional consumer stop with no leases ends the supervisor (no 20Hz poll)."""
    zen = ZenControl()
    zen.event_receiver._endpoint_factory = fake_endpoint_factory()
    zen.commands.set_tpi_event_unicast_address = AsyncMock()
    zen.commands.tpi_event_emit = AsyncMock(return_value=True)

    ctrl = zen.add_controller(
        id=1, name="ctrl-a", label="A", host="127.0.0.1", mac="02:00:00:00:00:01"
    )
    await zen.start()
    supervisor = zen._supervisor_task
    assert supervisor is not None

    await zen.remove_controller(ctrl)
    await asyncio.wait_for(supervisor, timeout=1.0)
    assert supervisor.done()
    assert not zen._has_event_leases()
    await zen.aclose()


@pytest.mark.asyncio
async def test_stop_does_not_reconnect() -> None:
    zen = ZenControl()
    zen.reconnect_min_delay = 0.01
    open_count = 0
    base = fake_endpoint_factory()

    async def counting_factory(**kwargs):
        nonlocal open_count
        open_count += 1
        return await base(**kwargs)

    zen.event_receiver._endpoint_factory = counting_factory
    zen.commands.set_tpi_event_unicast_address = AsyncMock()
    zen.commands.tpi_event_emit = AsyncMock(return_value=True)

    await zen.start()
    assert open_count == 1
    await zen.stop()
    await asyncio.sleep(0.1)
    assert open_count == 1


@pytest.mark.asyncio
async def test_supervisor_cancel_does_not_reconnect() -> None:
    """HA cancels tasks on shutdown before unload sets _stopping — no reconnect."""
    zen = ZenControl()
    zen.reconnect_min_delay = 0.01
    open_count = 0
    base = fake_endpoint_factory()

    async def counting_factory(**kwargs):
        nonlocal open_count
        open_count += 1
        return await base(**kwargs)

    zen.event_receiver._endpoint_factory = counting_factory
    zen.commands.set_tpi_event_unicast_address = AsyncMock()
    zen.commands.tpi_event_emit = AsyncMock(return_value=True)

    await zen.start()
    assert open_count == 1
    assert zen._supervisor_task is not None

    zen._supervisor_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await zen._supervisor_task

    await asyncio.sleep(0.1)
    assert open_count == 1
    await zen.aclose()
