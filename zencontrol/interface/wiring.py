"""Compose event-plane subscribe/lease with command-plane emit programming.

ZenEventWiring is the only object that holds both a receiver and a command
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
from ..api.models import mac_bytes_to_str
from ..api.types import Transport, ZenEventMode
from ..utils import resolve_host
from .entities import ZenController

ResyncHandler = Callable[[], Awaitable[None]]
EventHandler = Callable[[ZenController, ZenDecodedEvent], Awaitable[None]]
IdentifiedHandler = Callable[[ZenController, str], Awaitable[None]]
LostBindingHandler = Callable[[ZenController, str], Awaitable[None]]


class CommandPlane(Protocol):
    """Minimal command surface needed to program controller event emit."""

    async def set_tpi_event_unicast_address(
        self,
        ctrl: ZenController,
        ipaddr: str | None = None,
        port: int | None = None,
    ) -> bytes | None: ...

    async def tpi_event_emit(self, ctrl: ZenController, mode: ZenEventMode | None = None) -> bool: ...


@dataclass
class ZenBinding:
    """One controller's subscription + lease + stored emit mode."""

    ctrl: ZenController
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
        self._binding_loss_tasks: set[asyncio.Task[None]] = set()

    @property
    def bindings(self) -> dict[str, ZenBinding]:
        return self._bindings

    def get(self, ctrl: ZenController | str) -> ZenBinding | None:
        name = ctrl if isinstance(ctrl, str) else ctrl.name
        return self._bindings.get(name)

    async def attach(self, ctrl: ZenController, mode: ZenEventMode) -> ZenBinding:
        name = ctrl.name
        if name in self._bindings:
            raise ValueError(f"controller already attached: {name}")

        async def handler(decoded: ZenDecodedEvent) -> None:
            await self._event_handler(ctrl, decoded)

        async def on_identified(mac: bytes) -> None:
            mac_str = mac_bytes_to_str(mac)
            was_unknown = ctrl.mac is None
            ctrl.mac = mac_str
            if was_unknown and callable(self.on_identified):
                await self.on_identified(ctrl, mac_str)

        async def on_lost(reason: str) -> None:
            # Receiver dropped routing; tear down off the consumer path so we
            # do not await command-plane work inside handle().
            self.logger.error(
                "Event subscription for %s lost (%s); detaching binding",
                ctrl.name,
                reason,
            )
            task = asyncio.create_task(self._handle_subscription_lost(ctrl, reason))
            self._binding_loss_tasks.add(task)
            task.add_done_callback(self._binding_loss_tasks.discard)

        # Resolve off the loop - never ctrl.ip / gethostbyname here (HA).
        ip = await resolve_host(ctrl.host)
        ctrl.set_resolved_ip(ip)

        sub = self._receiver.subscribe(
            handler,
            mac=ctrl.mac_bytes,
            host=ip,
            on_identified=on_identified,
            on_lost=on_lost,
        )
        try:
            lease = await self._receiver.acquire(mode.transport, toward=ip)
            try:
                await self._configure_event_delivery(ctrl, lease, mode)
            except Exception:
                await lease.release()
                raise
        except Exception:
            sub.close()
            raise

        binding = ZenBinding(
            ctrl=ctrl,
            subscription=sub,
            lease=lease,
            mode=mode,
            _wiring=self,
        )
        self._bindings[name] = binding
        return binding

    async def detach(self, ctrl: ZenController | str | ZenBinding) -> None:
        if isinstance(ctrl, ZenBinding):
            binding: ZenBinding | None = ctrl
            name = ctrl.ctrl.name
        else:
            name = ctrl if isinstance(ctrl, str) else ctrl.name
            binding = self._bindings.get(name)
        if binding is None:
            return

        self._bindings.pop(name, None)
        try:
            await self._commands.tpi_event_emit(
                binding.ctrl,
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

    async def _handle_subscription_lost(self, ctrl: ZenController, reason: str) -> None:
        """Release lease/binding after the receiver drops a subscription."""
        await self.detach(ctrl)
        if not callable(self.on_lost):
            return
        try:
            await self.on_lost(ctrl, reason)
        except Exception as err:
            self.logger.error(
                "on_lost handler error for %s (%s): %s",
                ctrl.name,
                reason,
                err,
                exc_info=True,
            )

    async def detach_all(self) -> None:
        for name in list(self._bindings):
            await self.detach(name)

    async def rearm(self, ctrl: ZenController | str) -> None:
        binding = self.get(ctrl)
        if binding is None:
            return
        await self._configure_event_delivery(binding.ctrl, binding.lease, binding.mode)

    async def rearm_all(self) -> None:
        """Replay stored modes after the receiver restores leased endpoints."""
        for binding in list(self._bindings.values()):
            try:
                await self._configure_event_delivery(binding.ctrl, binding.lease, binding.mode)
            except Exception as err:
                self.logger.error(
                    "Failed to re-arm events for %s: %s",
                    binding.ctrl.name,
                    err,
                    exc_info=True,
                )
        if callable(self.on_resync):
            try:
                await self.on_resync()
            except Exception as err:
                self.logger.error(f"on_resync error: {err}", exc_info=True)

    async def _configure_event_delivery(self, ctrl: ZenController, lease: Lease, mode: ZenEventMode) -> None:
        if mode.transport is Transport.UNICAST:
            advertise = lease.advertise
            if advertise is None:
                raise RuntimeError(
                    f"Cannot program unicast events for {ctrl.name}: no advertise address (unicast endpoint not open)"
                )
            ip, port = advertise
            await self._commands.set_tpi_event_unicast_address(ctrl, ipaddr=ip, port=port)
        else:
            await self._commands.set_tpi_event_unicast_address(ctrl, ipaddr=None, port=None)
        await self._commands.tpi_event_emit(ctrl, mode)
