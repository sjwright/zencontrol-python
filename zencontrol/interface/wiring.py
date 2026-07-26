"""Compose event-plane subscribe/lease with command-plane emit programming.

``ZenEventWiring`` is the only object that holds both a receiver and a command
client. Attach failure is contained to one controller; session restart replays
each binding's stored mode without the caller re-attaching.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Protocol

from ..api.event_decode import ZenDecodedEvent
from ..api.event_router import EventHealth, Lease, Subscription, ZenEventReceiver
from ..api.models import ZenController, mac_bytes_to_str
from ..api.types import Transport, ZenEventMode
from ..utils import resolve_host

ResyncHandler = Callable[[], Awaitable[None]]
EventHandler = Callable[[ZenController, ZenDecodedEvent], Awaitable[None]]
IdentifiedHandler = Callable[[ZenController, str], Awaitable[None]]
LostBindingHandler = Callable[[ZenController, str], Awaitable[None]]


class CommandPlane(Protocol):
    """Minimal command surface needed to program controller event emit."""

    async def set_tpi_event_unicast_address(
        self,
        controller: ZenController,
        ipaddr: str | None = None,
        port: int | None = None,
    ) -> bytes | None: ...

    async def tpi_event_emit(self, controller: ZenController, mode: ZenEventMode | None = None) -> bool: ...


@dataclass
class ZenBinding:
    """One controller's subscription + lease + stored emit mode."""

    controller: ZenController
    subscription: Subscription
    lease: Lease
    mode: ZenEventMode
    _wiring: ZenEventWiring = field(repr=False)

    @property
    def mac(self) -> bytes | None:
        return self.subscription.mac

    @property
    def event_health(self) -> EventHealth:
        return self.subscription.event_health

    @property
    def last_seen(self) -> float | None:
        return self.subscription.last_seen

    async def detach(self) -> None:
        await self._wiring.detach(self)


class ZenEventWiring:
    """Subscribe + lease + program emit; re-arm bindings after session restore."""

    def __init__(
        self,
        receiver: ZenEventReceiver,
        commands: CommandPlane,
        *,
        event_handler: EventHandler,
        logger: logging.Logger | None = None,
    ) -> None:
        self._receiver = receiver
        self._commands = commands
        self._event_handler = event_handler
        self.logger = logger or logging.getLogger(__name__)
        self.on_resync: ResyncHandler | None = None
        self.on_identified: IdentifiedHandler | None = None
        self.on_lost: LostBindingHandler | None = None
        self._bindings: dict[str, ZenBinding] = {}
        self._lost_tasks: set[asyncio.Task[None]] = set()

    @property
    def bindings(self) -> dict[str, ZenBinding]:
        return self._bindings

    def get(self, controller: ZenController | str) -> ZenBinding | None:
        name = controller if isinstance(controller, str) else controller.name
        return self._bindings.get(name)

    async def attach(self, controller: ZenController, mode: ZenEventMode) -> ZenBinding:
        name = controller.name
        if name in self._bindings:
            raise ValueError(f"controller already attached: {name}")

        async def handler(decoded: ZenDecodedEvent) -> None:
            await self._event_handler(controller, decoded)

        async def on_identified(mac: bytes) -> None:
            mac_str = mac_bytes_to_str(mac)
            was_unknown = controller.mac is None
            controller.mac = mac_str
            if was_unknown and callable(self.on_identified):
                await self.on_identified(controller, mac_str)

        async def on_lost(reason: str) -> None:
            # Receiver dropped routing; tear down off the consumer path so we
            # do not await command-plane work inside handle().
            self.logger.error(
                "Event subscription for %s lost (%s); detaching binding",
                controller.name,
                reason,
            )
            task = asyncio.create_task(self._handle_subscription_lost(controller, reason))
            self._lost_tasks.add(task)
            task.add_done_callback(self._lost_tasks.discard)

        # Resolve off the loop — never controller.ip / gethostbyname here (HA).
        ip = await resolve_host(controller.host)
        controller.set_resolved_ip(ip)

        sub = self._receiver.subscribe(
            handler,
            mac=controller.mac_bytes,
            host=ip,
            on_identified=on_identified,
            on_lost=on_lost,
        )
        try:
            lease = await self._receiver.acquire(mode.transport, toward=ip)
            try:
                await self._program(controller, lease, mode)
            except Exception:
                await lease.release()
                raise
        except Exception:
            sub.close()
            raise

        binding = ZenBinding(
            controller=controller,
            subscription=sub,
            lease=lease,
            mode=mode,
            _wiring=self,
        )
        self._bindings[name] = binding
        return binding

    async def detach(self, controller: ZenController | str | ZenBinding) -> None:
        if isinstance(controller, ZenBinding):
            binding: ZenBinding | None = controller
            name = controller.controller.name
        else:
            name = controller if isinstance(controller, str) else controller.name
            binding = self._bindings.get(name)
        if binding is None:
            return

        self._bindings.pop(name, None)
        try:
            await self._commands.tpi_event_emit(
                binding.controller,
                ZenEventMode(
                    enabled=False,
                    filtering=binding.mode.filtering,
                    transport=binding.mode.transport,
                ),
            )
        except Exception as err:
            self.logger.debug("Detach: failed to disable events on %s: %s", name, err)
        await binding.lease.release()
        binding.subscription.close()

    async def _handle_subscription_lost(self, controller: ZenController, reason: str) -> None:
        """Release lease/binding after the receiver drops a subscription."""
        await self.detach(controller)
        if not callable(self.on_lost):
            return
        try:
            await self.on_lost(controller, reason)
        except Exception as err:
            self.logger.error(
                "on_lost handler error for %s (%s): %s",
                controller.name,
                reason,
                err,
                exc_info=True,
            )

    async def detach_all(self) -> None:
        for name in list(self._bindings):
            await self.detach(name)

    async def rearm(self, controller: ZenController | str) -> None:
        binding = self.get(controller)
        if binding is None:
            return
        await self._program(binding.controller, binding.lease, binding.mode)

    async def rearm_all(self) -> None:
        """Replay stored modes after the receiver restores leased endpoints."""
        for binding in list(self._bindings.values()):
            try:
                await self._program(binding.controller, binding.lease, binding.mode)
            except Exception as err:
                self.logger.error(
                    "Failed to re-arm events for %s: %s",
                    binding.controller.name,
                    err,
                    exc_info=True,
                )
        if callable(self.on_resync):
            try:
                await self.on_resync()
            except Exception as err:
                self.logger.error(f"on_resync error: {err}", exc_info=True)

    async def _program(self, controller: ZenController, lease: Lease, mode: ZenEventMode) -> None:
        if mode.transport is Transport.UNICAST:
            advertise = lease.advertise
            if advertise is None:
                raise RuntimeError(
                    f"Cannot program unicast events for {controller.name}: no advertise address (unicast endpoint not open)"
                )
            ip, port = advertise
            await self._commands.set_tpi_event_unicast_address(controller, ipaddr=ip, port=port)
        else:
            await self._commands.set_tpi_event_unicast_address(controller, ipaddr=None, port=None)
        await self._commands.tpi_event_emit(controller, mode)
