"""Event-plane session: wiring lifecycle, supervisor, and keepalive."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Protocol

from ..api.commands import ZenCommandClient
from ..api.event_decode import ZenDecodedEvent
from ..api.event_router import Lease, ZenEventReceiver
from ..api.types import Const, Transport, ZenEventMode
from ..exceptions import ZenConnectionError
from .context import ControllerRuntimeStatus, EntityContext
from .entities import ZenController
from .wiring import ZenEventWiring

EventHandler = Callable[[ZenController, ZenDecodedEvent], Awaitable[None]]
ResyncHandler = Callable[[], Awaitable[None]]
IdentifiedHandler = Callable[[ZenController, str], Awaitable[None]]
LostHandler = Callable[[ZenController, str], Awaitable[None]]
StatusHandler = Callable[[ZenController, ControllerRuntimeStatus], Awaitable[None]]


class SessionHost(Protocol):
    """Surface ``ZenSession`` needs from the composition root."""

    logger: logging.Logger
    commands: ZenCommandClient
    context: EntityContext
    event_receiver: ZenEventReceiver
    controllers: list[ZenController]
    reconnect_min_delay: float
    reconnect_max_delay: float
    reconnect_healthy_seconds: float
    event_keepalive_interval: float

    def _event_mode_for(self, controller: ZenController) -> ZenEventMode: ...
    async def assert_controller_events(self, controller: ZenController) -> bool: ...
    async def notify_disconnect(self) -> None: ...
    async def _notify_controller_identified(self, controller: ZenController, mac: str) -> None: ...
    async def _notify_controller_status(self, controller: ZenController, status: ControllerRuntimeStatus) -> None: ...
    def _forget_event_dispatch(self, name: str) -> None: ...
    def _forget_event_dispatch_all(self) -> None: ...
    def clear_entity_caches(self) -> None: ...
    async def _on_resync_callback(self) -> None: ...


class ZenSession:
    """Owns wiring, supervisor, keepalive, and session restore signalling."""

    def __init__(self, host: SessionHost, *, event_handler: EventHandler) -> None:
        self._host = host
        self._event_handler = event_handler
        self._event_task: asyncio.Task[None] | None = None
        self._disconnect_notified = False
        self._wiring: ZenEventWiring | None = None
        self._discovery_lease: Lease | None = None
        self._stopping = False
        self._supervisor_task: asyncio.Task[None] | None = None
        self._keepalive_task: asyncio.Task[None] | None = None
        self._first_connected = asyncio.Event()
        self._session_restored = asyncio.Event()
        host.event_receiver.on_leases_idle = self._session_restored.set
        host.event_receiver.on_unexpected_exit = self._on_listener_unexpected_exit

    @property
    def wiring(self) -> ZenEventWiring | None:
        return self._wiring

    @wiring.setter
    def wiring(self, value: ZenEventWiring | None) -> None:
        self._wiring = value

    @property
    def supervisor_task(self) -> asyncio.Task[None] | None:
        return self._supervisor_task

    @property
    def keepalive_task(self) -> asyncio.Task[None] | None:
        return self._keepalive_task

    @property
    def stopping(self) -> bool:
        return self._stopping

    def _is_stopping(self) -> bool:
        return self._stopping

    @property
    def event_task(self) -> asyncio.Task[None] | None:
        """Funnel consumer task for the current session."""
        h = self._host
        live = h.event_receiver.consumer_task
        if live is not None and not live.done():
            return live
        if self._event_task is not None:
            return self._event_task
        return None

    def is_event_monitoring_active(self) -> bool:
        """True while leases are held, transports are open, and the consumer runs."""
        task = self.event_task
        if task is None or task.done():
            return False
        return self._host.event_receiver.leased_transports_open()

    def _has_event_leases(self) -> bool:
        r = self._host.event_receiver
        return r.lease_count(Transport.MULTICAST) > 0 or r.lease_count(Transport.UNICAST) > 0

    async def notify_disconnect(self) -> None:
        """Fire on_disconnect at most once per monitoring session."""
        if self._disconnect_notified:
            return
        self._disconnect_notified = True
        cb = self._host.context.callbacks.on_disconnect
        if not callable(cb):
            return
        try:
            await cb()
        except Exception as err:
            self._host.logger.error(f"on_disconnect error: {err}")

    async def _on_listener_unexpected_exit(self) -> None:
        """Handle funnel consumer death. Recoverable gaps do not disconnect (I10)."""
        self._event_task = self._host.event_receiver.consumer_task
        if self._has_event_leases():
            return
        await self.notify_disconnect()

    async def start(self) -> None:
        """Start event monitoring; bindings survive receiver session restarts (I10)."""
        h = self._host
        self._stopping = False
        self._first_connected = asyncio.Event()
        self._session_restored = asyncio.Event()
        h.event_receiver.on_leases_idle = self._session_restored.set
        self._wiring = ZenEventWiring(
            h.event_receiver,
            h.commands,
            event_handler=self._event_handler,
            logger=h.logger,
        )
        self._wiring.on_resync = self._on_resync
        self._wiring.on_identified = self._on_controller_identified
        self._wiring.on_lost = self._on_binding_lost
        h.event_receiver.on_session_restored = self._on_session_restored
        if self._supervisor_task is None or self._supervisor_task.done():
            self._supervisor_task = asyncio.create_task(self._event_monitor_supervisor())
        if self._keepalive_task is None or self._keepalive_task.done():
            self._keepalive_task = asyncio.create_task(self._event_keepalive_loop())
        try:
            await asyncio.wait_for(
                self._first_connected.wait(),
                timeout=Const.START_TIMEOUT,
            )
        except TimeoutError as err:
            await self.stop()
            raise ZenConnectionError(f"Event monitoring failed to connect within {Const.START_TIMEOUT:.0f}s") from err

    async def stop(self) -> None:
        """Stop reconnect supervisor and event monitoring (keeps entity caches)."""
        await self._shutdown(close_clients=False, clear_caches=False)

    async def aclose(self) -> None:
        """Stop monitoring, cancel background work, close UDP clients, clear caches."""
        await self._shutdown(close_clients=True, clear_caches=True)

    async def _shutdown(self, *, close_clients: bool, clear_caches: bool) -> None:
        h = self._host
        self._stopping = True
        self._session_restored.set()
        was_running = self._wiring is not None
        await self._cancel_owned_tasks()
        if self._wiring is not None:
            await self._wiring.detach_all()
            self._wiring = None
        h._forget_event_dispatch_all()
        if self._discovery_lease is not None:
            await self._discovery_lease.release()
            self._discovery_lease = None
        await h.event_receiver.close()
        if close_clients:
            await h.context.cancel_background_tasks()
            await h.commands.close_all_clients()
        if was_running:
            await self.notify_disconnect()
        if clear_caches:
            h.clear_entity_caches()

    async def _cancel_owned_tasks(self) -> None:
        await self._cancel_task("_supervisor_task")
        await self._cancel_task("_keepalive_task")

    async def _cancel_task(self, attr: str) -> None:
        task: asyncio.Task[None] | None = getattr(self, attr)
        setattr(self, attr, None)
        await EntityContext.cancel_and_await(task)

    async def _attach_bindings(self) -> None:
        h = self._host
        assert self._wiring is not None
        self._disconnect_notified = False
        for controller in h.controllers:
            if self._wiring.get(controller) is not None:
                continue
            await self._wiring.attach(controller, h._event_mode_for(controller))
        if not h.controllers and self._discovery_lease is None:
            self._discovery_lease = await h.event_receiver.acquire(Transport.MULTICAST)

    async def _on_session_restored(self) -> None:
        try:
            self._disconnect_notified = False
            self._event_task = None
            if self._wiring is not None:
                await self._wiring.rearm_all()
        finally:
            self._session_restored.set()

    async def _on_resync(self) -> None:
        await self._host._on_resync_callback()

    async def _on_controller_identified(self, controller: ZenController, mac: str) -> None:
        await self._host._notify_controller_identified(controller, mac)

    async def _on_binding_lost(self, controller: ZenController, reason: str) -> None:
        h = self._host
        h.logger.error(
            "Event binding lost for %s (%s)",
            controller.name,
            reason,
        )
        h._forget_event_dispatch(controller.name)
        await h._notify_controller_status(controller, "unreachable")

    async def _event_monitor_supervisor(self) -> None:
        h = self._host
        delay = h.reconnect_min_delay
        attached = False
        connect_notified = False
        while not self._stopping:
            try:
                if not attached:
                    await self._attach_bindings()
                    attached = True
            except asyncio.CancelledError:
                raise
            except Exception as err:
                h.logger.error(f"Failed to attach event bindings: {err}")
                if self._is_stopping():
                    return
                await asyncio.sleep(delay)
                delay = min(delay * 2, h.reconnect_max_delay)
                continue

            if not connect_notified and callable(h.context.callbacks.on_connect):
                try:
                    await h.context.callbacks.on_connect()
                except Exception as err:
                    h.logger.error(f"on_connect error: {err}")
                connect_notified = True
            self._first_connected.set()
            delay = h.reconnect_min_delay

            event_task = self.event_task
            if event_task is None:
                if not self._has_event_leases():
                    return
                if await self._wait_for_session_restore(None):
                    continue
                return

            try:
                await asyncio.wait({event_task})
            except asyncio.CancelledError:
                raise

            if self._event_task is event_task:
                self._event_task = None

            if event_task.cancelled():
                h.logger.error("Event monitor consumer cancelled unexpectedly")
            elif (exc := event_task.exception()) is not None:
                h.logger.error(f"Event monitor task error: {exc}")

            if self._is_stopping():
                return

            if await self._wait_for_session_restore(event_task):
                continue
            return

    def _session_is_restored(self, dead_task: asyncio.Task[None] | None) -> bool:
        live = self._host.event_receiver.consumer_task
        if live is None or live.done():
            return False
        if dead_task is not None and live is dead_task:
            return False
        return self._host.event_receiver.leased_transports_open()

    async def _wait_for_session_restore(
        self,
        dead_task: asyncio.Task[None] | None,
        *,
        timeout: float | None = None,
    ) -> bool:
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            if self._is_stopping() or not self._has_event_leases():
                return False
            if self._session_is_restored(dead_task):
                return True

            self._session_restored.clear()
            if self._is_stopping() or not self._has_event_leases():
                return False
            if self._session_is_restored(dead_task):
                return True

            wait_timeout = None
            if deadline is not None:
                wait_timeout = deadline - time.monotonic()
                if wait_timeout <= 0:
                    return False
            try:
                await asyncio.wait_for(self._session_restored.wait(), timeout=wait_timeout)
            except TimeoutError:
                return False
            except asyncio.CancelledError:
                raise

    async def _event_keepalive_loop(self) -> None:
        h = self._host
        try:
            await self._first_connected.wait()
        except asyncio.CancelledError:
            raise
        while not self._stopping:
            try:
                await asyncio.sleep(h.event_keepalive_interval)
            except asyncio.CancelledError:
                raise
            if self._stopping or not self.is_event_monitoring_active():
                continue
            for controller in list(h.controllers):
                if self._is_stopping():
                    return
                try:
                    await h.assert_controller_events(controller)
                except asyncio.CancelledError:
                    raise
                except Exception as err:
                    h.logger.debug(
                        "Event keepalive failed for %s: %s",
                        controller.name,
                        err,
                    )
