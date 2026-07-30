"""
===================================================================================
High-level interface: ZenControl composition root.
===================================================================================

ZenControl is the main entry point for the ZenControl library.
It provides the high-level API for interacting with the ZenControl system.
It is responsible for:
- Discovering controllers
- Adding and removing controllers
- Configuring event monitoring (wiring, supervisor, keepalive)
- Discovering entities (lights, groups, scenes, etc.)
- Maintaining lists of entities by controller
"""


from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Self

from ..api import (
    ZenAddress,
    ZenInstance,
    ZenInstanceType,
)
from ..api.commands import ZenCommandClient
from ..api.event_decode import ZenDecodedEvent
from ..api.event_router import EventHealth, Lease, ZenEventReceiver
from ..api.models import DiscoveredController
from ..api.types import Const, Transport, ZenEventMode
from ..exceptions import ZenConnectionError
from .context import ControllerRuntimeStatus, EntityContext, ZenCallbacks
from .discovery import ControllerDiscovery
from .dispatch import EventDispatcher
from .entities import (
    ZenAbsoluteInput,
    ZenButton,
    ZenController,
    ZenGroup,
    ZenLight,
    ZenMotionSensor,
    ZenProfile,
    ZenSystemVariable,
)
from .wiring import ZenEventWiring


def _assign_light_sub_labels(lights: list[ZenLight] | set[ZenLight]) -> None:
    """Derive sub_label for lights that share a comma-separated label.

    Controllers sometimes store one label string across several ECGs that share
    a fitting, e.g. "Hallway,Bathroom,,Annex" on addresses 31-34 meaning
    31=Hallway, 32=Bathroom, 33 unused, 34=Annex.

    Only applied when multiple lights share an identical label that contains a
    comma. Clusters are sorted by address number; empty segments become
    Unused {number}. Lights outside such clusters keep sub_label=None.
    """
    for light in lights:
        light.sub_label = None

    clusters: dict[tuple[str, str], list[ZenLight]] = {}
    for light in lights:
        label = light.label
        if not label or "," not in label:
            continue
        key = (light.address.controller.name, label)
        clusters.setdefault(key, []).append(light)

    for cluster in clusters.values():
        if len(cluster) < 2:
            continue
        cluster.sort(key=lambda lt: lt.address.number)
        parts = [part.strip() for part in (cluster[0].label or "").split(",")]
        for i, light in enumerate(cluster):
            part = parts[i] if i < len(parts) else ""
            light.sub_label = part if part else f"Unused {light.address.number}"


ZenEcdEntity = ZenButton | ZenMotionSensor | ZenAbsoluteInput


class ZenControl:
    def __init__(
        self,
        logger: logging.Logger | None = None,
        print_traffic: bool = False,
        unicast: bool = False,
        listen_ip: str | None = None,
        listen_port: int | None = None,
    ):
        self.logger = logger or logging.getLogger(__name__)
        # Preferred TPI event emit transport (not a socket bind).
        self.unicast = unicast
        self.commands: ZenCommandClient = ZenCommandClient(
            logger=self.logger,
            print_traffic=print_traffic,
        )
        self.context = EntityContext(commands=self.commands, logger=self.logger)
        self.controllers: list[ZenController] = []
        listen_ip_val = listen_ip if listen_ip else "0.0.0.0"
        self.event_receiver = ZenEventReceiver(
            logger=self.logger,
            unicast_listen_ip=listen_ip_val if unicast else "0.0.0.0",
            unicast_port=(listen_port if listen_port else 0) if unicast else 0,
        )
        self.identities = self.event_receiver.identities
        self.identities.on_discovered = self._forward_discovered
        self._enrich_locks: dict[str, asyncio.Lock] = {}
        self.reconnect_min_delay = Const.RECONNECT_MIN_DELAY
        self.reconnect_max_delay = Const.RECONNECT_MAX_DELAY
        self.reconnect_healthy_seconds = Const.RECONNECT_HEALTHY_SECONDS
        self.event_keepalive_interval = Const.EVENT_KEEPALIVE_INTERVAL

        self._dispatcher = EventDispatcher(self.context, self.logger)
        self._discovery = ControllerDiscovery(self)

        # Event-plane session state (wiring, supervisor, keepalive).
        self._wiring: ZenEventWiring | None = None
        self._event_task: asyncio.Task[None] | None = None
        self._disconnect_notified = False
        self._discovery_lease: Lease | None = None
        self._stopping = False
        self._supervisor_task: asyncio.Task[None] | None = None
        self._keepalive_task: asyncio.Task[None] | None = None
        self._first_connected = asyncio.Event()
        self._session_restored = asyncio.Event()
        self.event_receiver.on_leases_idle = self._session_restored.set
        self.event_receiver.on_unexpected_exit = self._on_listener_unexpected_exit

        # Shared ECD instance list per controller; reused by get_instances
        # until clear_entity_caches().
        self._ecd_instances_by_controller: dict[str, list[ZenInstance]] = {}

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        await self.aclose()

    @property
    def wiring(self) -> ZenEventWiring | None:
        """Active event bindings, or None when monitoring is stopped."""
        return self._wiring

    @wiring.setter
    def wiring(self, value: ZenEventWiring | None) -> None:
        self._wiring = value

    @property
    def event_task(self) -> asyncio.Task[None] | None:
        """Funnel consumer task for the current session."""
        live = self.event_receiver.consumer_task
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
        return self.event_receiver.leased_transports_open()

    def is_session_running(self) -> bool:
        """True when the session supervisor is alive (may still be binding)."""
        task = self._supervisor_task
        return task is not None and not task.done()

    def _has_event_leases(self) -> bool:
        r = self.event_receiver
        return r.lease_count(Transport.MULTICAST) > 0 or r.lease_count(Transport.UNICAST) > 0

    async def notify_disconnect(self) -> None:
        """Fire on_disconnect at most once per monitoring session."""
        if self._disconnect_notified:
            return
        self._disconnect_notified = True
        cb = self.context.callbacks.on_disconnect
        if not callable(cb):
            return
        try:
            await cb()
        except Exception as err:
            self.logger.error(f"on_disconnect error: {err}")

    async def _on_listener_unexpected_exit(self) -> None:
        """Handle funnel consumer death. Recoverable gaps do not disconnect (I10)."""
        self._event_task = self.event_receiver.consumer_task
        if self._has_event_leases():
            return
        await self.notify_disconnect()

    async def start(self) -> None:
        """Start event monitoring; bindings survive receiver session restarts (I10)."""
        self._stopping = False
        self._first_connected = asyncio.Event()
        self._session_restored = asyncio.Event()
        self.event_receiver.on_leases_idle = self._session_restored.set
        self._wiring = ZenEventWiring(
            self.event_receiver,
            self.commands,
            event_handler=self._on_controller_event,
            logger=self.logger,
        )
        self._wiring.on_resync = self._on_resync_callback
        self._wiring.on_identified = self._notify_controller_identified
        self._wiring.on_lost = self._on_binding_lost
        self.event_receiver.on_session_restored = self._on_session_restored
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
        self._stopping = True
        self._session_restored.set()
        was_running = self._wiring is not None
        await self._cancel_owned_tasks()
        if self._wiring is not None:
            await self._wiring.detach_all()
            self._wiring = None
        self._forget_event_dispatch_all()
        if self._discovery_lease is not None:
            await self._discovery_lease.release()
            self._discovery_lease = None
        await self.event_receiver.close()
        if close_clients:
            await self.context.cancel_background_tasks()
            await self.commands.close_all_clients()
        if was_running:
            await self.notify_disconnect()
        if clear_caches:
            self.clear_entity_caches()

    async def _cancel_owned_tasks(self) -> None:
        await self._cancel_task("_supervisor_task")
        await self._cancel_task("_keepalive_task")

    async def _cancel_task(self, attr: str) -> None:
        task: asyncio.Task[None] | None = getattr(self, attr)
        setattr(self, attr, None)
        await EntityContext.cancel_and_await(task)

    async def _attach_bindings(self) -> None:
        assert self._wiring is not None
        self._disconnect_notified = False
        for controller in self.controllers:
            if self._wiring.get(controller) is not None:
                continue
            await self._wiring.attach(controller, self._event_mode_for(controller))
        if not self.controllers and self._discovery_lease is None:
            self._discovery_lease = await self.event_receiver.acquire(Transport.MULTICAST)

    async def _on_session_restored(self) -> None:
        try:
            self._disconnect_notified = False
            self._event_task = None
            if self._wiring is not None:
                await self._wiring.rearm_all()
        finally:
            self._session_restored.set()

    async def _on_binding_lost(self, controller: ZenController, reason: str) -> None:
        self.logger.error(
            "Event binding lost for %s (%s)",
            controller.name,
            reason,
        )
        self._forget_event_dispatch(controller.name)
        await self._notify_controller_status(controller, "unreachable")

    async def _event_monitor_supervisor(self) -> None:
        delay = self.reconnect_min_delay
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
                self.logger.error(f"Failed to attach event bindings: {err}")
                if self._stopping:
                    return
                await asyncio.sleep(delay)
                delay = min(delay * 2, self.reconnect_max_delay)
                continue

            if not connect_notified and callable(self.context.callbacks.on_connect):
                try:
                    await self.context.callbacks.on_connect()
                except Exception as err:
                    self.logger.error(f"on_connect error: {err}")
                connect_notified = True
            self._first_connected.set()
            delay = self.reconnect_min_delay

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
                self.logger.error("Event monitor consumer cancelled unexpectedly")
            elif (exc := event_task.exception()) is not None:
                self.logger.error(f"Event monitor task error: {exc}")

            if self._stopping:
                return

            if await self._wait_for_session_restore(event_task):
                continue
            return

    def _session_is_restored(self, dead_task: asyncio.Task[None] | None) -> bool:
        live = self.event_receiver.consumer_task
        if live is None or live.done():
            return False
        if dead_task is not None and live is dead_task:
            return False
        return self.event_receiver.leased_transports_open()

    async def _wait_for_session_restore(
        self,
        dead_task: asyncio.Task[None] | None,
        *,
        timeout: float | None = None,
    ) -> bool:
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            if self._stopping or not self._has_event_leases():
                return False
            if self._session_is_restored(dead_task):
                return True

            self._session_restored.clear()
            if self._stopping or not self._has_event_leases():
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
        try:
            await self._first_connected.wait()
        except asyncio.CancelledError:
            raise
        while not self._stopping:
            try:
                await asyncio.sleep(self.event_keepalive_interval)
            except asyncio.CancelledError:
                raise
            if self._stopping or not self.is_event_monitoring_active():
                continue
            for controller in list(self.controllers):
                if self._stopping:
                    return
                try:
                    await self.assert_controller_events(controller)
                except asyncio.CancelledError:
                    raise
                except Exception as err:
                    self.logger.debug(
                        "Event keepalive failed for %s: %s",
                        controller.name,
                        err,
                    )

    async def _on_controller_event(self, controller: ZenController, ev: ZenDecodedEvent) -> None:
        await self._dispatcher.handle(controller, ev)

    def _forget_event_dispatch(self, name: str) -> None:
        self._dispatcher.forget(name)

    def _forget_event_dispatch_all(self) -> None:
        self._dispatcher.clear()

    async def _on_resync_callback(self) -> None:
        callback = self.callbacks.on_resync
        if not callable(callback):
            return
        try:
            await callback()
        except Exception as err:
            self.logger.error(f"on_resync error: {err}", exc_info=True)

    async def enrich_discovered(self, discovered: DiscoveredController) -> DiscoveredController:
        return await self._discovery.enrich_discovered(discovered)

    async def discover(self, timeout: float = 5.0) -> list[DiscoveredController]:
        return await self._discovery.discover(timeout=timeout)

    def clear_entity_caches(self) -> None:
        """Clear entity singleton registries for this ZenControl instance."""
        self.context.clear_entity_caches()
        self._ecd_instances_by_controller.clear()

    @property
    def callbacks(self) -> ZenCallbacks:
        """Application callback registry (same object as ``context.callbacks``)."""
        return self.context.callbacks

    @property
    def discovered_controllers(self) -> list[DiscoveredController]:
        """Controllers identified from multicast but not yet registered."""
        return list(self.identities.discovered)

    def event_health_for(self, controller: ZenController | str) -> EventHealth | None:
        """Per-binding event-plane health, or None if the controller is not attached."""
        if self._wiring is None:
            return None
        binding = self._wiring.get(controller)
        return None if binding is None else binding.event_health

    async def _forward_discovered(self, discovered: DiscoveredController) -> None:
        callback = self.context.callbacks.controller_discovered
        if not callable(callback):
            return
        try:
            await callback(discovered)
        except Exception as err:
            self.logger.error("controller_discovered callback error: %s", err, exc_info=err)

    async def _notify_controller_identified(self, controller: ZenController, mac: str) -> None:
        callback = self.context.callbacks.controller_identified
        if not callable(callback):
            return
        try:
            await callback(controller, mac)
        except Exception as err:
            self.logger.error("controller_identified callback error: %s", err, exc_info=err)

    def add_controller(
        self, id: int, name: str, label: str, host: str, port: int = 5108, mac: str | None = None, filtering: bool = False
    ) -> ZenController:
        controller = ZenController(
            ctx=self.context, id=id, name=name, label=label, host=host, port=port, mac=mac, filtering=filtering
        )
        self.controllers.append(controller)
        self.identities.forget(host=host, mac=mac)
        return controller

    async def remove_controller(self, controller: ZenController | str) -> None:
        """Detach a controller and close its command client.

        Safe to call while event monitoring is running. Does not stop the shared
        listener; callers that own the last controller should ``aclose()``.
        """
        name = controller if isinstance(controller, str) else controller.name
        removed = [c for c in self.controllers if c.name == name]
        self.controllers = [c for c in self.controllers if c.name != name]
        if self._wiring is not None:
            await self._wiring.detach(name)
        self._forget_event_dispatch(name)
        self.context.purge_controller_entities(name)
        for ctrl in removed:
            await self.commands._invalidate_client(ctrl)

    def _event_mode_for(self, controller: ZenController) -> ZenEventMode:
        return ZenEventMode(
            enabled=True,
            filtering=controller.filtering,
            transport=(Transport.UNICAST if self.unicast else Transport.MULTICAST),
        )

    async def configure_controller_events(self, controller: ZenController) -> bool:
        """Enable TPI event emit for one controller using this client's listen mode.

        Call after ``add_controller`` when event monitoring is already running so a
        newly attached controller joins the shared listener. Returns True when the
        emit-enable command succeeds.
        """
        if self._wiring is None:
            return False
        mode = self._event_mode_for(controller)
        try:
            if self._wiring.get(controller) is not None:
                await self._wiring.rearm(controller)
            else:
                await self._wiring.attach(controller, mode)
            return True
        except Exception as err:
            self.logger.debug(
                "configure_controller_events failed for %s: %s",
                controller.name,
                err,
            )
            return False

    async def assert_controller_events(self, controller: ZenController) -> bool:
        """Ping event emit state and re-assert config if the controller lost it.

        Controllers that reboot while our listener stays up typically come back
        with events disabled (or with a stale unicast target). Returns True when
        the controller is reachable and events are confirmed/enabled, False when
        the ping timed out / failed or re-assert could not enable emit.

        Never re-asserts while ``is_controller_ready()`` is false — the startup
        sequence can take several minutes after a reboot.
        """
        if not self.is_event_monitoring_active():
            return False
        if self._wiring is not None and self._wiring.get(controller) is None:
            # Binding was dropped (e.g. MAC promotion conflict) — do not keep
            # confirming emit into a route that no longer exists.
            self.logger.debug(
                "No event binding for %s — skipping emit keepalive",
                controller.name,
            )
            return False

        ready = await controller.is_controller_ready()
        if ready is None:
            self.logger.debug(
                "No response from %s during event keepalive ping",
                controller.name,
            )
            await self._notify_controller_status(controller, "unreachable")
            return False
        if ready is not True:
            self.logger.debug(
                "Controller %s still starting — deferring event re-assert",
                controller.name,
            )
            await self._notify_controller_status(controller, "starting")
            return True

        unicast = self.unicast
        needs_reassert = False
        info = await self.commands.query_tpi_event_unicast_address(controller)
        if info is not None:
            mode = info["mode"]
            if not mode.enabled or bool(mode.unicast) != unicast:
                needs_reassert = True
            elif unicast and self._unicast_target_mismatch(controller, info):
                needs_reassert = True
        else:
            enabled = await self.commands.query_tpi_event_emit_state(controller)
            if enabled is None:
                self.logger.debug(
                    "No response from %s during event keepalive ping",
                    controller.name,
                )
                await self._notify_controller_status(controller, "unreachable")
                return False
            needs_reassert = not enabled

        if needs_reassert:
            self.logger.info(
                "Controller %s TPI events not correctly enabled — re-asserting",
                controller.name,
            )
            if not await self.configure_controller_events(controller):
                self.logger.warning(
                    "Failed to re-assert TPI events for %s",
                    controller.name,
                )
                await self._notify_controller_status(controller, "unreachable")
                return False
        await self._notify_controller_status(controller, "online")
        return True

    def _unicast_target_mismatch(self, controller: ZenController, info: dict[str, Any]) -> bool:
        """True when the controller's programmed unicast target is wrong for it.

        Compares against that controller's binding advertise (per-``toward``).
        Without a live advertise there is nothing to compare — return False.
        """
        if self._wiring is None:
            return False
        binding = self._wiring.get(controller)
        advertise = None if binding is None else binding.lease.advertise
        if advertise is None:
            return False
        expected_ip, expected_port = advertise
        return info.get("port") != expected_port or info.get("ip") != expected_ip

    async def _notify_controller_status(self, controller: ZenController, status: ControllerRuntimeStatus) -> None:
        """Notify listeners of online / starting / unreachable."""
        callback = self.callbacks.controller_status_change
        if not callable(callback):
            return
        try:
            await callback(controller, status)
        except Exception as err:
            self.logger.debug(
                "controller_status_change error for %s: %s",
                controller.name,
                err,
            )

    async def get_profiles(self, controller: ZenController | None = None) -> set[ZenProfile]:
        """Return a set of all profiles."""
        profiles: set[ZenProfile] = set()
        controllers = [controller] if controller else self.controllers
        for ctrl in controllers:
            numbers = await self.commands.query_profile_numbers(controller=ctrl)
            if numbers is None:
                continue
            for number in numbers:
                profile = await ZenProfile.create(ctx=self.context, controller=ctrl, number=number)
                profiles.add(profile)
        return profiles

    async def get_groups(self, controller: ZenController | None = None) -> set[ZenGroup]:
        """Return a set of all groups (optionally for one controller)."""
        groups: set[ZenGroup] = set()
        controllers = [controller] if controller else self.controllers
        for ctrl in controllers:
            addresses = await self.commands.query_group_numbers(controller=ctrl)
            for address in addresses:
                group = await ZenGroup.create(ctx=self.context, address=address)
                groups.add(group)
        return groups

    async def get_lights(self, controller: ZenController | None = None) -> set[ZenLight]:
        """Return a set of all lights available (optionally for one controller)."""
        lights: set[ZenLight] = set()
        controllers = [controller] if controller else self.controllers
        for ctrl in controllers:
            addresses = await self.commands.query_control_gear_dali_addresses(controller=ctrl)
            for address in addresses:
                light = await ZenLight.create(ctx=self.context, address=address)
                lights.add(light)
        # Second pass: labels are known; split shared comma-labels into sub_labels.
        _assign_light_sub_labels(lights)
        return lights

    async def _get_addresses_with_instances(self, controller: ZenController) -> list[ZenAddress]:
        """Return all DALI addresses that have instances (full address-space scan)."""
        return await self.commands.query_dali_addresses_with_instances(controller)

    async def _scan_ecd_instances(self, controller: ZenController) -> list[ZenInstance]:
        """Return every ECD instance on ``controller`` (one query per address).

        Results are cached until ``clear_entity_caches()`` so repeated
        ``get_instances`` calls share a single address-space scan.
        """
        cached = self._ecd_instances_by_controller.get(controller.name)
        if cached is not None:
            return cached
        instances: list[ZenInstance] = []
        for address in await self._get_addresses_with_instances(controller):
            instances.extend(await self.commands.query_instances_by_address(address=address))
        self._ecd_instances_by_controller[controller.name] = instances
        return instances

    async def get_instances(self, controller: ZenController | None = None) -> set[ZenEcdEntity]:
        """Interview all ECD instances (buttons, motion sensors, absolute inputs)."""
        entities: set[ZenEcdEntity] = set()
        controllers = [controller] if controller else self.controllers
        for ctrl in controllers:
            for instance in await self._scan_ecd_instances(ctrl):
                match instance.type:
                    case ZenInstanceType.PUSH_BUTTON:
                        entities.add(await ZenButton.create(ctx=self.context, instance=instance))
                    case ZenInstanceType.OCCUPANCY_SENSOR:
                        entities.add(await ZenMotionSensor.create(ctx=self.context, instance=instance))
                    case ZenInstanceType.ABSOLUTE_INPUT:
                        entities.add(await ZenAbsoluteInput.create(ctx=self.context, instance=instance))
                    case _:
                        continue
        return entities

    async def get_buttons(self, controller: ZenController | None = None) -> set[ZenButton]:
        """Return push-button instances (prefer ``get_instances``)."""
        return {e for e in await self.get_instances(controller) if isinstance(e, ZenButton)}

    async def get_motion_sensors(self, controller: ZenController | None = None) -> set[ZenMotionSensor]:
        """Return occupancy-sensor instances (prefer ``get_instances``)."""
        return {e for e in await self.get_instances(controller) if isinstance(e, ZenMotionSensor)}

    async def get_absolute_inputs(self, controller: ZenController | None = None) -> set[ZenAbsoluteInput]:
        """Return absolute-input instances (prefer ``get_instances``)."""
        return {e for e in await self.get_instances(controller) if isinstance(e, ZenAbsoluteInput)}

    async def get_system_variables(
        self,
        give_up_after: int = 10,
        controller: ZenController | None = None,
    ) -> set[ZenSystemVariable]:
        """Return labelled system variables (optionally for one controller)."""
        sysvars: set[ZenSystemVariable] = set()
        controllers = [controller] if controller else self.controllers
        for ctrl in controllers:
            failed_attempts = 0
            for variable in range(Const.MAX_SYSVAR):
                label = await self.commands.query_system_variable_name(controller=ctrl, variable=variable)
                if label:
                    failed_attempts = 0
                    sysvar = await ZenSystemVariable.create(ctx=self.context, controller=ctrl, id=variable, label=label)
                    sysvars.add(sysvar)
                else:
                    failed_attempts += 1
                    if failed_attempts >= give_up_after:
                        break
        return sysvars


__all__ = ["ZenControl", "ControllerRuntimeStatus"]
