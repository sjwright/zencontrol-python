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
    ZenInstance,
    ZenInstanceType,
)
from ..api.commands import ZenCommandClient
from ..api.event_decode import ZenDecodedEvent
from ..api.event_router import EventHealth, Lease, ZenEventReceiver
from ..api.models import DiscoveredController
from ..api.const import Const as ApiConst
from ..api.types import Transport, ZenEventMode
from ..api.types import TpiEventUnicastAddress
from ..exceptions import ZenConnectionError
from .const import Const
from .context import ControllerRuntimeStatus, EntityContext, ZenCallbacks
from .discovery import ControllerDiscovery
from .dispatch import EventDispatcher
from .entities import (
    ZenAbsoluteInput,
    ZenBlind,
    ZenButton,
    ZenControlGear,
    ZenController,
    ZenFan,
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
        key = (light.address.ctrl.name, label)
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
        listen_ip: str | None = None,
        listen_port: int | None = None,
    ):
        self.logger = logger or logging.getLogger(__name__)
        self.commands: ZenCommandClient = ZenCommandClient(
            logger=self.logger,
            print_traffic=print_traffic,
        )
        self.ctx = EntityContext(commands=self.commands, logger=self.logger)
        self.controllers: list[ZenController] = []
        # listen_* apply when any controller uses unicast events.
        self.event_receiver = ZenEventReceiver(
            logger=self.logger,
            unicast_listen_ip=listen_ip if listen_ip else "0.0.0.0",
            unicast_port=listen_port if listen_port else 0,
        )
        self.identities = self.event_receiver.identities
        self.identities.on_discovered = self._forward_discovered
        self._enrich_locks: dict[str, asyncio.Lock] = {}
        self.reconnect_min_delay = Const.RECONNECT_MIN_DELAY
        self.reconnect_max_delay = Const.RECONNECT_MAX_DELAY
        self.reconnect_healthy_seconds = Const.RECONNECT_HEALTHY_SECONDS
        self.event_keepalive_interval = Const.EVENT_KEEPALIVE_INTERVAL

        self._dispatcher = EventDispatcher(self.ctx, self.logger)
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
        await self._await_callback(self.ctx.callbacks.on_disconnect, what="on_disconnect")

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
            await self.ctx.cancel_background_tasks()
            await self.commands.close_all_clients()
        if was_running:
            await self.notify_disconnect()
        if clear_caches:
            self.clear_entity_caches()

    async def _cancel_owned_tasks(self) -> None:
        supervisor, self._supervisor_task = self._supervisor_task, None
        keepalive, self._keepalive_task = self._keepalive_task, None
        await EntityContext.cancel_and_await(supervisor)
        await EntityContext.cancel_and_await(keepalive)

    async def _await_callback(
        self,
        callback: Any,
        *args: Any,
        what: str,
        debug: bool = False,
        exc_info: bool = False,
    ) -> None:
        if not callable(callback):
            return
        try:
            await callback(*args)
        except Exception as err:
            if debug:
                self.logger.debug("%s error: %s", what, err)
            else:
                self.logger.error("%s error: %s", what, err, exc_info=exc_info)

    async def _attach_bindings(self) -> None:
        assert self._wiring is not None
        self._disconnect_notified = False
        for ctrl in self.controllers:
            if self._wiring.get(ctrl) is not None:
                continue
            await self._wiring.attach(ctrl, self._event_mode_for(ctrl))
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

    async def _on_binding_lost(self, ctrl: ZenController, reason: str) -> None:
        self.logger.error(
            "Event binding lost for %s (%s)",
            ctrl.name,
            reason,
        )
        self._forget_event_dispatch(ctrl.name)
        await self._notify_controller_status(ctrl, "unreachable")

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

            if not connect_notified:
                await self._await_callback(self.ctx.callbacks.on_connect, what="on_connect")
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
            for ctrl in list(self.controllers):
                if self._stopping:
                    return
                try:
                    await self.assert_controller_events(ctrl)
                except asyncio.CancelledError:
                    raise
                except Exception as err:
                    self.logger.debug(
                        "Event keepalive failed for %s: %s",
                        ctrl.name,
                        err,
                    )

    async def _on_controller_event(self, ctrl: ZenController, ev: ZenDecodedEvent) -> None:
        await self._dispatcher.handle(ctrl, ev)

    def _forget_event_dispatch(self, name: str) -> None:
        self._dispatcher.forget(name)

    def _forget_event_dispatch_all(self) -> None:
        self._dispatcher.clear()

    async def _on_resync_callback(self) -> None:
        await self._await_callback(
            self.callbacks.on_resync, what="on_resync", exc_info=True,
        )

    async def enrich_discovered(self, discovered: DiscoveredController) -> DiscoveredController:
        return await self._discovery.enrich_discovered(discovered)

    async def discover(self, timeout: float = 5.0) -> list[DiscoveredController]:
        return await self._discovery.discover(timeout=timeout)

    def clear_entity_caches(self) -> None:
        """Clear entity singleton registries for this ZenControl instance."""
        self.ctx.clear_entity_caches()
        self._ecd_instances_by_controller.clear()

    @property
    def callbacks(self) -> ZenCallbacks:
        """Application callback registry (same object as ctx.callbacks)."""
        return self.ctx.callbacks

    @property
    def discovered_controllers(self) -> list[DiscoveredController]:
        """Controllers identified from multicast but not yet registered."""
        return list(self.identities.discovered)

    def event_health_for(self, ctrl: ZenController | str) -> EventHealth | None:
        """Per-binding event-plane health, or None if the controller is not attached."""
        if self._wiring is None:
            return None
        binding = self._wiring.get(ctrl)
        return None if binding is None else binding.event_health

    async def _forward_discovered(self, discovered: DiscoveredController) -> None:
        await self._await_callback(
            self.ctx.callbacks.controller_discovered,
            discovered,
            what="controller_discovered",
            exc_info=True,
        )

    async def _notify_controller_identified(self, ctrl: ZenController, mac: str) -> None:
        await self._await_callback(
            self.ctx.callbacks.controller_identified,
            ctrl,
            mac,
            what="controller_identified",
            exc_info=True,
        )

    def add_controller(
        self,
        id: int,
        name: str,
        label: str,
        host: str,
        port: int = 5108,
        mac: str | None = None,
        filtering: bool = False,
        tcp: bool = False,
        unicast: bool = False,
    ) -> ZenController:
        ctrl = self.ctx.ctrl(
            id=id,
            name=name,
            label=label,
            host=host,
            port=port,
            mac=mac,
            filtering=filtering,
            tcp=tcp,
            unicast=unicast,
        )
        self.controllers.append(ctrl)
        self.identities.forget(host=host, mac=mac)
        return ctrl

    async def remove_controller(self, ctrl: ZenController | str) -> None:
        """Detach a controller and close its command client.

        Safe to call while event monitoring is running. Does not stop the shared
        listener; callers that own the last controller should aclose().
        """
        name = ctrl if isinstance(ctrl, str) else ctrl.name
        removed = [c for c in self.controllers if c.name == name]
        self.controllers = [c for c in self.controllers if c.name != name]
        if self._wiring is not None:
            await self._wiring.detach(name)
        self._forget_event_dispatch(name)
        self.ctx.purge_controller_entities(name)
        for ctrl in removed:
            await self.commands._invalidate_client(ctrl)

    def _event_mode_for(self, ctrl: ZenController) -> ZenEventMode:
        # Bool until IO: Transport is the lease/emit key used at the receiver.
        return ZenEventMode(
            enabled=True,
            filtering=ctrl.filtering,
            transport=(Transport.UNICAST if ctrl.unicast else Transport.MULTICAST),
        )

    async def configure_controller_events(self, ctrl: ZenController) -> bool:
        """Enable TPI event emit for one controller using its unicast/multicast setting.

        Call after add_controller when event monitoring is already running so a
        newly attached controller joins the shared listener. Returns True when the
        emit-enable command succeeds.
        """
        if self._wiring is None:
            return False
        mode = self._event_mode_for(ctrl)
        try:
            if self._wiring.get(ctrl) is not None:
                await self._wiring.rearm(ctrl)
            else:
                await self._wiring.attach(ctrl, mode)
            return True
        except Exception as err:
            self.logger.debug(
                "configure_controller_events failed for %s: %s",
                ctrl.name,
                err,
            )
            return False

    async def assert_controller_events(self, ctrl: ZenController) -> bool:
        """Ping event emit state and re-assert config if the controller lost it.

        Controllers that reboot while our listener stays up typically come back
        with events disabled (or with a stale unicast target). Returns True when
        the controller is reachable and events are confirmed/enabled, False when
        the ping timed out / failed or re-assert could not enable emit.

        Never re-asserts while query_controller_startup_complete() is false - the startup
        sequence can take several minutes after a reboot.
        """
        if not self.is_event_monitoring_active():
            return False
        if self._wiring is not None and self._wiring.get(ctrl) is None:
            # Binding was dropped (e.g. MAC promotion conflict) - do not keep
            # confirming emit into a route that no longer exists.
            self.logger.debug(
                "No event binding for %s - skipping emit keepalive",
                ctrl.name,
            )
            return False

        ready = await self.commands.query_controller_startup_complete(ctrl)
        if ready is None:
            self.logger.debug(
                "No response from %s during event keepalive ping",
                ctrl.name,
            )
            await self._notify_controller_status(ctrl, "unreachable")
            return False
        if ready is not True:
            self.logger.debug(
                "Controller %s still starting - deferring event re-assert",
                ctrl.name,
            )
            await self._notify_controller_status(ctrl, "starting")
            return True

        unicast = ctrl.unicast
        needs_reassert = False
        info = await self.commands.query_tpi_event_unicast_address(ctrl)
        if info is not None:
            mode = info.mode
            if not mode.enabled or bool(mode.unicast) != unicast:
                needs_reassert = True
            elif unicast and self._unicast_target_mismatch(ctrl, info):
                needs_reassert = True
        else:
            enabled = await self.commands.query_tpi_event_emit_state(ctrl)
            if enabled is None:
                self.logger.debug(
                    "No response from %s during event keepalive ping",
                    ctrl.name,
                )
                await self._notify_controller_status(ctrl, "unreachable")
                return False
            needs_reassert = not enabled

        if needs_reassert:
            self.logger.info(
                "Controller %s TPI events not correctly enabled - re-asserting",
                ctrl.name,
            )
            if not await self.configure_controller_events(ctrl):
                self.logger.warning(
                    "Failed to re-assert TPI events for %s",
                    ctrl.name,
                )
                await self._notify_controller_status(ctrl, "unreachable")
                return False
        await self._notify_controller_status(ctrl, "online")
        return True

    def _unicast_target_mismatch(self, ctrl: ZenController, info: TpiEventUnicastAddress) -> bool:
        """True when the controller's programmed unicast target is wrong for it.

        Compares against that controller's binding advertise (per-toward).
        Without a live advertise there is nothing to compare - return False.
        """
        if self._wiring is None:
            return False
        binding = self._wiring.get(ctrl)
        advertise = None if binding is None else binding.lease.advertise
        if advertise is None:
            return False
        expected_ip, expected_port = advertise
        return info.port != expected_port or info.ip != expected_ip

    async def _notify_controller_status(self, ctrl: ZenController, status: ControllerRuntimeStatus) -> None:
        """Notify listeners of online / starting / unreachable."""
        await self._await_callback(
            self.callbacks.controller_status_change,
            ctrl,
            status,
            what=f"controller_status_change for {ctrl.name}",
            debug=True,
        )

    async def get_profiles(self, ctrl: ZenController | None = None) -> set[ZenProfile]:
        """Return a set of all profiles."""
        profiles: set[ZenProfile] = set()
        controllers = [ctrl] if ctrl else self.controllers
        for ctrl in controllers:
            numbers = await self.commands.query_profile_numbers(ctrl=ctrl)
            if numbers is None:
                continue
            for number in numbers:
                profile = await self.ctx.create_profile(ctrl, number)
                profiles.add(profile)
        return profiles

    async def switch_to_profile(self, ctrl: ZenController, profile: ZenProfile | int | str) -> bool:
        """Switch controller to a profile by object, number, or label."""
        zp: ZenProfile | None = None
        if isinstance(profile, ZenProfile):
            zp = profile
        elif isinstance(profile, str):
            for key, p in self.ctx.registry.profiles.items():
                if key[0] == ctrl.name and p.label == profile:
                    zp = p
                    break
        elif isinstance(profile, int):
            zp = self.ctx.registry.profiles.get((ctrl.name, profile))
        if zp is None:
            return False
        self.commands.logger.debug("Switching to profile %s", zp)
        return bool(await self.commands.change_profile_number(ctrl, zp.number))

    async def get_groups(self, ctrl: ZenController | None = None) -> set[ZenGroup]:
        """Return a set of all groups (optionally for one controller)."""
        groups: set[ZenGroup] = set()
        controllers = [ctrl] if ctrl else self.controllers
        for ctrl in controllers:
            addresses = await self.commands.query_group_numbers(ctrl=ctrl)
            for address in addresses:
                group = await self.ctx.create_group(address)
                groups.add(group)
        return groups

    async def get_control_gear(self, ctrl: ZenController | None = None) -> set[ZenControlGear]:
        """Interview all control gear, discriminating light / fan / blind."""
        # (ean, bus_unit) → kind. bus_unit None matches any bus unit for that EAN.
        allowlist: dict[tuple[int, int | None], str] = {
            (6971103534836, None): "fan",   # zencontrol smart fan controller
            (6971103534829, None): "blind", # zencontrol smart blind controller
        }
        gear: set[ZenControlGear] = set()
        controllers = [ctrl] if ctrl else self.controllers
        for ctrl in controllers:
            addresses = await self.commands.query_control_gear_dali_addresses(ctrl=ctrl)
            for address in addresses:
                label = await self.commands.query_dali_device_label(address)
                ean = await self.commands.query_dali_ean(address)
                bus_unit: int | None = None
                kind: str | None = None
                if ean is not None:
                    kind = allowlist.get((ean, bus_unit)) or allowlist.get((ean, None))
                if kind is None:
                    text = (label or "").casefold().strip()
                    # Blind before fan (pathological labels containing both tokens).
                    if text == "blind" or text.endswith(" blind"):
                        kind = "blind"
                    elif text == "fan" or text.endswith(" fan"):
                        kind = "fan"
                    else:
                        kind = "light"
                match kind:
                    case "fan":
                        gear.add(await self.ctx.create_fan(address, label=label, ean=ean))
                    case "blind":
                        gear.add(await self.ctx.create_blind(address, label=label, ean=ean))
                    case _:
                        gear.add(await self.ctx.create_light(address, label=label, ean=ean))
        lights = {g for g in gear if isinstance(g, ZenLight)}
        _assign_light_sub_labels(lights)
        return gear

    async def get_lights(self, ctrl: ZenController | None = None) -> set[ZenLight]:
        """Return lights among discovered control gear (prefer get_control_gear)."""
        return {g for g in await self.get_control_gear(ctrl) if isinstance(g, ZenLight)}

    async def get_fans(self, ctrl: ZenController | None = None) -> set[ZenFan]:
        """Return fans among discovered control gear (prefer get_control_gear)."""
        return {g for g in await self.get_control_gear(ctrl) if isinstance(g, ZenFan)}

    async def get_blinds(self, ctrl: ZenController | None = None) -> set[ZenBlind]:
        """Return blinds among discovered control gear (prefer get_control_gear)."""
        return {g for g in await self.get_control_gear(ctrl) if isinstance(g, ZenBlind)}

    async def get_instances(self, ctrl: ZenController | None = None) -> set[ZenEcdEntity]:
        """Interview all ECD instances (buttons, motion sensors, absolute inputs).

        Address-space scan results are cached per controller until clear_entity_caches()
        so repeated get_instances / filter calls share one scan.
        """
        entities: set[ZenEcdEntity] = set()
        controllers = [ctrl] if ctrl else self.controllers
        for ctrl in controllers:
            instances = self._ecd_instances_by_controller.get(ctrl.name)
            if instances is None:
                instances = []
                for address in await self.commands.query_dali_addresses_with_instances(ctrl):
                    instances.extend(await self.commands.query_instances_by_address(address=address))
                self._ecd_instances_by_controller[ctrl.name] = instances
            for instance in instances:
                match instance.type:
                    case ZenInstanceType.PUSH_BUTTON:
                        entities.add(await self.ctx.create_button(instance))
                    case ZenInstanceType.OCCUPANCY_SENSOR:
                        entities.add(await self.ctx.create_motion_sensor(instance))
                    case ZenInstanceType.ABSOLUTE_INPUT:
                        entities.add(await self.ctx.create_absolute_input(instance))
                    case _:
                        continue
        return entities

    async def get_buttons(self, ctrl: ZenController | None = None) -> set[ZenButton]:
        """Return push-button instances (prefer get_instances)."""
        return {e for e in await self.get_instances(ctrl) if isinstance(e, ZenButton)}

    async def get_motion_sensors(self, ctrl: ZenController | None = None) -> set[ZenMotionSensor]:
        """Return occupancy-sensor instances (prefer get_instances)."""
        return {e for e in await self.get_instances(ctrl) if isinstance(e, ZenMotionSensor)}

    async def get_absolute_inputs(self, ctrl: ZenController | None = None) -> set[ZenAbsoluteInput]:
        """Return absolute-input instances (prefer get_instances)."""
        return {e for e in await self.get_instances(ctrl) if isinstance(e, ZenAbsoluteInput)}

    async def get_system_variables(self, give_up_after: int = 10, ctrl: ZenController | None = None) -> set[ZenSystemVariable]:
        """Return labelled system variables (optionally for one controller)."""
        sysvars: set[ZenSystemVariable] = set()
        controllers = [ctrl] if ctrl else self.controllers
        for ctrl in controllers:
            failed_attempts = 0
            for variable in range(ApiConst.MAX_SYSVAR):
                label = await self.commands.query_system_variable_name(ctrl=ctrl, variable=variable)
                if label:
                    failed_attempts = 0
                    sysvar = await self.ctx.create_system_variable(ctrl, variable, label=label)
                    sysvars.add(sysvar)
                else:
                    failed_attempts += 1
                    if failed_attempts >= give_up_after:
                        break
        return sysvars


__all__ = ["ZenControl", "ControllerRuntimeStatus"]
