from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any, Literal, Self, cast

from ..api import (
    ZenAddress,
    ZenAddressType,
    ZenColour,
    ZenColourType,
    ZenInstance,
    ZenInstanceType,
)
from ..api import ZenController as SuperZenController
from ..api.event_decode import (
    AbsoluteInput,
    ButtonHold,
    ButtonPress,
    ColourChange,
    IsOccupied,
    LevelChangeV2,
    ProfileChange,
    SceneChange,
    SystemVariableChange,
    ZenDecodedEvent,
)
from ..api.models import ControllerRef, DiscoveredController, mac_key, mac_to_bytes
from ..api.event_router import EventHealth, Lease, ZenEventReceiver
from ..api.commands import ZenCommandClient
from .context import EntityContext
from ..api.types import Const, Transport, ZenEventMode
from ..exceptions import ZenConnectionError, ZenTimeoutError
from ..io import ZenClient
from .wiring import ZenEventWiring

"""
===================================================================================
This module takes the ZenControl API and provides a higher level interface
intended for use in a control interface or home automation system written in Python.
===================================================================================



Terms:
ZenCommandClient = Command plane (TPI over zen_io). ZenControl owns commands + context + event_receiver.
ZenController = Represents a ZenControl controller.
ZenAddress = Represents a DALI address.
ZenInstance = Represents a DALI ECD instance.


"""

# Constants moved to api/types.py
# Placeholder classes removed - real implementations are below


# Callback type definitions moved to end of file after class definitions


def _assign_light_sub_labels(lights: list[ZenLight] | set[ZenLight]) -> None:
    """Derive ``sub_label`` for lights that share a comma-separated label.

    Controllers sometimes store one label string across several ECGs that share
    a fitting, e.g. ``"Hallway,Bathroom,,Annex"`` on addresses 31–34 meaning
    31=Hallway, 32=Bathroom, 33 unused, 34=Annex.

    Only applied when multiple lights share an identical label that contains a
    comma. Clusters are sorted by address number; empty segments become
    ``Unused {number}``. Lights outside such clusters keep ``sub_label=None``.
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


def _serialize_colour(colour: ZenColour | None) -> dict[str, int | str | None] | None:
    if colour is None or colour.type is None:
        return None
    data: dict[str, int | str | None] = {"type": colour.type.name.lower()}
    match colour.type:
        case ZenColourType.TC:
            data["kelvin"] = colour.kelvin
        case ZenColourType.RGBWAF:
            data["r"] = colour.r
            data["g"] = colour.g
            data["b"] = colour.b
            data["w"] = colour.w
            data["a"] = colour.a
            data["f"] = colour.f
        case ZenColourType.XY:
            data["x"] = colour.x
            data["y"] = colour.y
    return data


def _hydrate_colour(data: dict[str, Any] | None) -> ZenColour | None:
    if data is None:
        return None
    colour_type = ZenColourType[str(data["type"]).upper()]
    match colour_type:
        case ZenColourType.TC:
            return ZenColour(type=colour_type, kelvin=data.get("kelvin"))
        case ZenColourType.RGBWAF:
            return ZenColour(
                type=colour_type,
                r=data.get("r"),
                g=data.get("g"),
                b=data.get("b"),
                w=data.get("w"),
                a=data.get("a"),
                f=data.get("f"),
            )
        case ZenColourType.XY:
            return ZenColour(type=colour_type, x=data.get("x"), y=data.get("y"))


def _serialize_group_address(address: ZenAddress) -> dict[str, int]:
    return {"number": address.number}


def _loads_interview_data(data: str | dict[str, Any]) -> dict[str, Any]:
    if isinstance(data, str):
        loaded: dict[str, Any] = json.loads(data)
        return loaded
    return data


def _or_group_label(label: str | None, number: int) -> str:
    return label if label is not None else f"Group {number}"


def _or_device_label(label: str | None, address: ZenAddress) -> str:
    return label if label is not None else f"{address.controller.label} ECD {address.number}"


def _or_instance_label(label: str | None, instance: ZenInstance) -> str:
    if label is not None:
        return label
    return instance.type.name.title().replace("_", " ") + " " + str(instance.number)


def _or_scene_label(label: str | None, scene: int) -> str:
    return label if label is not None else f"Scene {scene}"


async def _group_scene_labels(
    commands: ZenCommandClient, address: ZenAddress
) -> list[str | None]:
    """Scene labels for a group, with generic names when the controller has none."""
    scenes: list[str | None] = [None] * Const.MAX_SCENE
    for scene in await commands.query_scene_numbers_for_group(address):
        label = await commands.query_scene_label_for_group(address, scene)
        scenes[scene] = _or_scene_label(label, scene)
    return scenes


class ZenControl:
    def __init__(self,
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
        self.event_receiver.on_unexpected_exit = self._on_listener_unexpected_exit
        self._event_task: asyncio.Task[None] | None = None
        self._disconnect_notified = False
        self._wiring: ZenEventWiring | None = None
        self._discovery_lease: Lease | None = None
        self._stopping = False
        self._supervisor_task: asyncio.Task[None] | None = None
        self._keepalive_task: asyncio.Task[None] | None = None
        self._first_connected = asyncio.Event()
        # Set on session restore; also woken on stop / leases-idle so waiters
        # can observe those terminal conditions without polling.
        self._session_restored = asyncio.Event()
        self.event_receiver.on_leases_idle = self._session_restored.set
        self._controller_status_change: CallbackControllerStatusChange | None = None
        self._resync_callback: CallbackOnResync | None = None
        self._enrich_locks: dict[str, asyncio.Lock] = {}
        # Per-controller tail of scheduled event-dispatch tasks (I8: keep the
        # funnel consumer free of entity/callback/device work).
        self._event_dispatch_tail: dict[str, asyncio.Task[None]] = {}
        self.reconnect_min_delay = Const.RECONNECT_MIN_DELAY
        self.reconnect_max_delay = Const.RECONNECT_MAX_DELAY
        self.reconnect_healthy_seconds = Const.RECONNECT_HEALTHY_SECONDS
        self.event_keepalive_interval = Const.EVENT_KEEPALIVE_INTERVAL

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        await self.aclose()

    def _is_stopping(self) -> bool:
        return self._stopping

    def clear_entity_caches(self) -> None:
        """Clear entity singleton registries for this ZenControl instance."""
        self.context.clear_entity_cache()

    @property
    def on_connect(self) -> CallbackOnConnect | None:
        return self.context.callbacks.on_connect
    @on_connect.setter
    def on_connect(self, func: CallbackOnConnect | None) -> None:
        self.context.callbacks.on_connect = func

    @property
    def on_disconnect(self) -> CallbackOnDisconnect | None:
        return self.context.callbacks.on_disconnect
    @on_disconnect.setter
    def on_disconnect(self, func: CallbackOnDisconnect | None) -> None:
        self.context.callbacks.on_disconnect = func

    @property
    def on_resync(self) -> CallbackOnResync | None:
        return self._resync_callback
    @on_resync.setter
    def on_resync(self, func: CallbackOnResync | None) -> None:
        self._resync_callback = func

    @property
    def profile_change(self) -> CallbackProfileChange | None:
        return self.context.callbacks.profile_change
    @profile_change.setter
    def profile_change(self, func: CallbackProfileChange | None) -> None:
        self.context.callbacks.profile_change = func

    @property
    def group_change(self) -> CallbackGroupChange | None:
        return self.context.callbacks.group_change
    @group_change.setter
    def group_change(self, func: CallbackGroupChange | None) -> None:
        self.context.callbacks.group_change = func

    @property
    def light_change(self) -> CallbackLightChange | None:
        return self.context.callbacks.light_change
    @light_change.setter
    def light_change(self, func: CallbackLightChange | None) -> None:
        self.context.callbacks.light_change = func

    @property
    def button_press(self) -> CallbackButtonPress | None:
        return self.context.callbacks.button_press
    @button_press.setter
    def button_press(self, func: CallbackButtonPress | None) -> None:
        self.context.callbacks.button_press = func
    
    @property
    def button_long_press(self) -> CallbackButtonLongPress | None:
        return self.context.callbacks.button_long_press
    @button_long_press.setter
    def button_long_press(self, func: CallbackButtonLongPress | None) -> None:
        self.context.callbacks.button_long_press = func

    @property
    def absolute_input_change(self) -> CallbackAbsoluteInputChange | None:
        return self.context.callbacks.absolute_input_change
    @absolute_input_change.setter
    def absolute_input_change(self, func: CallbackAbsoluteInputChange | None) -> None:
        self.context.callbacks.absolute_input_change = func

    @property
    def motion_event(self) -> CallbackMotionEvent | None:
        return self.context.callbacks.motion_event
    @motion_event.setter
    def motion_event(self, func: CallbackMotionEvent | None) -> None:
        self.context.callbacks.motion_event = func
    
    @property
    def system_variable_change(self) -> CallbackSystemVariableChange | None:
        return self.context.callbacks.system_variable_change
    @system_variable_change.setter
    def system_variable_change(self, func: CallbackSystemVariableChange | None) -> None:
        self.context.callbacks.system_variable_change = func

    @property
    def controller_discovered(self) -> CallbackControllerDiscovered | None:
        return self.context.callbacks.controller_discovered
    @controller_discovered.setter
    def controller_discovered(self, func: CallbackControllerDiscovered | None) -> None:
        self.context.callbacks.controller_discovered = func

    @property
    def controller_identified(self) -> CallbackControllerIdentified | None:
        return self.context.callbacks.controller_identified
    @controller_identified.setter
    def controller_identified(self, func: CallbackControllerIdentified | None) -> None:
        self.context.callbacks.controller_identified = func

    @property
    def controller_status_change(self) -> CallbackControllerStatusChange | None:
        return self._controller_status_change
    @controller_status_change.setter
    def controller_status_change(
        self, func: CallbackControllerStatusChange | None
    ) -> None:
        self._controller_status_change = func

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

    @property
    def event_task(self) -> asyncio.Task[None] | None:
        """Funnel consumer task for the current session.

        Prefers a live receiver consumer. Falls back to a stashed task from an
        unexpected exit so the supervisor can await it once; never returns a
        done receiver consumer that was not stashed (that would busy-spin).
        """
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

    def _has_event_leases(self) -> bool:
        return (
            self.event_receiver.lease_count(Transport.MULTICAST) > 0
            or self.event_receiver.lease_count(Transport.UNICAST) > 0
        )

    async def notify_disconnect(self) -> None:
        """Fire on_disconnect at most once per monitoring session."""
        if self._disconnect_notified:
            return
        self._disconnect_notified = True
        if not callable(self.context.callbacks.on_disconnect):
            return
        try:
            await self.context.callbacks.on_disconnect()
        except Exception as err:
            self.logger.error(f"on_disconnect error: {err}")

    async def _on_listener_unexpected_exit(self) -> None:
        """Handle funnel consumer death. Recoverable gaps do not disconnect (I10)."""
        self._event_task = self.event_receiver.consumer_task
        if self._has_event_leases():
            return
        await self.notify_disconnect()

    async def _forward_discovered(self, discovered: DiscoveredController) -> None:
        callback = self.context.callbacks.controller_discovered
        if not callable(callback):
            return
        try:
            await callback(discovered)
        except Exception as err:
            self.logger.error(
                "controller_discovered callback error: %s", err, exc_info=err
            )

    async def _notify_controller_identified(
        self, controller: SuperZenController, mac: str
    ) -> None:
        callback = self.context.callbacks.controller_identified
        if not callable(callback):
            return
        try:
            await callback(controller, mac)
        except Exception as err:
            self.logger.error(
                "controller_identified callback error: %s", err, exc_info=err
            )

    # ============================
    # Setup / Start / Stop
    # ============================

    def add_controller(self, id: int, name: str, label: str, host: str, port: int = 5108, mac: str | None = None, filtering: bool = False) -> ZenController:
        controller = ZenController(ctx=self.context, id=id, name=name, label=label, host=host, port=port, mac=mac, filtering=filtering)
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
            transport=(
                Transport.UNICAST if self.unicast else Transport.MULTICAST
            ),
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

    def _unicast_target_mismatch(
        self, controller: ZenController, info: dict[str, Any]
    ) -> bool:
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

    async def _notify_controller_status(
        self, controller: ZenController, status: ControllerRuntimeStatus
    ) -> None:
        """Notify listeners of online / starting / unreachable."""
        if not callable(self._controller_status_change):
            return
        try:
            await self._controller_status_change(controller, status)
        except Exception as err:
            self.logger.debug(
                "controller_status_change error for %s: %s",
                controller.name,
                err,
            )

    async def enrich_discovered(
        self, discovered: DiscoveredController
    ) -> DiscoveredController:
        """Probe ``QUERY_CONTROLLER_LABEL`` over the command plane and store the result.

        Discovery on the identity log is host/mac only. Call this (or
        ``discover()``, which enriches its return value) when a distinct label
        is needed for UI listing. Uses a temporary controller name for the UDP
        client; does not register the controller.
        """
        if discovered.label:
            return discovered

        key = mac_key(discovered.mac)
        lock = self._enrich_locks.setdefault(key, asyncio.Lock())
        async with lock:
            current = self.identities.get(mac=discovered.mac) or discovered
            if current.label:
                return current

            probe_name = f"_discover_{key.replace(':', '')}"
            temp = SuperZenController(
                id=f"discover-{key}",
                name=probe_name,
                label="",
                host=discovered.host,
                port=discovered.port,
                mac=discovered.mac,
            )
            label: str | None = None
            try:
                label = await self.commands.query_controller_label(temp)
            except ZenTimeoutError:
                self.logger.info(
                    "Discovered controller %s (%s) but label query timed out",
                    discovered.host,
                    discovered.mac,
                )
            except Exception as err:
                self.logger.warning(
                    "Discovered controller %s (%s) but label query failed: %s",
                    discovered.host,
                    discovered.mac,
                    err,
                )
            finally:
                await self.commands._invalidate_client(temp)

            if not label:
                return current

            enriched = DiscoveredController(
                host=current.host,
                mac=current.mac,
                label=label,
                port=current.port,
                first_seen=current.first_seen,
                last_seen=current.last_seen,
            )
            self.identities.replace(enriched)
            self.logger.info(
                "Enriched discovered controller %s mac=%s label=%r",
                enriched.host,
                enriched.mac,
                enriched.label,
            )
            return enriched

    async def discover(self, timeout: float = 5.0) -> list[DiscoveredController]:
        """Listen for multicast and return controllers heard within ``timeout`` seconds.

        Starts event monitoring if needed. Works with zero registered controllers
        and also reports unregistered controllers while already running. Returns
        identities with a packet in this window (``last_seen``), so a second call
        on a long-lived instance still surfaces controllers that emit again —
        required for HA "add another" / "try discovery again".

        Opens a temporary multicast lease when multicast is not already up
        (e.g. unicast-only runtime). Enriches each result with a command-plane
        label probe after the listen window.
        """
        window_start = time.time()
        started_here = False
        if self._supervisor_task is None or self._supervisor_task.done():
            await self.start()
            started_here = True

        temp_mcast: Lease | None = None
        if not self.event_receiver.is_transport_open(Transport.MULTICAST):
            temp_mcast = await self.event_receiver.acquire(Transport.MULTICAST)
        # Snapshot before teardown: stop() → close() clears the identity log.
        found: list[DiscoveredController] = []
        try:
            await asyncio.sleep(timeout)
            found = self.identities.heard_since(window_start)
        finally:
            if temp_mcast is not None:
                await temp_mcast.release()
            if started_here:
                await self.stop()

        return list(await asyncio.gather(*(self.enrich_discovered(d) for d in found)))

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
        self._wiring.on_resync = self._on_resync
        self._wiring.on_identified = self._on_controller_identified
        self._wiring.on_lost = self._on_binding_lost
        # Wiring rearms emit; replace protocol's session-lease rearm path.
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
            raise ZenConnectionError(
                f"Event monitoring failed to connect within {Const.START_TIMEOUT:.0f}s"
            ) from err

    async def stop(self) -> None:
        """Stop reconnect supervisor and event monitoring (keeps entity caches)."""
        await self._shutdown(close_clients=False, clear_caches=False)

    async def aclose(self) -> None:
        """Stop monitoring, cancel background work, close UDP clients, clear caches."""
        await self._shutdown(close_clients=True, clear_caches=True)

    async def _shutdown(self, *, close_clients: bool, clear_caches: bool) -> None:
        """Single shutdown path for ``stop`` and ``aclose``.

        Ownership:
        - This layer owns wiring, the supervisor, and keepalive tasks.
        - Bindings release leases; the receiver closes endpoints when refcounts hit 0.
        """
        self._stopping = True
        self._session_restored.set()  # wake restore waiters to observe stop
        was_running = self._wiring is not None
        await self._cancel_owned_tasks()
        if self._wiring is not None:
            await self._wiring.detach_all()
            self._wiring = None
        self._event_dispatch_tail.clear()
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
        """Cancel interface-owned long-lived tasks (supervisor + keepalive)."""
        await self._cancel_task("_supervisor_task")
        await self._cancel_task("_keepalive_task")

    async def _cancel_task(self, attr: str) -> None:
        task: asyncio.Task[None] | None = getattr(self, attr)
        setattr(self, attr, None)
        await EntityContext.cancel_and_await(task)

    async def _attach_bindings(self) -> None:
        """Attach once: subscribe + lease + program emit for every controller."""
        assert self._wiring is not None
        self._disconnect_notified = False
        for controller in self.controllers:
            if self._wiring.get(controller) is not None:
                continue
            await self._wiring.attach(controller, self._event_mode_for(controller))
        if not self.controllers and self._discovery_lease is None:
            self._discovery_lease = await self.event_receiver.acquire(
                Transport.MULTICAST
            )

    async def _on_session_restored(self) -> None:
        """Receiver re-opened leased transports — re-arm emit, then resync."""
        try:
            self._disconnect_notified = False
            self._event_task = None
            if self._wiring is not None:
                await self._wiring.rearm_all()
        finally:
            # Tell the supervisor — do not make it discover restore by polling.
            self._session_restored.set()

    async def _on_resync(self) -> None:
        """Invoke consumer on_resync after a session gap (not a wire event)."""
        callback = self._resync_callback
        if not callable(callback):
            return
        try:
            await callback()
        except Exception as err:
            self.logger.error(f"on_resync error: {err}", exc_info=True)

    async def _on_controller_identified(
        self, controller: SuperZenController, mac: str
    ) -> None:
        """Forward first-time MAC promotion so HA can persist the unique ID."""
        await self._notify_controller_identified(controller, mac)

    async def _on_binding_lost(
        self, controller: SuperZenController, reason: str
    ) -> None:
        """Receiver dropped routing for this controller — surface as unreachable."""
        self.logger.error(
            "Event binding lost for %s (%s)",
            controller.name,
            reason,
        )
        self._forget_event_dispatch(controller.name)
        await self._notify_controller_status(
            cast(ZenController, controller), "unreachable"
        )

    def _forget_event_dispatch(self, name: str) -> None:
        """Drop the per-controller dispatch-chain tail when a binding goes away."""
        self._event_dispatch_tail.pop(name, None)

    async def _event_monitor_supervisor(self) -> None:
        """Attach once; on consumer death wait for receiver recovery (do not re-attach)."""
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
                if self._is_stopping():
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
                # Idle with nothing leased — nothing left to supervise.
                if not self._has_event_leases():
                    return
                # Between sessions (recover in flight) — await the restore signal.
                if await self._wait_for_session_restore(None):
                    continue
                return

            try:
                # wait() returns when the consumer finishes (including if it was
                # cancelled). CancelledError here means *this* supervisor was
                # cancelled — HA unload — so re-raise and do not reconnect.
                await asyncio.wait({event_task})
            except asyncio.CancelledError:
                raise

            # Observed dead — drop the stash so event_task cannot keep
            # handing back this corpse on the next iteration.
            if self._event_task is event_task:
                self._event_task = None

            if event_task.cancelled():
                self.logger.error("Event monitor consumer cancelled unexpectedly")
            elif (exc := event_task.exception()) is not None:
                self.logger.error(f"Event monitor task error: {exc}")

            if self._is_stopping():
                return

            # I10: leases/subscriptions survive; the receiver owns bind retry.
            # Wait on the restore Event — do not poll or duplicate backoff.
            if await self._wait_for_session_restore(event_task):
                continue
            # Stopping, or lease counts hit zero — nothing left to supervise.
            return

    def _session_is_restored(self, dead_task: asyncio.Task[None] | None) -> bool:
        """True when a live consumer (not ``dead_task``) has open leased transports."""
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
        """True when a new consumer runs and every leased transport is open.

        Awaits ``_session_restored`` (set from ``_on_session_restored``, stop, and
        leases-idle). With ``timeout=None`` (default), waits until restored, stop,
        or leases are gone. Bind retry/backoff lives on the receiver.
        """
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            if self._is_stopping() or not self._has_event_leases():
                return False
            if self._session_is_restored(dead_task):
                return True

            self._session_restored.clear()
            # Restore / stop / idle may have raced between the checks and clear.
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
            # Woken — loop to classify restore vs stop vs idle.

    async def _event_keepalive_loop(self) -> None:
        """Periodically ping controllers and re-enable TPI events if needed."""
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
                if self._is_stopping():
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

    # ============================
    # Typed event dispatch
    # ============================

    def _ecd_instance(
        self,
        controller: ZenController,
        target: int,
        instance_type: ZenInstanceType,
        number: int,
    ) -> ZenInstance | None:
        ecd = target - 64
        if not 0 <= ecd <= 63:
            self.logger.error(f"Invalid ECD event target: {target}")
            return None
        address = ZenAddress(controller=controller, type=ZenAddressType.ECD, number=ecd)
        return ZenInstance(address=address, type=instance_type, number=number)

    def _ecg_or_group(
        self, controller: ZenController, target: int
    ) -> ZenAddress | None:
        if target <= 63:
            return ZenAddress(controller=controller, type=ZenAddressType.ECG, number=target)
        if 64 <= target <= 79:
            return ZenAddress(
                controller=controller, type=ZenAddressType.GROUP, number=target - 64
            )
        self.logger.error(f"Invalid gear/group event target: {target}")
        return None

    async def _on_controller_event(
        self, controller: SuperZenController, ev: ZenDecodedEvent
    ) -> None:
        """Subscription handler entry — returns immediately (I8).

        Entity updates and application callbacks run on a per-controller task
        chain so a slow callback or accidental device query cannot stall the
        shared funnel consumer. The funnel drop counter therefore reflects
        datagram bursts / parse backlog, not callback latency — slow work grows
        this chain instead. Handlers must still avoid awaiting the command
        plane; if they do, they only stall that controller's chain.
        """
        ctrl = cast(ZenController, controller)
        name = ctrl.name
        previous = self._event_dispatch_tail.get(name)

        async def run() -> None:
            if previous is not None and not previous.done():
                try:
                    await previous
                except asyncio.CancelledError:
                    # Predecessor may have been cancelled; ignore that. If *we*
                    # were cancelled while waiting (shutdown), honour it.
                    me = asyncio.current_task()
                    if me is not None and me.cancelling():
                        raise
                except Exception:
                    pass
            try:
                await self._dispatch_controller_event(ctrl, ev)
            except asyncio.CancelledError:
                raise
            except Exception as err:
                self.logger.error(
                    "Event dispatch error for %s: %s", name, err, exc_info=True
                )

        self._event_dispatch_tail[name] = self.context.track_task(run())

    async def _dispatch_controller_event(
        self, ctrl: ZenController, ev: ZenDecodedEvent
    ) -> None:
        """Apply a decoded event to entities and fire application callbacks."""
        match ev:
            case ButtonPress(target, instance_num):
                instance = self._ecd_instance(
                    ctrl, target, ZenInstanceType.PUSH_BUTTON, instance_num
                )
                if instance is None:
                    return
                await ZenButton(ctx=self.context, instance=instance)._event_received()

            case ButtonHold(target, instance_num):
                instance = self._ecd_instance(
                    ctrl, target, ZenInstanceType.PUSH_BUTTON, instance_num
                )
                if instance is None:
                    return
                await ZenButton(
                    ctx=self.context, instance=instance
                )._event_received(held=True)

            case AbsoluteInput(target, instance_num, value):
                instance = self._ecd_instance(
                    ctrl, target, ZenInstanceType.ABSOLUTE_INPUT, instance_num
                )
                if instance is None:
                    return
                # Absolute-input entity still expects the wire payload shape
                payload = bytes([instance_num, (value >> 8) & 0xFF, value & 0xFF])
                await ZenAbsoluteInput(
                    ctx=self.context, instance=instance
                )._event_received(payload)

            case IsOccupied(target, instance_num):
                instance = self._ecd_instance(
                    ctrl, target, ZenInstanceType.OCCUPANCY_SENSOR, instance_num
                )
                if instance is None:
                    return
                await ZenMotionSensor(
                    ctx=self.context, instance=instance
                )._event_received()

            case LevelChangeV2(target, _current, level):
                address = self._ecg_or_group(ctrl, target)
                if address is None:
                    return
                if address.type == ZenAddressType.ECG:
                    light = ZenLight(ctx=self.context, address=address)
                    await light._event_received(level=level)
                elif address.type == ZenAddressType.GROUP:
                    group = ZenGroup(ctx=self.context, address=address)
                    await group._event_received(level=level)

            case ColourChange(target, colour_bytes):
                address = self._ecg_or_group(ctrl, target)
                if address is None:
                    return
                colour = ZenColour.from_bytes(colour_bytes)
                if colour is None:
                    return
                if address.type == ZenAddressType.ECG:
                    await ZenLight(
                        ctx=self.context, address=address
                    )._event_received(colour=colour)
                elif address.type == ZenAddressType.GROUP:
                    group = ZenGroup(ctx=self.context, address=address)
                    await group._event_received(colour=colour)
                    for light in group.lights:
                        await light._event_received(colour=colour, cascaded_from=group)

            case SceneChange(target, scene, active):
                address = self._ecg_or_group(ctrl, target)
                if address is None:
                    return
                if address.type == ZenAddressType.ECG:
                    await ZenLight(
                        ctx=self.context, address=address
                    )._event_received(scene=scene, active=active)
                elif address.type == ZenAddressType.GROUP:
                    group = ZenGroup(ctx=self.context, address=address)
                    await group._event_received(scene=scene, active=active)
                    for light in group.lights:
                        await light._event_received(
                            scene=scene, active=active, cascaded_from=group
                        )

            case SystemVariableChange(target, value):
                await ZenSystemVariable(
                    ctx=self.context, controller=ctrl, id=target
                )._event_received(value)

            case ProfileChange(profile):
                await ctrl._event_received(profile=profile)

            case _:
                # Deprecated level events and group occupancy — ignored
                return

    # ============================
    # Abstraction layer commands
    # ============================ 

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
        """Return all DALI addresses that have instances, scanning all address ranges.

        ``query_dali_addresses_with_instances`` can only return up to 60 addresses
        per call. Iterating over start_address in steps of 60 covers the full
        DALI address space (0-127).
        """
        seen: set[tuple[str, int]] = set()
        addresses: list[ZenAddress] = []
        for start in range(0, 128, 60):
            batch = await self.commands.query_dali_addresses_with_instances(
                controller=controller, start_address=start
            )
            for addr in batch:
                key = (addr.controller.name, addr.number)
                if key not in seen:
                    seen.add(key)
                    addresses.append(addr)
        return addresses

    async def get_buttons(self, controller: ZenController | None = None) -> set[ZenButton]:
        """Return a set of all buttons available (optionally for one controller)."""
        buttons: set[ZenButton] = set()
        controllers = [controller] if controller else self.controllers
        for ctrl in controllers:
            addresses = await self._get_addresses_with_instances(ctrl)
            for address in addresses:
                instances = await self.commands.query_instances_by_address(address=address)
                for instance in instances:
                    if instance.type == ZenInstanceType.PUSH_BUTTON:
                        button = await ZenButton.create(ctx=self.context, instance=instance)
                        buttons.add(button)
        return buttons
    
    async def get_motion_sensors(self, controller: ZenController | None = None) -> set[ZenMotionSensor]:
        """Return a set of all motion sensors available (optionally for one controller)."""
        motion_sensors: set[ZenMotionSensor] = set()
        controllers = [controller] if controller else self.controllers
        for ctrl in controllers:
            addresses = await self._get_addresses_with_instances(ctrl)
            for address in addresses:
                instances = await self.commands.query_instances_by_address(address=address)
                for instance in instances:
                    if instance.type == ZenInstanceType.OCCUPANCY_SENSOR:
                        motion_sensor = await ZenMotionSensor.create(ctx=self.context, instance=instance)
                        motion_sensors.add(motion_sensor)
        return motion_sensors

    async def get_absolute_inputs(
        self, controller: ZenController | None = None
    ) -> set[ZenAbsoluteInput]:
        """Return absolute (numerical) ECD instances (optionally for one controller)."""
        absolute_inputs: set[ZenAbsoluteInput] = set()
        controllers = [controller] if controller else self.controllers
        for ctrl in controllers:
            addresses = await self._get_addresses_with_instances(ctrl)
            for address in addresses:
                instances = await self.commands.query_instances_by_address(address=address)
                for instance in instances:
                    if instance.type == ZenInstanceType.ABSOLUTE_INPUT:
                        absolute_input = await ZenAbsoluteInput.create(
                            ctx=self.context, instance=instance
                        )
                        absolute_inputs.add(absolute_input)
        return absolute_inputs

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

# ============================
# Abstraction layer classes
# ============================ 

class ZenController(SuperZenController):
    # Interface-owned references — not part of the API model (I9).
    ctx: EntityContext
    commands: ZenCommandClient
    version: str | None = None

    connected: bool = False
    profile: ZenProfile | None = None
    profiles: set[ZenProfile] = set()
    lights: set[ZenLight] = set()
    groups: set[ZenGroup] = set()
    buttons: set[ZenButton] = set()
    absolute_inputs: set[ZenAbsoluteInput] = set()
    motion_sensors: set[ZenMotionSensor] = set()
    sysvars: set[ZenSystemVariable] = set()
    client_data: dict[str, Any] = {}

    @property
    def client(self) -> ZenClient | None:
        """Compat shim: command clients live on commands, keyed by name."""
        return self.commands.client_for(self)

    @client.setter
    def client(self, value: ZenClient | None) -> None:
        self.commands.set_client(self, value)

    def __new__(cls, ctx: EntityContext, id: int, name: str, label: str, host: str, port: int = 5108, mac: str | None = None, filtering: bool = False) -> ZenController:
        # Unique per context + controller name
        registry = ctx.registry.controllers
        if name not in registry:
            inst = super().__new__(cls)
            registry[name] = inst
            inst.connected = False
            object.__setattr__(inst, "_ip", None)
            object.__setattr__(inst, "_dataclass_initialized", False)
            inst._reset()
            # Don't call interview() here - it will be called async later
        inst = registry[name]
        # Always refresh config fields; never wipe transport/interview state via __init__
        inst.ctx = ctx
        inst.commands = ctx.commands
        inst.id = str(id)
        inst.name = name
        inst.label = label
        inst.host = host
        inst.port = port
        inst.mac = mac  # mac_bytes is derived from mac
        inst.filtering = filtering
        mac_to_bytes(mac)  # eager validate on config refresh
        return cast(ZenController, inst)

    def __init__(self, ctx: EntityContext, id: int, name: str, label: str, host: str, port: int = 5108, mac: str | None = None, filtering: bool = False) -> None:
        # Dataclass __init__ resets version/etc. Run only once per singleton.
        if getattr(self, "_dataclass_initialized", False):
            return
        super().__init__(
            id=str(id),
            name=name,
            label=label,
            host=host,
            port=port,
            mac=mac,
            filtering=filtering,
        )
        self.ctx = ctx
        self.commands = ctx.commands
        object.__setattr__(self, "_dataclass_initialized", True)
    
    @classmethod
    async def create(cls, ctx: EntityContext, id: int, name: str, label: str, host: str, port: int = 5108, mac: str | None = None, filtering: bool = False) -> ZenController:
        """Async factory method for ZenController"""
        controller = cls(ctx=ctx, id=id, name=name, label=label, host=host, port=port, mac=mac, filtering=filtering)
        await controller.interview()
        return controller
    def __repr__(self) -> str:
        return f"ZenController<{self.name}>"
    def _reset(self) -> None:
        # label is set from config in __new__ or from interview(); not runtime state
        self.version = None
        self.profile = None
        self.profiles = set()
        self.lights = set()
        self.groups = set()
        self.buttons = set()
        self.absolute_inputs = set()
        self.motion_sensors = set()
        self.sysvars = set()
        self.client_data = {}
    async def interview(self) -> bool:
        protocol = self.commands
        if self.label is None or self.label == "":
            queried = await protocol.query_controller_label(self)
            if queried is not None:
                self.label = queried
        self.version = await protocol.query_controller_version_number(self)
        current_profile = await protocol.query_current_profile_number(self)
        if current_profile is not None:
            self.profile = ZenProfile(ctx=self.ctx, controller=self, number=current_profile)
        self.connected = True
        return True
    async def _event_received(self, profile: int | None = None) -> None:
        protocol = self.commands
        if profile is not None:
            self.profile = ZenProfile(ctx=self.ctx, controller=self, number=profile)
            cb = self.ctx.callbacks.profile_change
            if callable(cb):
                await cb(profile=self.profile)
    def get_sysvar(self, id: int) -> ZenSystemVariable:
        return ZenSystemVariable(ctx=self.ctx, controller=self, id=id)
    async def is_controller_ready(self) -> bool | None:
        return await self.commands.query_controller_startup_complete(self)
    async def is_dali_ready(self) -> bool | None:
        return await self.commands.query_is_dali_ready(self)
    async def switch_to_profile(self, profile: ZenProfile|int|str) -> bool:
        zp = None
        if isinstance(profile, ZenProfile):
            zp = profile
        elif isinstance(profile, str):
            for p in self.profiles:
                if p.label == profile: zp = p
        elif isinstance(profile, int):
            for p in self.profiles:
                if p.number == profile: zp = p
        if isinstance(zp, ZenProfile):
            self.commands.logger.debug("Switching to profile %s", zp)
            result = await self.commands.change_profile_number(self, zp.number)
            return bool(result)
        else:
            return False
    async def return_to_scheduled_profile(self) -> bool | None:
        return await self.commands.return_to_scheduled_profile(self)


def _registered(controller: ControllerRef) -> ZenController:
    """Narrow ``address.controller`` to the interface subclass.

    Addresses are typed with ``ControllerRef`` so ``api`` does not import this
    layer; every registered controller is a ``ZenController`` instance.
    """
    return cast(ZenController, controller)


class ZenProfile:
    ctx: EntityContext
    commands: ZenCommandClient
    controller: ZenController
    number: int
    label: str | None = None
    client_data: dict[str, Any] = {}

    def __new__(cls, ctx: EntityContext, controller: ZenController, number: int) -> ZenProfile:
        # Unique per context + controller + profile number
        compound_id = f"{controller.name} {number}"
        registry = ctx.registry.profiles
        if compound_id not in registry:
            inst = super().__new__(cls)
            registry[compound_id] = inst
            inst.ctx = ctx
            inst.commands = ctx.commands
            inst.controller = controller
            inst.number = number
            inst._reset()
            # Don't call interview() here - it will be called async later
        return cast(ZenProfile, registry[compound_id])

    def __init__(self, ctx: EntityContext, controller: ZenController, number: int) -> None:
        self.ctx = ctx
        self.commands = ctx.commands
        self.controller = controller
        self.number = number
    
    @classmethod
    async def create(cls, ctx: EntityContext, controller: ZenController, number: int) -> ZenProfile:
        """Async factory method for ZenProfile"""
        profile = cls(ctx, controller, number)
        await profile.interview()
        return profile
    def __repr__(self) -> str:
        return f"ZenProfile<{self.controller.name} profile {self.number}: {self.label}>"
    def _reset(self) -> None:
        self.label = None
        self.client_data = {}
    def interview_serialize(self) -> str:
        return json.dumps({
            "number": self.number,
            "label": self.label,
        })
    def interview_hydrate(self, data: str | dict[str, Any]) -> bool:
        try:
            data = _loads_interview_data(data)
            self.label = data.get("label")
            self.controller.profiles.add(self)
            return True
        except Exception:
            return False
    async def interview(self) -> bool:
        self.label = await self.commands.query_profile_label(self.controller, self.number)
        # Add self to controller's set of profiles
        self.controller.profiles.add(self)
        return True
    async def select(self) -> bool:
        result = await self.commands.change_profile_number(self.controller, self.number)
        return bool(result)


class ZenLight:
    ctx: EntityContext
    commands: ZenCommandClient
    address: ZenAddress
    label: str | None = None
    sub_label: str | None = None
    serial: (int | str) | None = None
    cgtype: list[int] = []
    groups: set[ZenGroup] = set()
    group_membership: list[ZenAddress] = []
    features: dict[str, bool] = {
        "brightness": False,
        "temperature": False,
        "RGB": False,
        "RGBW": False,
        "RGBWW": False,
        "XY": False,
    }
    properties: dict[str, int | None] = {
        "min_kelvin": Const.DEFAULT_WARMEST_TEMP,
        "max_kelvin": Const.DEFAULT_COOLEST_TEMP,
    }
    _scene_labels: list[str | None] = [None] * Const.MAX_SCENE
    _scene_levels: list[int | None] = [None] * Const.MAX_SCENE
    _scene_colours: list[ZenColour | None] = [None] * Const.MAX_SCENE
    level: int | None = None
    colour: ZenColour | None = None
    scene: int | None = None
    client_data: dict[str, Any] = {}
    _refresh_timer: asyncio.Task[None] | None = None

    def __new__(cls, ctx: EntityContext, address: ZenAddress) -> Self:
        # Inherited classes should bypass ZenLight __new__
        if cls is not ZenLight:
            return super().__new__(cls)
        # Unique per context + controller + address
        compound_id = f"{address.controller.name} {address.number}"
        registry = ctx.registry.lights
        if compound_id not in registry:
            inst = super().__new__(cls)
            registry[compound_id] = inst
            inst.ctx = ctx
            inst.commands = ctx.commands
            inst.address = address
            inst._reset()
            # Don't call interview() here - it will be called async later
        return cast(Self, registry[compound_id])

    def __init__(self, ctx: EntityContext, address: ZenAddress) -> None:
        self.ctx = ctx
        self.commands = ctx.commands
        self.address = address
    
    @classmethod
    async def create(cls, ctx: EntityContext, address: ZenAddress) -> ZenLight:
        """Async factory method for ZenLight"""
        instance = cls(ctx, address)
        await instance.interview()
        return instance
    def __repr__(self) -> str:
        return f"ZenLight<{self.address.controller.name} ecg {self.address.number}: {self.label}>"
    def _reset(self) -> None:
        self.label = None
        self.sub_label = None
        self.serial = None
        self.cgtype = []
        self.groups = set()
        self.group_membership = []
        self.features = {
            "brightness": False,
            "temperature": False,
            "RGB": False,
            "RGBW": False,
            "RGBWW": False,
            "XY": False,
        }
        self.properties = {
            "min_kelvin": Const.DEFAULT_WARMEST_TEMP,
            "max_kelvin": Const.DEFAULT_COOLEST_TEMP,
        }
        self._scene_labels = [None] * Const.MAX_SCENE # Scene labels (only used by ZenGroup)
        self._scene_levels = [None] * Const.MAX_SCENE # Scene levels (only used by ZenLight)
        self._scene_colours = [None] * Const.MAX_SCENE # Scene colours (only used by ZenLight)
        self.level = None
        self.colour = None
        self.scene = None # Current scene number
        self.client_data = {}
        # Timer for refresh_state_from_controller after property changes
        self._refresh_timer = None
    def _apply_group_membership(self, membership: list[ZenAddress]) -> None:
        for existing_group in self.groups:
            existing_group.lights.discard(self)
        self.groups.clear()
        self.group_membership = list(membership)
        for group_address in self.group_membership:
            group = ZenGroup(ctx=self.ctx, address=group_address)
            group.lights.add(self)
            self.groups.add(group)
    def interview_serialize(self) -> str:
        return json.dumps({
            "label": self.label,
            "sub_label": self.sub_label,
            "serial": self.serial,
            "cgtype": list(self.cgtype),
            "group_membership": [_serialize_group_address(group) for group in self.group_membership],
            "features": dict(self.features),
            "properties": dict(self.properties),
            "scene_levels": list(self._scene_levels),
            "scene_colours": [_serialize_colour(colour) for colour in self._scene_colours],
        })
    def interview_hydrate(self, data: str | dict[str, Any]) -> bool:
        try:
            data = _loads_interview_data(data)
            self.label = data.get("label")
            self.sub_label = data.get("sub_label")
            self.serial = data.get("serial")
            self.cgtype = list(data.get("cgtype", []))
            self.features.update(data.get("features", {}))
            self.properties.update(data.get("properties", {}))
            self._scene_levels = list(data.get("scene_levels", []))
            self._scene_colours = [_hydrate_colour(colour) for colour in data.get("scene_colours", [])]
            membership = [
                ZenAddress(controller=self.address.controller, type=ZenAddressType.GROUP, number=group["number"])
                for group in data.get("group_membership", [])
            ]
            self._apply_group_membership(membership)
            _registered(self.address.controller).lights.add(self)
            return True
        except Exception:
            return False
    async def interview(self) -> bool:
        cgstatus = await self.commands.dali_query_control_gear_status(self.address)
        if cgstatus:
            self.label = _or_device_label(
                await self.commands.query_dali_device_label(self.address), self.address
            )
            self.serial = await self.commands.query_dali_serial(self.address)
            self.cgtype = await self.commands.dali_query_cg_type(self.address) or []
            
            # If cgtype contains 6, it supports brightness
            if 6 in self.cgtype:
                self.features["brightness"] = True
            
            # If cgtype contains 8, it supports some kind of colour
            if 8 in self.cgtype:
                cgtype = await self.commands.query_dali_colour_features(self.address)
                # XY is independent of TC/RGBWAF; a fixture may support more than one.
                if cgtype and cgtype.get("supports_xy", False) is True:
                    self.features["brightness"] = True
                    self.features["XY"] = True
                if cgtype and cgtype.get("supports_tunable", False) is True:
                    self.features["brightness"] = True
                    self.features["temperature"] = True
                    colour_temp_limits = await self.commands.query_dali_colour_temp_limits(self.address)
                    if colour_temp_limits:
                        self.properties["min_kelvin"] = colour_temp_limits.get("soft_warmest", Const.DEFAULT_WARMEST_TEMP)
                        self.properties["max_kelvin"] = colour_temp_limits.get("soft_coolest", Const.DEFAULT_COOLEST_TEMP)
                elif cgtype and cgtype.get("rgbwaf_channels", 0) == Const.RGB_CHANNELS:
                    self.features["brightness"] = True
                    self.features["RGB"] = True
                elif cgtype and cgtype.get("rgbwaf_channels", 0) == Const.RGBW_CHANNELS:
                    self.features["brightness"] = True
                    self.features["RGBW"] = True
                elif cgtype and cgtype.get("rgbwaf_channels", 0) == Const.RGBWW_CHANNELS:
                    self.features["brightness"] = True
                    self.features["RGBWW"] = True
            
            # Scenes
            self._scene_levels = await self.commands.query_scene_levels_by_address(self.address)
            self._scene_colours = await self.commands.query_scene_colours_by_address(self.address)

            # Groups
            groups = await self.commands.query_group_membership_by_address(self.address)
            self._apply_group_membership(groups or [])
            
            # Add to controller's set of lights
            _registered(self.address.controller).lights.add(self)

            return True
        else:
            self._reset()
            return False
    async def refresh_state_from_controller(self, verifying: bool = False) -> None:
        
        refreshed_level = await self.commands.dali_query_level(self.address)
        refreshed_colour = None
        refreshed_scene = None
        if await self.commands.dali_query_last_scene_is_current(self.address):
            refreshed_scene = await self.commands.dali_query_last_scene(self.address)
        if (
            self.features.get("temperature")
            or self.features.get("RGB")
            or self.features.get("RGBW")
            or self.features.get("RGBWW")
            or self.features.get("XY")
        ):
            refreshed_colour = await self.commands.query_dali_colour(self.address)
        
        if verifying:
            # Level is driven by LEVEL_CHANGE_V2 dimming-to events; query returns current arc mid-fade
            if refreshed_level is not None and self.level != refreshed_level:
                self.commands.logger.debug(
                    f"Light {self.address.number} queried level {refreshed_level} "
                    f"differs from tracked destination {self.level} (expected during fade)"
                )
            refreshed_level = None
            if self.colour != refreshed_colour:
                self.commands.logger.error(f"Light {self.address.number} colour mismatch! We had {self.colour}, actual colour is {refreshed_colour}")
            if self.scene != refreshed_scene:
                self.commands.logger.error(f"Light {self.address.number} scene mismatch! We had {self.scene}, actual scene is {refreshed_scene}")
        
        # Mimic an incoming scene event when the controller reports the last
        # scene is current. This ensures we also update `self.scene`.
        await self._event_received(
            level=refreshed_level,
            colour=refreshed_colour,
            scene=refreshed_scene,
            active=(refreshed_scene is not None and not verifying),
            verifying=verifying,
        )

    def _start_refresh_timer(self) -> None:
        """Start a 2-second timer to refresh from controller after API user changes state."""
        # Cancel any existing timer
        if self._refresh_timer and not self._refresh_timer.done():
            self._refresh_timer.cancel()
        
        # Start new timer (which quietly dies if cancelled)
        async def delayed_refresh() -> None:
            try:
                await asyncio.sleep(2.0)
                await self.refresh_state_from_controller(verifying=True)
            except asyncio.CancelledError:
                pass
        
        self._refresh_timer = self.ctx.track_task(delayed_refresh())

    async def _event_received(self,
            level: int|None = 255,
            colour: ZenColour | None = None,
            scene: int | None = None,
            active: bool | None = None,
            cascaded_from: ZenGroup | None = None,
            verifying: bool = False
        ) -> None:
        # Called when a query command is issued or an event is received
        level_changed = False
        colour_changed = False
        scene_changed = False
        # `active` may be bool or int (protocol passes payload[1] as 0/1).
        # Use truthiness — `1 is True` is False in Python.
        if scene is not None and active:
            self.scene = scene
            scene_changed = True
            scene_level = self._scene_levels[scene]
            scene_colour = self._scene_colours[scene]
            if scene_level is None:
                # Some objects (e.g. groups) may not have scene level tables.
                # Fall back to the queried `level` so we still keep runtime
                # light/group state consistent on refresh.
                if level is not None and level != 255 and level != self.level:
                    self.level = level
                    level_changed = True
            elif self.level == scene_level:
                pass # The level didn't change
            else:
                self.level = scene_level
                level_changed = True
            if scene_colour is None:
                # Same fallback as for level: preserve queried colour when
                # scene colour tables are unavailable.
                if colour is not None and colour != self.colour:
                    self.colour = colour
                    colour_changed = True
            elif self.colour == scene_colour:
                pass # The colour didn't change
            else:
                self.colour = scene_colour
                colour_changed = True
            if type(self) is ZenGroup:
                # print(f"                              Group {self.address.number} changed to scene {self.scene}")
                pass
            elif type(self) is ZenLight:
                # For each group it's a member of, it must declare the same scene, else we declare it discoordinated
                # print(f"                              Light {self.address.number} changed to scene {self.scene}" + f" cascaded from group {cascaded_from.address.number}" if cascaded_from else "")
                for group in self.groups:
                    if group.scene != self.scene:
                        # print(f"                              Group {group.address.number} discoordinated after scene set" + f" cascaded from group {cascaded_from.address.number}" if cascaded_from else "")
                        await group.declare_discoordination()
        else:
            if level is not None and level != 255 and level != self.level:
                self.level = level
                level_changed = True
                if self.scene is not None:
                    self.scene = None
                    scene_changed = True
            if colour is not None and colour != self.colour:
                self.colour = colour
                colour_changed = True
                if self.scene is not None:
                    self.scene = None
                    scene_changed = True
            # For each group it's a member of, it must declare the same levels, else we declare it discoordinated
            if type(self) is ZenGroup:
                # print(f"                              Group {self.address.number} changed to {self.level} {self.colour}")
                pass
            elif type(self) is ZenLight:
                # print(f"                              Light {self.address.number} changed to {self.level} {self.colour}" + f" cascaded from group {cascaded_from.address.number}" if cascaded_from else "")
                for group in self.groups:
                    if (level_changed and group.level != self.level) or (colour_changed and self.colour is not None and group.colour != self.colour):
                        await group.declare_discoordination()
        # Send callbacks to the application
        if type(self) is ZenGroup:
            if level_changed or colour_changed or scene_changed:
                if callable(self.ctx.callbacks.group_change):
                    await self.ctx.callbacks.group_change(group=self,
                                    level=self.level if level_changed else None,
                                    colour=self.colour if colour_changed else None,
                                    scene=self.scene if scene_changed else None)
        elif type(self) is ZenLight:
            if level_changed or colour_changed or scene_changed:
                if callable(self.ctx.callbacks.light_change):
                    await self.ctx.callbacks.light_change(light=self,
                                    level=self.level if level_changed else None,
                                    colour=self.colour if colour_changed else None,
                                    scene=self.scene if scene_changed else None)
    def supports_colour(self, colour: ZenColourType|ZenColour) -> bool:
        if type(colour) is ZenColour:
            colour_type = colour.type
        elif type(colour) is ZenColourType:
            colour_type = colour
        else:
            return False
        if (colour_type == ZenColourType.TC and self.features.get("temperature")) or \
            (colour_type == ZenColourType.RGBWAF and self.features.get("RGB")) or \
            (colour_type == ZenColourType.RGBWAF and self.features.get("RGBW")) or \
            (colour_type == ZenColourType.RGBWAF and self.features.get("RGBWW")) or \
            (colour_type == ZenColourType.XY and self.features.get("XY")):
            return True
        return False
    # -----------------------------------------------------------------------------------------
    # REMINDER: None of the following methods should update the internal object state directly.
    #   These methods send commands to the controller. The controller sends events back.
    #   The events update the internal state.
    # -----------------------------------------------------------------------------------------
    async def on(self, fade: bool = True) -> bool | None:
        self._start_refresh_timer()
        if not fade: await self.commands.dali_enable_dapc_sequence(self.address)
        return await self.commands.dali_go_to_last_active_level(self.address)
    async def off(self, fade: bool = True) -> bool | None:
        self._start_refresh_timer()
        if fade: return await self.commands.dali_arc_level(self.address, 0)
        else: return await self.commands.dali_off(self.address)
    async def set_scene(self, scene: int|str|dict[str, Any], fade: bool = True) -> bool | None:
        self._start_refresh_timer()
        if type(scene) is str:
            scene = next((i for i, s in enumerate(self._scene_labels) if s == scene), False)
        if type(scene) is int:
            if not fade: await self.commands.dali_enable_dapc_sequence(self.address)
            return await self.commands.dali_scene(self.address, scene)
        return False
    async def set(self, level: int = 255, colour: ZenColour | None = None, fade: bool = True) -> bool | None:
        self._start_refresh_timer()
        if colour is not None and self.supports_colour(colour):
            if not fade: await self.commands.dali_enable_dapc_sequence(self.address)
            return await self.commands.dali_colour(self.address, colour, level)
        if 0 <= level <= 254:
            if fade:
                return await self.commands.dali_arc_level(self.address, level)
            else:
                return await self.commands.dali_custom_fade(self.address, level, 0)
        return False
    async def dali_on_step_up(self) -> bool | None:
        self._start_refresh_timer()
        return await self.commands.dali_on_step_up(self.address)
    async def dali_step_down_off(self) -> bool | None:
        self._start_refresh_timer()
        return await self.commands.dali_step_down_off(self.address)
    async def dali_up(self) -> bool | None:
        self._start_refresh_timer()
        return await self.commands.dali_up(self.address)
    async def dali_down(self) -> bool | None:
        self._start_refresh_timer()
        return await self.commands.dali_down(self.address)
    async def dali_recall_max(self) -> bool | None:
        self._start_refresh_timer()
        return await self.commands.dali_recall_max(self.address)
    async def dali_recall_min(self) -> bool | None:
        self._start_refresh_timer()
        return await self.commands.dali_recall_min(self.address)
    async def dali_go_to_last_active_level(self) -> bool | None:
        self._start_refresh_timer()
        return await self.commands.dali_go_to_last_active_level(self.address)
    async def dali_off(self) -> bool | None:
        self._start_refresh_timer()
        return await self.commands.dali_off(self.address)
    async def dali_custom_fade(self, level: int, duration: int) -> bool | None:
        self._start_refresh_timer()
        return await self.commands.dali_custom_fade(self.address, level, duration)
    async def dali_stop_fade(self) -> bool | None:
        self._start_refresh_timer()
        return await self.commands.dali_stop_fade(self.address)
    async def dali_enable_dapc_sequence(self) -> bool | None:
        return await self.commands.dali_enable_dapc_sequence(self.address)
    async def dali_inhibit(self, inhibit: bool = True) -> bool | None:
        time_seconds = 65535 if inhibit else 0
        return await self.commands.dali_inhibit(self.address, time_seconds)
        

class ZenGroup(ZenLight):
    lights: set[ZenLight] = set()

    def __new__(cls, ctx: EntityContext, address: ZenAddress) -> ZenGroup:
        # Unique per context + controller + group address
        compound_id = f"{address.controller.name} g{address.number}"
        registry = ctx.registry.groups
        if compound_id not in registry:
            inst = super().__new__(cls, ctx=ctx, address=address)
            registry[compound_id] = inst
            inst.ctx = ctx
            inst.commands = ctx.commands
            inst.address = address
            inst.lights = set()  # member lights; managed via ZenLight._apply_group_membership
            inst._reset()
            # Don't call interview() here - it will be called async later
        return cast(ZenGroup, registry[compound_id])

    def __init__(self, ctx: EntityContext, address: ZenAddress) -> None:
        super().__init__(ctx, address)
    
    @classmethod
    async def create(cls, ctx: EntityContext, address: ZenAddress) -> ZenGroup:
        """Async factory method for ZenGroup"""
        group = cls(ctx, address)
        await group.interview()
        return group
    def __repr__(self) -> str:
        return f"ZenGroup<{self.address.controller.name} group {self.address.number}: {self.label}>"
    def interview_serialize(self) -> str:
        return json.dumps({
            "label": self.label,
            "scene_labels": list(self._scene_labels),
        })
    def interview_hydrate(self, data: str | dict[str, Any]) -> bool:
        try:
            data = _loads_interview_data(data)
            self.label = data.get("label")
            self._scene_labels = list(data.get("scene_labels", []))
            _registered(self.address.controller).groups.add(self)
            return True
        except Exception:
            return False
    async def interview(self) -> bool:
        self.label = _or_group_label(
            await self.commands.query_group_label(self.address), self.address.number
        )
        self._scene_labels = await _group_scene_labels(self.commands, self.address)
        # Add to controller's set of groups
        _registered(self.address.controller).groups.add(self)
        return True
    def supports_colour(self, colour: ZenColourType|ZenColour) -> bool:
        # If at least one light in the group supports this colour, return True
        for light in self.lights:
            if light.supports_colour(colour):
                return True
        return False
    def get_scene_number_from_label(self, label: str) -> int | None:
        # return list index of label in self._scene_labels
        return next((i for i, s in enumerate(self._scene_labels) if s == label), None)
    def get_scene_label_from_number(self, number: int) -> str | None:
        # return label at index number in self._scene_labels
        return self._scene_labels[number]
    def get_scene_labels(self, exclude_none: bool = False) -> list[str | None]:
        if exclude_none:
            return [label for label in self._scene_labels if label is not None]
        else:
            return self._scene_labels
    # -----------------------------------------------------------------------------------------
    # REMINDER: None of the following methods should update the internal object state directly.
    #   These methods send commands to the controller. The controller sends events back.
    #   The events update the internal state.
    # -----------------------------------------------------------------------------------------
    async def declare_discoordination(self) -> None:
        # Only do something if the group claims to be coordinated
        if self.level is None and self.colour is None and self.scene is None:
            return
        # This is called when members of the group are no longer in a uniform state
        self.level = None
        self.colour = None
        self.scene = None
        if callable(self.ctx.callbacks.group_change):
            await self.ctx.callbacks.group_change(group=self,
                                    discoordinated=True)
    def contains_dimmable_lights(self) -> bool:
        # Is there at least one ZenLight in self.lights that supports dimming?
        for light in self.lights:
            if light.features["brightness"]:
                return True
        return False
    def contains_temperature_lights(self) -> bool:
        # Is there at least one ZenLight in self.lights that supports temperature?
        for light in self.lights:
            if light.features["temperature"]:
                return True
        return False

class ZenButton:
    ctx: EntityContext
    commands: ZenCommandClient
    instance: ZenInstance
    serial: (int | str) | None = None
    label: str | None = None
    instance_label: str | None = None
    last_press_time: float = 0.0
    long_press_count: int = 0
    client_data: dict[str, Any] = {}

    def __new__(cls, ctx: EntityContext, instance: ZenInstance) -> ZenButton:
        # Unique per context + controller + address + instance
        compound_id = f"{instance.address.controller.name} {instance.address.number} {instance.number}"
        registry = ctx.registry.buttons
        if compound_id not in registry:
            inst = super().__new__(cls)
            registry[compound_id] = inst
            inst.ctx = ctx
            inst.commands = ctx.commands
            inst.instance = instance
            inst._reset()
            # Don't call interview() here - it will be called async later
        return cast(ZenButton, registry[compound_id])

    def __init__(self, ctx: EntityContext, instance: ZenInstance) -> None:
        self.ctx = ctx
        self.commands = ctx.commands
        self.instance = instance
    
    @classmethod
    async def create(cls, ctx: EntityContext, instance: ZenInstance) -> ZenButton:
        """Async factory method for ZenButton"""
        button = cls(ctx, instance)
        await button.interview()
        return button
    def __repr__(self) -> str:
        return f"ZenButton<{self.instance.address.controller.name} ecd {self.instance.address.number} inst {self.instance.number}: {self.label} / {self.instance_label}>"
    def _reset(self) -> None:
        self.serial = None
        self.label = None
        self.instance_label = None
        self.last_press_time = time.time()
        self.long_press_count = 0
        self.client_data = {}
    def interview_serialize(self) -> str:
        return json.dumps({
            "serial": self.serial,
            "label": self.label,
            "instance_label": self.instance_label,
        })
    def interview_hydrate(self, data: str | dict[str, Any]) -> bool:
        try:
            data = _loads_interview_data(data)
            self.serial = data.get("serial")
            self.label = data.get("label")
            self.instance_label = data.get("instance_label")
            self.instance.address.label = self.label
            self.instance.address.serial = cast(str | None, self.serial)
            _registered(self.instance.address.controller).buttons.add(self)
            return True
        except Exception:
            return False
    async def interview(self) -> bool:
        inst = self.instance
        addr = inst.address
        ctrl = _registered(addr.controller)
        if addr.label is None:
            addr.label = _or_device_label(
                await self.commands.query_dali_device_label(addr), addr
            )
        if addr.serial is None: addr.serial = cast(str | None, await self.commands.query_dali_serial(addr))
        self.label = addr.label
        self.serial = addr.serial
        self.instance_label = _or_instance_label(
            await self.commands.query_dali_instance_label(inst), inst
        )
        # Add to controller's set of buttons
        ctrl.buttons.add(self)
        return True
    async def _event_received(self, held: bool = False) -> None:
        if not held:
            if callable(self.ctx.callbacks.button_press):
                await self.ctx.callbacks.button_press(button=self)
        else:
            seconds_since_last_press = time.time() - self.last_press_time
            # if there's been less than 500 msec between the last hold message, increment the hold count
            if seconds_since_last_press < 0.5:
                self.long_press_count += 1
            else:
                self.long_press_count = 0
            self.last_press_time = time.time()
            # if the hold count is exactly Const.LONG_PRESS_COUNT, call the long press callback
            if self.long_press_count == Const.LONG_PRESS_COUNT:
                if callable(self.ctx.callbacks.button_long_press):
                    await self.ctx.callbacks.button_long_press(button=self)


class ZenAbsoluteInput:
    """DALI ECD absolute (numerical) input instance — dials, sliders, etc.

    Controllers emit value-change events only; TPI has no query/set command for
    the current value, so ``value`` stays ``None`` until the first event.
    Payload matches ``_protocol.txt``: ``[instance, value_hi, value_lo]``.
    """

    ctx: EntityContext
    commands: ZenCommandClient
    instance: ZenInstance
    serial: (int | str) | None = None
    label: str | None = None
    instance_label: str | None = None
    _value: int | None = None
    client_data: dict[str, Any] = {}

    def __new__(cls, ctx: EntityContext, instance: ZenInstance) -> ZenAbsoluteInput:
        compound_id = f"{instance.address.controller.name} {instance.address.number} {instance.number}"
        registry = ctx.registry.absolute_inputs
        if compound_id not in registry:
            inst = super().__new__(cls)
            registry[compound_id] = inst
            inst.ctx = ctx
            inst.commands = ctx.commands
            inst.instance = instance
            inst._reset()
        return cast(ZenAbsoluteInput, registry[compound_id])

    def __init__(self, ctx: EntityContext, instance: ZenInstance) -> None:
        self.ctx = ctx
        self.commands = ctx.commands
        self.instance = instance

    @classmethod
    async def create(cls, ctx: EntityContext, instance: ZenInstance) -> ZenAbsoluteInput:
        """Async factory method for ZenAbsoluteInput."""
        absolute_input = cls(ctx, instance)
        await absolute_input.interview()
        return absolute_input

    def __repr__(self) -> str:
        return (
            f"ZenAbsoluteInput<{self.instance.address.controller.name} "
            f"ecd {self.instance.address.number} inst {self.instance.number}: "
            f"{self.label} / {self.instance_label}>"
        )

    def _reset(self) -> None:
        self.serial = None
        self.label = None
        self.instance_label = None
        self._value = None
        self.client_data = {}

    def interview_serialize(self) -> str:
        return json.dumps({
            "serial": self.serial,
            "label": self.label,
            "instance_label": self.instance_label,
        })

    def interview_hydrate(self, data: str | dict[str, Any]) -> bool:
        try:
            data = _loads_interview_data(data)
            self.serial = data.get("serial")
            self.label = data.get("label")
            self.instance_label = data.get("instance_label")
            self.instance.address.label = self.label
            self.instance.address.serial = cast(str | None, self.serial)
            _registered(self.instance.address.controller).absolute_inputs.add(self)
            return True
        except Exception:
            return False

    async def interview(self) -> bool:
        inst = self.instance
        addr = inst.address
        ctrl = _registered(addr.controller)
        if addr.label is None:
            addr.label = _or_device_label(
                await self.commands.query_dali_device_label(addr), addr
            )
        if addr.serial is None:
            addr.serial = cast(str | None, await self.commands.query_dali_serial(addr))
        self.label = addr.label
        self.serial = addr.serial
        self.instance_label = _or_instance_label(
            await self.commands.query_dali_instance_label(inst), inst
        )
        ctrl.absolute_inputs.add(self)
        return True

    @property
    def value(self) -> int | None:
        """Last-known 16-bit value from an absolute-input event, or None."""
        return self._value

    async def _event_received(self, payload: bytes) -> None:
        if len(payload) < 3:
            return
        new_value = (payload[1] << 8) | payload[2]
        changed = new_value != self._value
        self._value = new_value
        if changed and callable(self.ctx.callbacks.absolute_input_change):
            await self.ctx.callbacks.absolute_input_change(
                absolute_input=self, value=new_value
            )


class ZenMotionSensor:
    ctx: EntityContext
    commands: ZenCommandClient
    instance: ZenInstance
    hold_time: int = Const.DEFAULT_HOLD_TIME
    hold_expiry_task: asyncio.Task[None] | None = None
    serial: (int | str) | None = None
    label: str | None = None
    instance_label: str | None = None
    deadtime: int | None = None
    last_detect: float | None = None
    _occupied: bool | None = None
    client_data: dict[str, Any] = {}

    def __new__(cls, ctx: EntityContext, instance: ZenInstance) -> ZenMotionSensor:
        # Unique per context + controller + address + instance
        compound_id = f"{instance.address.controller.name} {instance.address.number} {instance.number}"
        registry = ctx.registry.motion_sensors
        if compound_id not in registry:
            inst = super().__new__(cls)
            registry[compound_id] = inst
            inst.ctx = ctx
            inst.commands = ctx.commands
            inst.instance = instance
            inst._reset()
            # Don't call interview() here - it will be called async later
        return cast(ZenMotionSensor, registry[compound_id])

    def __init__(self, ctx: EntityContext, instance: ZenInstance) -> None:
        self.ctx = ctx
        self.commands = ctx.commands
        self.instance = instance
    
    @classmethod
    async def create(cls, ctx: EntityContext, instance: ZenInstance) -> ZenMotionSensor:
        """Async factory method for ZenMotionSensor"""
        sensor = cls(ctx, instance)
        await sensor.interview()
        return sensor
    def __repr__(self) -> str:
        return f"ZenMotionSensor<{self.instance.address.controller.name} ecd {self.instance.address.number} inst {self.instance.number}: {self.label} / {self.instance_label}>"
    def _reset(self) -> None:
        self.hold_time = Const.DEFAULT_HOLD_TIME
        self.hold_expiry_task = None
        #
        self.serial = None
        self.label = None
        self.instance_label = None
        self.deadtime = None
        self.last_detect = None
        self._occupied = None
        #
        self.client_data = {}
    def interview_serialize(self) -> str:
        return json.dumps({
            "serial": self.serial,
            "label": self.label,
            "instance_label": self.instance_label,
            "deadtime": self.deadtime,
            "hold_time": self.hold_time,
        })
    def interview_hydrate(self, data: str | dict[str, Any]) -> bool:
        try:
            data = _loads_interview_data(data)
            self.serial = data.get("serial")
            self.label = data.get("label")
            self.instance_label = data.get("instance_label")
            self.deadtime = data.get("deadtime")
            self.hold_time = data.get("hold_time", Const.DEFAULT_HOLD_TIME)
            self._occupied = None
            self.instance.address.label = self.label
            self.instance.address.serial = cast(str | None, self.serial)
            _registered(self.instance.address.controller).motion_sensors.add(self)
            return True
        except Exception:
            return False
    async def interview(self) -> bool:
        inst = self.instance
        addr = inst.address
        ctrl = _registered(addr.controller)
        occupancy_timers = await self.commands.query_occupancy_instance_timers(inst)
        if occupancy_timers is not None:
            self.serial = await self.commands.query_dali_serial(addr)
            self.label = _or_device_label(
                await self.commands.query_dali_device_label(addr), addr
            )
            self.instance_label = _or_instance_label(
                await self.commands.query_dali_instance_label(inst), inst
            )
            self.deadtime = occupancy_timers["deadtime"]
            self.hold_time = occupancy_timers["hold"]
            self.last_detect = time.time() - occupancy_timers["last_detect"]
            self._occupied = None
        else:
            self._reset()
            return False
        # Add to controller's set of motion sensors
        ctrl.motion_sensors.add(self)
        return True

    async def refresh_state_from_controller(self) -> bool:
        """Query controller and update runtime occupancy fields."""
        inst = self.instance
        occupancy_timers = await self.commands.query_occupancy_instance_timers(inst)
        if occupancy_timers is None:
            self.last_detect = None
            self._occupied = None
            self.hold_expiry_task = None
            self.deadtime = None
            self.hold_time = Const.DEFAULT_HOLD_TIME
            return False

        # `last_detect` is stored as "time when last motion happened"
        # converted into a duration since last motion (same as interview()).
        self.deadtime = occupancy_timers["deadtime"]
        self.hold_time = occupancy_timers["hold"]
        self.last_detect = time.time() - occupancy_timers["last_detect"]
        self._occupied = None
        return True
    @property
    def occupied(self) -> bool:
        if self.last_detect is None:
            return False
        seconds_since_last_motion = time.time() - self.last_detect
        within_hold_time = seconds_since_last_motion < self.hold_time
        # if occupied but a hold task isn't running, start one with the time remaining
        if within_hold_time and self.hold_expiry_task is None:
            seconds_until_hold_time_expires = self.hold_time - seconds_since_last_motion
            self.hold_expiry_task = self.ctx.track_task(self._timeout_after_delay(seconds_until_hold_time_expires))
        return within_hold_time

    @occupied.setter 
    def occupied(self, new_value: bool) -> None:
        old_value = self._occupied or False
        # Cancel any hold time task
        if self.hold_expiry_task is not None:
            self.hold_expiry_task.cancel()
            self.hold_expiry_task = None
        # Start a new task
        if new_value:
            # Update last detect time, begin a task, and set occupied to True.
            # The occupied=True callback is fired by _event_received (which is
            # async and can await it properly).
            self.last_detect = time.time()
            self.hold_expiry_task = self.ctx.track_task(self._timeout_after_delay(self.hold_time))
            self._occupied = True
        else:
            self._occupied = False
            self.last_detect = None
            # If we're going from True to False, trigger motion event callback.
            # This branch is only reached when occupied is set to False directly
            # (not via _timeout_after_delay which handles the callback itself).
            if old_value is True:
                cb = self.ctx.callbacks.motion_event
                if callable(cb):
                    self.ctx.track_task(cast(Coroutine[Any, Any, None], cb(sensor=self, occupied=False)))

    async def _timeout_after_delay(self, delay: float) -> None:
        """Async method to handle motion sensor timeout"""
        await asyncio.sleep(delay)
        self._occupied = False
        self.last_detect = None
        self.hold_expiry_task = None
        # Trigger motion event callback
        if callable(self.ctx.callbacks.motion_event):
            await self.ctx.callbacks.motion_event(sensor=self, occupied=False)

    async def _event_received(self) -> None:
        # Capture old state before the setter updates it so we can fire the
        # callback with await instead of asyncio.create_task (fire-and-forget).
        was_occupied = self._occupied or False
        self.occupied = True
        if not was_occupied and callable(self.ctx.callbacks.motion_event):
            await self.ctx.callbacks.motion_event(sensor=self, occupied=True)


class ZenSystemVariable:
    ctx: EntityContext
    commands: ZenCommandClient
    controller: ZenController
    id: int
    label: str | None = None
    _value: int | None = None
    _future_value: int | None = None
    client_data: dict[str, Any] = {}

    def __new__(cls, ctx: EntityContext, controller: ZenController, id: int, value: int | None = None, label: str | None = None) -> ZenSystemVariable:
        # Unique per context + controller + id
        compound_id = f"{controller.name} {id}"
        registry = ctx.registry.system_variables
        if compound_id not in registry:
            inst = super().__new__(cls)
            registry[compound_id] = inst
            inst.ctx = ctx
            inst.commands = ctx.commands
            inst.controller = controller
            inst.id = id
            inst._reset()
            inst._value = value
            inst.label = label
            # Don't call interview() here - it will be called async later
        return cast(ZenSystemVariable, registry[compound_id])

    def __init__(self, ctx: EntityContext, controller: ZenController, id: int, value: int | None = None, label: str | None = None) -> None:
        self.ctx = ctx
        self.commands = ctx.commands
        self.controller = controller
        self.id = id
        if value is not None:
            self._value = value
        if label is not None:
            self.label = label
    
    @classmethod
    async def create(cls, ctx: EntityContext, controller: ZenController, id: int, value: int | None = None, label: str | None = None) -> ZenSystemVariable:
        """Async factory method for ZenSystemVariable"""
        sysvar = cls(ctx, controller, id, value, label)
        await sysvar.interview()
        return sysvar
    def __repr__(self) -> str:
        return f"ZenSystemVariable<{self.controller.name} sv {self.id}: {self.label}>"
    def _reset(self) -> None:
        self.label = None
        self._value = None
        self._future_value = None
        self.client_data = {}
    def interview_serialize(self) -> str:
        return json.dumps({
            "label": self.label,
        })
    def interview_hydrate(self, data: str | dict[str, Any]) -> bool:
        try:
            data = _loads_interview_data(data)
            self.label = data.get("label")
            self._future_value = None
            self.controller.sysvars.add(self)
            return True
        except Exception:
            return False
    async def interview(self) -> bool:
        ctrl = self.controller
        if self.label is None:
            self.label = await self.commands.query_system_variable_name(ctrl, self.id)
        if self._value is None:
            self._value = await self.commands.query_system_variable(ctrl, self.id)
        # Add to controller's set of system variables
        ctrl.sysvars.add(self)
        return True
    async def _event_received(self, new_value: int | None) -> None:
        changed = (new_value != self._value)
        by_me = (new_value == self._future_value)
        self._value = new_value
        self._future_value = None
        if changed:
            if callable(self.ctx.callbacks.system_variable_change):
                await self.ctx.callbacks.system_variable_change(system_variable=self,
                                  value=self._value,
                                  changed=changed,
                                  by_me=by_me)
    # -----------------------------------------------------------------------------------------
    # REMINDER: None of the following methods should update the internal object state directly.
    #   These methods send commands to the controller. The controller sends events back.
    #   The events update the internal state.
    # -----------------------------------------------------------------------------------------
    @property
    def value(self) -> int | None:
        """Return the last-known value without querying the controller."""
        return self._value

    async def get_value(self) -> int | None:
        """Get the current value of the system variable, querying the controller if unknown."""
        if self._value is None:
            self._value = await self.commands.query_system_variable(self.controller, self.id)
        return self._value

    async def refresh_state_from_controller(self) -> None:
        """Query the controller and update this system variable's runtime value."""
        new_value = await self.commands.query_system_variable(self.controller, self.id)
        await self._event_received(new_value)
    
    async def set_value(self, new_value: int) -> None:
        """Set the value of the system variable"""
        self._future_value = new_value # If we get this value back as an event, we'll know it's from us
        await self.commands.set_system_variable(self.controller, self.id, new_value)


# Callback type definitions (moved here after class definitions)
type ControllerRuntimeStatus = Literal["online", "starting", "unreachable"]
type CallbackOnConnect = Callable[[], Awaitable[None]]
type CallbackOnDisconnect = Callable[[], Awaitable[None]]
type CallbackOnResync = Callable[[], Awaitable[None]]
type CallbackProfileChange = Callable[[ZenProfile], Awaitable[None]]
type CallbackGroupChange = Callable[[ZenGroup, int], Awaitable[None]]
type CallbackLightChange = Callable[[ZenLight, int, ZenColour, int], Awaitable[None]]
type CallbackButtonPress = Callable[[ZenButton], Awaitable[None]]
type CallbackButtonLongPress = Callable[[ZenButton], Awaitable[None]]
type CallbackAbsoluteInputChange = Callable[[ZenAbsoluteInput, int], Awaitable[None]]
type CallbackMotionEvent = Callable[[ZenMotionSensor, bool], Awaitable[None]]
type CallbackSystemVariableChange = Callable[[ZenSystemVariable, int, bool, bool], Awaitable[None]]
type CallbackControllerDiscovered = Callable[[DiscoveredController], Awaitable[None]]
type CallbackControllerIdentified = Callable[[SuperZenController, str], Awaitable[None]]
type CallbackControllerStatusChange = Callable[
    [ZenController, ControllerRuntimeStatus], Awaitable[None]
]
