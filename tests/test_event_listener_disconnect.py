"""Unit tests for event-listener disconnect, start rollback, and teardown."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from zencontrol.api.protocol import ZenProtocol
from zencontrol.api.types import ZenEventMode
from zencontrol.interface.interface import ZenControl
from zencontrol.io.event import ZenEvent

EventHandler = Callable[[ZenEvent], Awaitable[None]]
UnexpectedExitHandler = Callable[[], Awaitable[None]]


class _FakeListener:
    """Duck-typed listener implementing the ZenListener session API."""

    def __init__(self, *, raise_error: Exception | None = None) -> None:
        self._raise_error = raise_error
        self.listen_port = 6969
        self.closed = False
        self.close_calls = 0
        self._consumer_task: asyncio.Task[None] | None = None
        self._on_event: EventHandler | None = None
        self._on_unexpected_exit: UnexpectedExitHandler | None = None

    @property
    def consumer_task(self) -> asyncio.Task[None] | None:
        return self._consumer_task

    def is_running(self) -> bool:
        return self._consumer_task is not None and not self._consumer_task.done()

    def run(
        self,
        on_event: EventHandler,
        *,
        on_unexpected_exit: UnexpectedExitHandler | None = None,
    ) -> asyncio.Task[None]:
        self._on_event = on_event
        self._on_unexpected_exit = on_unexpected_exit
        self._consumer_task = asyncio.create_task(self._consume())
        return self._consumer_task

    async def _consume(self) -> None:
        unexpected = True
        try:
            async for event in self.events():
                if self._on_event is not None:
                    await self._on_event(event)
        except asyncio.CancelledError:
            unexpected = False
            raise
        except Exception:
            pass
        finally:
            await self.close()
            if unexpected and callable(self._on_unexpected_exit):
                await self._on_unexpected_exit()

    async def events(self):
        if self._raise_error is not None:
            raise self._raise_error
        return
        yield  # pragma: no cover — makes this an async generator

    async def stop(self) -> None:
        task = self._consumer_task
        self._consumer_task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        await self.close()

    async def close(self) -> None:
        self.close_calls += 1
        self.closed = True


@pytest.mark.asyncio
async def test_listener_error_fires_on_disconnect_once() -> None:
    protocol = ZenProtocol()
    on_disconnect = AsyncMock()
    protocol.disconnect_callback = on_disconnect

    fake = _FakeListener(raise_error=RuntimeError("socket died"))

    with patch(
        "zencontrol.api.protocol.ZenListener.create",
        new=AsyncMock(return_value=fake),
    ):
        await protocol.start_event_monitoring()
        assert protocol.event_task is not None
        await asyncio.wait_for(protocol.event_task, timeout=1.0)

    on_disconnect.assert_awaited_once()
    assert protocol._disconnect_notified is True
    assert protocol.event_listener is None


@pytest.mark.asyncio
async def test_listener_clean_exit_fires_on_disconnect() -> None:
    protocol = ZenProtocol()
    on_disconnect = AsyncMock()
    protocol.disconnect_callback = on_disconnect

    fake = _FakeListener()

    with patch(
        "zencontrol.api.protocol.ZenListener.create",
        new=AsyncMock(return_value=fake),
    ):
        await protocol.start_event_monitoring()
        assert protocol.event_task is not None
        await asyncio.wait_for(protocol.event_task, timeout=1.0)

    on_disconnect.assert_awaited_once()


@pytest.mark.asyncio
async def test_intentional_stop_fires_on_disconnect_once() -> None:
    zen = ZenControl()
    zen.reconnect_min_delay = 10.0  # avoid reconnect during this test
    on_disconnect = AsyncMock()
    zen.on_disconnect = on_disconnect

    # Keep the listener alive until stop cancels it
    started = asyncio.Event()

    class _BlockingListener(_FakeListener):
        async def events(self):
            started.set()
            try:
                while True:
                    await asyncio.sleep(3600)
                    yield  # pragma: no cover
            except asyncio.CancelledError:
                raise

    fake = _BlockingListener()

    with patch(
        "zencontrol.api.protocol.ZenListener.create",
        new=AsyncMock(return_value=fake),
    ):
        await zen.start()
        await asyncio.wait_for(started.wait(), timeout=1.0)
        await zen.stop()

    on_disconnect.assert_awaited_once()


@pytest.mark.asyncio
async def test_stop_after_crash_does_not_double_notify() -> None:
    zen = ZenControl()
    zen.reconnect_min_delay = 10.0  # stop before reconnect sleep elapses
    on_disconnect = AsyncMock()
    zen.on_disconnect = on_disconnect

    fake = _FakeListener(raise_error=RuntimeError("boom"))

    with patch(
        "zencontrol.api.protocol.ZenListener.create",
        new=AsyncMock(return_value=fake),
    ):
        await zen.start()
        assert zen.protocol.event_task is not None
        await asyncio.wait_for(zen.protocol.event_task, timeout=1.0)
        # Allow disconnect callback to run
        await asyncio.sleep(0.05)
        await zen.stop()

    on_disconnect.assert_awaited_once()


def _mock_controller(name: str) -> MagicMock:
    ctrl = MagicMock()
    ctrl.name = name
    ctrl.filtering = False
    return ctrl


@pytest.mark.asyncio
async def test_partial_start_closes_listener_and_disables_enabled_controllers() -> None:
    """If controller config fails mid-loop, the socket is closed and prior enables undone."""
    protocol = ZenProtocol()
    ctrl_a = _mock_controller("ctrl-a")
    ctrl_b = _mock_controller("ctrl-b")
    protocol.controllers = [ctrl_a, ctrl_b]

    fake = _FakeListener()
    enabled: list[str] = []
    disabled: list[str] = []

    async def emit(controller: MagicMock, mode: ZenEventMode | None = None) -> bool:
        assert mode is not None
        if mode.enabled:
            if controller is ctrl_b:
                raise RuntimeError("config failed")
            enabled.append(controller.name)
        else:
            disabled.append(controller.name)
        return True

    protocol.set_tpi_event_unicast_address = AsyncMock(return_value=None)
    protocol.tpi_event_emit = AsyncMock(side_effect=emit)

    with patch(
        "zencontrol.api.protocol.ZenListener.create",
        new=AsyncMock(return_value=fake),
    ):
        with pytest.raises(RuntimeError, match="config failed"):
            await protocol.start_event_monitoring()

    assert fake.closed
    assert fake.close_calls >= 1
    assert protocol.event_listener is None
    assert protocol.event_task is None
    assert enabled == ["ctrl-a"]
    assert disabled == ["ctrl-a"]


@pytest.mark.asyncio
async def test_stop_event_monitoring_is_idempotent() -> None:
    protocol = ZenProtocol()

    class _BlockingListener(_FakeListener):
        async def events(self):
            try:
                while True:
                    await asyncio.sleep(3600)
                    yield  # pragma: no cover
            except asyncio.CancelledError:
                raise

    blocking = _BlockingListener()

    with patch(
        "zencontrol.api.protocol.ZenListener.create",
        new=AsyncMock(return_value=blocking),
    ):
        protocol.set_tpi_event_unicast_address = AsyncMock(return_value=None)
        protocol.tpi_event_emit = AsyncMock(return_value=True)
        await protocol.start_event_monitoring()
        assert protocol.is_event_monitoring_active()

        await protocol.stop_event_monitoring()
        assert not protocol.is_event_monitoring_active()
        assert protocol.event_listener is None
        assert blocking.closed

        # Second stop must not raise
        await protocol.stop_event_monitoring()
        assert not protocol.is_event_monitoring_active()
