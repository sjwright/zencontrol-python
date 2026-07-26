"""Unit tests for Cluster C event-monitor reconnect supervisor."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from unittest.mock import AsyncMock, patch

import pytest

from zencontrol.interface.interface import ZenControl
from zencontrol.io.event import ZenEvent

EventHandler = Callable[[ZenEvent], Awaitable[None]]
UnexpectedExitHandler = Callable[[], Awaitable[None]]


class _ControllableListener:
    """Listener that blocks until released, then exits cleanly (unexpected stop)."""

    def __init__(self) -> None:
        self.listen_port = 6969
        self._release = asyncio.Event()
        self.closed = False
        self.create_count = 0
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
        await self._release.wait()
        return
        yield  # pragma: no cover

    def release(self) -> None:
        self._release.set()

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
        self.closed = True


@pytest.fixture(autouse=True)
def _noop_cleanup():
    # Entity registries are per-protocol; tests call aclose/clear as needed
    yield


@pytest.mark.asyncio
async def test_supervisor_reconnects_after_listener_death() -> None:
    zen = ZenControl()
    zen.reconnect_min_delay = 0.05
    zen.reconnect_max_delay = 0.05
    zen.reconnect_healthy_seconds = 3600  # treat short sessions as unhealthy

    listeners: list[_ControllableListener] = []
    on_connect = AsyncMock()
    on_disconnect = AsyncMock()
    zen.on_connect = on_connect
    zen.on_disconnect = on_disconnect

    async def fake_create(*args, **kwargs):
        listener = _ControllableListener()
        listeners.append(listener)
        return listener

    with patch(
        "zencontrol.api.protocol.ZenListener.create",
        new=AsyncMock(side_effect=fake_create),
    ):
        await zen.start()
        assert len(listeners) == 1
        on_connect.assert_awaited_once()

        listeners[0].release()
        assert zen.protocol.event_task is not None
        await asyncio.wait_for(zen.protocol.event_task, timeout=1.0)

        for _ in range(50):
            if on_disconnect.await_count >= 1 and on_connect.await_count >= 2:
                break
            await asyncio.sleep(0.05)
        else:
            pytest.fail(
                f"reconnect did not complete: connect={on_connect.await_count} "
                f"disconnect={on_disconnect.await_count} listeners={len(listeners)}"
            )

        assert len(listeners) >= 2
        assert on_disconnect.await_count >= 1
        assert on_connect.await_count >= 2

        await zen.stop()


@pytest.mark.asyncio
async def test_stop_does_not_reconnect() -> None:
    zen = ZenControl()
    zen.reconnect_min_delay = 0.01
    started = asyncio.Event()
    create_count = 0

    class _BlockingListener(_ControllableListener):
        async def events(self):
            started.set()
            try:
                while True:
                    await asyncio.sleep(3600)
                    yield  # pragma: no cover
            except asyncio.CancelledError:
                raise

    async def fake_create(*args, **kwargs):
        nonlocal create_count
        create_count += 1
        return _BlockingListener()

    with patch(
        "zencontrol.api.protocol.ZenListener.create",
        new=AsyncMock(side_effect=fake_create),
    ):
        await zen.start()
        await asyncio.wait_for(started.wait(), timeout=1.0)
        assert create_count == 1
        await zen.stop()
        await asyncio.sleep(0.1)
        assert create_count == 1


@pytest.mark.asyncio
async def test_supervisor_cancel_does_not_reconnect() -> None:
    """HA cancels tasks on shutdown before unload sets _stopping — no reconnect."""
    zen = ZenControl()
    zen.reconnect_min_delay = 0.01
    started = asyncio.Event()
    create_count = 0

    class _BlockingListener(_ControllableListener):
        async def events(self):
            started.set()
            try:
                while True:
                    await asyncio.sleep(3600)
                    yield  # pragma: no cover
            except asyncio.CancelledError:
                raise

    async def fake_create(*args, **kwargs):
        nonlocal create_count
        create_count += 1
        return _BlockingListener()

    with patch(
        "zencontrol.api.protocol.ZenListener.create",
        new=AsyncMock(side_effect=fake_create),
    ):
        await zen.start()
        await asyncio.wait_for(started.wait(), timeout=1.0)
        assert create_count == 1
        assert zen._supervisor_task is not None

        zen._supervisor_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await zen._supervisor_task

        await asyncio.sleep(0.1)
        assert create_count == 1
        # Mimic unload after task cancel
        await zen.aclose()
