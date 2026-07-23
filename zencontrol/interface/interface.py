from __future__ import annotations

import asyncio
import json
import time
import logging
from typing import Any, cast, Self
from collections.abc import Coroutine, Callable, Awaitable

from ..api import ZenProtocol, ZenController as SuperZenController, ZenAddress, ZenInstance, ZenAddressType, ZenColour, ZenColourType, ZenInstanceType
from ..api.models import DiscoveredController
from ..api.protocol import ZenCallbacks
from ..api.types import Const
from ..exceptions import ZenConnectionError

"""
===================================================================================
This module takes the ZenControl API and provides a higher level interface
intended for use in a control interface or home automation system written in Python.
===================================================================================



Terms:
ZenProtocol = A class which implements the ZenControl TPI Advanced API using zen_io.
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
        case _:
            return None


def _serialize_group_address(address: ZenAddress) -> dict[str, int]:
    return {"number": address.number}


def _loads_interview_data(data: str | dict[str, Any]) -> dict[str, Any]:
    if isinstance(data, str):
        loaded: dict[str, Any] = json.loads(data)
        return loaded
    return data


class ZenControl:
    def __init__(self,
                 logger: logging.Logger | None = None,
                 print_traffic: bool = False,
                 unicast: bool = False,
                 listen_ip: str | None = None,
                 listen_port: int | None = None,
                 cache: dict[bytes, dict[str, Any]] | None = None
                 ):
        self.logger = logger or logging.getLogger(__name__)
        self.protocol: ZenProtocol = ZenProtocol(logger=self.logger, print_traffic=print_traffic, unicast=unicast, listen_ip=listen_ip, listen_port=listen_port, cache=cache if cache is not None else {})
        self.controllers: list[ZenController] = []
        # Each ZenControl instance gets its own callback registry so multiple
        # instances (e.g. an integration and a test connection) cannot interfere.
        self.protocol.callbacks = ZenCallbacks()
        self._stopping = False
        self._supervisor_task: asyncio.Task[None] | None = None
        self._first_connected = asyncio.Event()
        self.reconnect_min_delay = Const.RECONNECT_MIN_DELAY
        self.reconnect_max_delay = Const.RECONNECT_MAX_DELAY
        self.reconnect_healthy_seconds = Const.RECONNECT_HEALTHY_SECONDS

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        await self.aclose()

    def clear_entity_caches(self) -> None:
        """Clear entity singleton registries for this ZenControl instance."""
        self.protocol.clear_entity_cache()

    @property
    def cache(self) -> dict[bytes, dict[str, Any]]:
        return self.protocol.cache

    @property
    def on_connect(self) -> CallbackOnConnect | None:
        return self.protocol.callbacks.on_connect
    @on_connect.setter
    def on_connect(self, func: CallbackOnConnect | None) -> None:
        self.protocol.callbacks.on_connect = func

    @property
    def on_disconnect(self) -> CallbackOnDisconnect | None:
        return self.protocol.callbacks.on_disconnect
    @on_disconnect.setter
    def on_disconnect(self, func: CallbackOnDisconnect | None) -> None:
        self.protocol.callbacks.on_disconnect = func

    @property
    def profile_change(self) -> CallbackProfileChange | None:
        return self.protocol.callbacks.profile_change
    @profile_change.setter
    def profile_change(self, func: CallbackProfileChange | None) -> None:
        self.protocol.callbacks.profile_change = func

    @property
    def group_change(self) -> CallbackGroupChange | None:
        return self.protocol.callbacks.group_change
    @group_change.setter
    def group_change(self, func: CallbackGroupChange | None) -> None:
        self.protocol.callbacks.group_change = func

    @property
    def light_change(self) -> CallbackLightChange | None:
        return self.protocol.callbacks.light_change
    @light_change.setter
    def light_change(self, func: CallbackLightChange | None) -> None:
        self.protocol.callbacks.light_change = func

    @property
    def button_press(self) -> CallbackButtonPress | None:
        return self.protocol.callbacks.button_press
    @button_press.setter
    def button_press(self, func: CallbackButtonPress | None) -> None:
        self.protocol.callbacks.button_press = func
    
    @property
    def button_long_press(self) -> CallbackButtonLongPress | None:
        return self.protocol.callbacks.button_long_press
    @button_long_press.setter
    def button_long_press(self, func: CallbackButtonLongPress | None) -> None:
        self.protocol.callbacks.button_long_press = func
    
    @property
    def motion_event(self) -> CallbackMotionEvent | None:
        return self.protocol.callbacks.motion_event
    @motion_event.setter
    def motion_event(self, func: CallbackMotionEvent | None) -> None:
        self.protocol.callbacks.motion_event = func
    
    @property
    def system_variable_change(self) -> CallbackSystemVariableChange | None:
        return self.protocol.callbacks.system_variable_change
    @system_variable_change.setter
    def system_variable_change(self, func: CallbackSystemVariableChange | None) -> None:
        self.protocol.callbacks.system_variable_change = func

    @property
    def controller_discovered(self) -> CallbackControllerDiscovered | None:
        return self.protocol.callbacks.controller_discovered
    @controller_discovered.setter
    def controller_discovered(self, func: CallbackControllerDiscovered | None) -> None:
        self.protocol.callbacks.controller_discovered = func

    @property
    def discovered_controllers(self) -> list[DiscoveredController]:
        """Controllers identified from multicast but not yet registered."""
        return list(self.protocol.identified_controllers)

    # ============================
    # Setup / Start / Stop
    # ============================

    def add_controller(self, id: int, name: str, label: str, host: str, port: int = 5108, mac: str | None = None, filtering: bool = False) -> ZenController:
        controller = ZenController(protocol=self.protocol, id=id, name=name, label=label, host=host, port=port, mac=mac, filtering=filtering)
        self.controllers.append(controller)
        # list is invariant; protocol expects the API-level ZenController type
        self.protocol.set_controllers(cast(list[SuperZenController], self.controllers))
        return controller

    async def discover(self, timeout: float = 5.0) -> list[DiscoveredController]:
        """Listen for multicast and return controllers identified within ``timeout`` seconds.

        Starts event monitoring if needed. Works with zero registered controllers
        and also reports controllers that are not already registered while running.
        """
        before = {
            (d.mac.upper().replace("-", ":"), d.host)
            for d in self.protocol.identified_controllers
        }
        started_here = False
        if self._supervisor_task is None or self._supervisor_task.done():
            await self.start()
            started_here = True
        try:
            await asyncio.sleep(timeout)
        finally:
            if started_here:
                await self.stop()
        return [
            d
            for d in self.protocol.identified_controllers
            if (d.mac.upper().replace("-", ":"), d.host) not in before
        ]

    async def start(self) -> None:
        """Start event monitoring with automatic reconnect on unexpected loss."""
        self._stopping = False
        self._first_connected = asyncio.Event()
        self.protocol.set_callbacks(
            button_press_callback = self.button_press_event,
            button_hold_callback = self.button_hold_event,
            absolute_input_callback = self.absolute_input_event,
            level_change_callback = self.level_change_event,
            group_level_change_callback = self.level_change_event,
            scene_change_callback = self.scene_change_event,
            is_occupied_callback = self.is_occupied_event,
            system_variable_change_callback = self.system_variable_change_event,
            colour_change_callback = self.colour_change_event,
            profile_change_callback = self.profile_change_event,
            disconnect_callback = self._protocol_disconnect_event,
        )
        if self._supervisor_task is None or self._supervisor_task.done():
            self._supervisor_task = asyncio.create_task(self._event_monitor_supervisor())
        try:
            await asyncio.wait_for(
                self._first_connected.wait(),
                timeout=Const.START_TIMEOUT,
            )
        except asyncio.TimeoutError as err:
            await self.stop()
            raise ZenConnectionError(
                f"Event monitoring failed to connect within {Const.START_TIMEOUT:.0f}s"
            ) from err

    async def stop(self) -> None:
        """Stop reconnect supervisor and event monitoring (keeps entity caches)."""
        self._stopping = True
        was_running = bool(
            self.protocol.event_task and not self.protocol.event_task.done()
        )
        await self._cancel_supervisor()
        await self.protocol.stop_event_monitoring()
        if was_running:
            await self.protocol.notify_disconnect()

    async def aclose(self) -> None:
        """Stop monitoring, cancel background tasks, close UDP clients, clear entity caches."""
        self._stopping = True
        was_running = bool(
            self.protocol.event_task and not self.protocol.event_task.done()
        )
        await self._cancel_supervisor()
        await self.protocol.aclose()
        if was_running:
            await self.protocol.notify_disconnect()
        self.clear_entity_caches()

    async def _cancel_supervisor(self) -> None:
        task = self._supervisor_task
        self._supervisor_task = None
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def _event_monitor_supervisor(self) -> None:
        """Keep the event listener running; reconnect with backoff after unexpected loss."""
        delay = self.reconnect_min_delay
        while not self._stopping:
            try:
                await self.protocol.start_event_monitoring()
            except asyncio.CancelledError:
                raise
            except Exception as err:
                self.logger.error(f"Failed to start event monitoring: {err}")
                if self._stopping:
                    return
                await asyncio.sleep(delay)
                delay = min(delay * 2, self.reconnect_max_delay)
                continue

            if callable(self.protocol.callbacks.on_connect):
                try:
                    await self.protocol.callbacks.on_connect()
                except Exception as err:
                    self.logger.error(f"on_connect error: {err}")
            self._first_connected.set()
            delay = self.reconnect_min_delay

            event_task = self.protocol.event_task
            if event_task is None:
                continue

            session_start = time.time()
            try:
                await event_task
            except asyncio.CancelledError:
                # HA (and asyncio) cancel tasks on shutdown before unload can set
                # _stopping. Never reconnect on cancel — always exit.
                raise
            except Exception as err:
                self.logger.error(f"Event monitor task error: {err}")

            if self._stopping:
                return

            session_secs = time.time() - session_start
            if session_secs >= self.reconnect_healthy_seconds:
                delay = self.reconnect_min_delay
            else:
                delay = min(max(delay, self.reconnect_min_delay) * 2, self.reconnect_max_delay)

            await self._prepare_for_reconnect()
            self.logger.warning(
                "Event monitoring stopped; reconnecting in %.1fs", delay
            )
            try:
                await asyncio.sleep(delay)
            except asyncio.CancelledError:
                raise

    async def _prepare_for_reconnect(self) -> None:
        """Drop stale clients and refresh DNS before binding the listener again."""
        for controller in self.controllers:
            controller.refresh_ip()
            client = controller.client
            controller.client = None
            if client is None:
                continue
            try:
                await client.close()
            except Exception:
                pass

    async def _protocol_disconnect_event(self) -> None:
        """Forward protocol-level disconnect to the high-level on_disconnect hook."""
        if callable(self.protocol.callbacks.on_disconnect):
            await self.protocol.callbacks.on_disconnect()

    # ============================
    # ZenProtocol callbacks
    # ============================ 
        
    async def button_press_event(self, instance: ZenInstance, payload: bytes) -> None:
        await ZenButton(protocol=self.protocol, instance=instance)._event_received()

    async def button_hold_event(self, instance: ZenInstance, payload: bytes) -> None:
        await ZenButton(protocol=self.protocol, instance=instance)._event_received(held=True)

    async def absolute_input_event(self, instance: ZenInstance, payload: bytes) -> None:
        pass

    async def is_occupied_event(self, instance: ZenInstance, payload: bytes) -> None:
        await ZenMotionSensor(protocol=self.protocol, instance=instance)._event_received()

    async def level_change_event(self, address: ZenAddress, arc_level: int, payload: bytes) -> None:
        # LEVEL_CHANGE_V2: payload[1] is the dimming destination — ignore current level in payload[0]
        if len(payload) >= 2:
            arc_level = payload[1]
        if address.type == ZenAddressType.ECG:
            light = ZenLight(protocol=self.protocol, address=address)
            await light._event_received(level=arc_level)

            # Delay the light event to allow group updates to arrive and propogate
            # async def delayed_event():
            #     await asyncio.sleep(0.1)
            #     await light._event_received(level=arc_level)
            # asyncio.create_task(delayed_event())
        elif address.type == ZenAddressType.GROUP:
            group = ZenGroup(protocol=self.protocol, address=address)
            await group._event_received(level=arc_level)

            # Don't cascade groups. Group change events are untrustworthy.
            # for light in group.lights:
            #     await light._event_received(level=arc_level, cascaded_from=group)

    async def colour_change_event(self, address: ZenAddress, colour: ZenColour | None, payload: bytes) -> None:
        # Protocol already parses payload via ZenColour.from_bytes before calling us
        if colour is None:
            return
        if address.type == ZenAddressType.ECG:
            # Delay the light event to allow group updates to arrive and propogate
            ecg = ZenLight(protocol=self.protocol, address=address)
            async def delayed_colour_event():
                await asyncio.sleep(0.0)
                await ecg._event_received(colour=colour)
            self.protocol.track_task(delayed_colour_event())
        elif address.type == ZenAddressType.GROUP:
            group = ZenGroup(protocol=self.protocol, address=address)
            await group._event_received(colour=colour)
            for light in group.lights:
                await light._event_received(colour=colour, cascaded_from=group)

    async def scene_change_event(self, address: ZenAddress, scene: int, active: bool, payload: bytes) -> None:
        if address.type == ZenAddressType.ECG:
            # Delay the light event to allow group updates to arrive and propogate
            ecg = ZenLight(protocol=self.protocol, address=address)
            # Option 3: Inline async function with shorter name
            async def delayed_scene_event():
                await asyncio.sleep(0.0)
                await ecg._event_received(scene=scene, active=active)
            self.protocol.track_task(delayed_scene_event())
        elif address.type == ZenAddressType.GROUP:
            group = ZenGroup(protocol=self.protocol, address=address)
            await group._event_received(scene=scene, active=active)
            for light in group.lights:
                await light._event_received(scene=scene, active=active, cascaded_from=group)
    
    async def system_variable_change_event(self, controller: ZenController, target: int, value: int, payload: bytes) -> None:
        await ZenSystemVariable(protocol=self.protocol, controller=controller, id=target)._event_received(value)

    async def profile_change_event(self, controller: ZenController, profile: int, payload: bytes) -> None:
        await controller._event_received(profile=profile)
    
    # ============================
    # Abstraction layer commands
    # ============================ 

    async def get_profiles(self, controller: ZenController | None = None) -> set[ZenProfile]:
        """Return a set of all profiles."""
        profiles: set[ZenProfile] = set()
        controllers = [controller] if controller else self.controllers
        for ctrl in controllers:
            numbers = await self.protocol.query_profile_numbers(controller=ctrl)
            if numbers is None:
                continue
            for number in numbers:
                profile = await ZenProfile.create(protocol=self.protocol, controller=ctrl, number=number)
                profiles.add(profile)
        return profiles

    async def get_groups(self) -> set[ZenGroup]:
        """Return a set of all groups."""
        groups: set[ZenGroup] = set()
        for controller in self.controllers:
            addresses = await self.protocol.query_group_numbers(controller=controller)
            for address in addresses:
                group = await ZenGroup.create(protocol=self.protocol, address=address)
                groups.add(group)
        return groups
    
    async def get_lights(self) -> set[ZenLight]:
        """Return a set of all lights available."""
        lights: set[ZenLight] = set()
        for controller in self.controllers:
            addresses = await self.protocol.query_control_gear_dali_addresses(controller=controller)
            for address in addresses:
                light = await ZenLight.create(protocol=self.protocol, address=address)
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
            batch = await self.protocol.query_dali_addresses_with_instances(
                controller=controller, start_address=start
            )
            for addr in batch:
                key = (addr.controller.name, addr.number)
                if key not in seen:
                    seen.add(key)
                    addresses.append(addr)
        return addresses

    async def get_buttons(self) -> set[ZenButton]:
        """Return a set of all buttons available."""
        buttons: set[ZenButton] = set()
        for controller in self.controllers:
            addresses = await self._get_addresses_with_instances(controller)
            for address in addresses:
                instances = await self.protocol.query_instances_by_address(address=address)
                for instance in instances:
                    if instance.type == ZenInstanceType.PUSH_BUTTON:
                        button = await ZenButton.create(protocol=self.protocol, instance=instance)
                        buttons.add(button)
        return buttons
    
    async def get_motion_sensors(self) -> set[ZenMotionSensor]:
        """Return a set of all motion sensors available."""
        motion_sensors: set[ZenMotionSensor] = set()
        for controller in self.controllers:
            addresses = await self._get_addresses_with_instances(controller)
            for address in addresses:
                instances = await self.protocol.query_instances_by_address(address=address)
                for instance in instances:
                    if instance.type == ZenInstanceType.OCCUPANCY_SENSOR:
                        motion_sensor = await ZenMotionSensor.create(protocol=self.protocol, instance=instance)
                        motion_sensors.add(motion_sensor)
        return motion_sensors

    async def get_system_variables(self, give_up_after: int = 10) -> set[ZenSystemVariable]:
        """Return a set of all system variables. Variables must have a label. Searching will give_up_after [x] sequential IDs without a label."""
        sysvars: set[ZenSystemVariable] = set()
        failed_attempts = 0
        for controller in self.controllers:
            for variable in range(Const.MAX_SYSVAR):
                label = await self.protocol.query_system_variable_name(controller=controller, variable=variable)
                if label:
                    failed_attempts = 0
                    sysvar = await ZenSystemVariable.create(protocol=self.protocol, controller=controller, id=variable, label=label)
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
    # Narrow/override dataclass fields used by the interface layer
    protocol: ZenProtocol
    version: str | None = None

    connected: bool = False
    profile: ZenProfile | None = None
    profiles: set[ZenProfile] = set()
    lights: set[ZenLight] = set()
    groups: set[ZenGroup] = set()
    buttons: set[ZenButton] = set()
    motion_sensors: set[ZenMotionSensor] = set()
    sysvars: set[ZenSystemVariable] = set()
    client_data: dict[str, Any] = {}

    def __new__(cls, protocol: ZenProtocol, id: int, name: str, label: str, host: str, port: int = 5108, mac: str | None = None, filtering: bool = False) -> ZenController:
        # Unique per protocol + controller name
        registry = protocol.entity_registry.controllers
        if name not in registry:
            inst = super().__new__(cls)
            registry[name] = inst
            inst.connected = False
            inst.client = None  # Will be initialized when first used
            object.__setattr__(inst, "_ip", None)
            object.__setattr__(inst, "mac_bytes", None)
            object.__setattr__(inst, "_dataclass_initialized", False)
            inst._reset()
            # Don't call interview() here - it will be called async later
        inst = registry[name]
        # Always refresh config fields; never wipe transport/interview state via __init__
        inst.protocol = protocol
        inst.id = str(id)
        inst.name = name
        inst.label = label
        inst.host = host
        inst.port = port
        inst.mac = mac
        inst.filtering = filtering
        inst._update_mac_bytes(mac)
        return inst

    def __init__(self, protocol: ZenProtocol, id: int, name: str, label: str, host: str, port: int = 5108, mac: str | None = None, filtering: bool = False) -> None:
        # Dataclass __init__ resets client/version/etc. Run only once per singleton.
        if getattr(self, "_dataclass_initialized", False):
            return
        super().__init__(
            id=str(id),
            name=name,
            label=label,
            host=host,
            port=port,
            mac=mac,
            protocol=protocol,
            filtering=filtering,
        )
        object.__setattr__(self, "_dataclass_initialized", True)
    
    @classmethod
    async def create(cls, protocol: ZenProtocol, id: int, name: str, label: str, host: str, port: int = 5108, mac: str | None = None, filtering: bool = False) -> ZenController:
        """Async factory method for ZenController"""
        controller = cls(protocol=protocol, id=id, name=name, label=label, host=host, port=port, mac=mac, filtering=filtering)
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
        self.motion_sensors = set()
        self.sysvars = set()
        self.client_data = {}
    async def interview(self) -> bool:
        protocol = self.protocol
        if self.label is None or self.label == "":
            queried = await protocol.query_controller_label(self)
            if queried is not None:
                self.label = queried
        self.version = await protocol.query_controller_version_number(self)
        current_profile = await protocol.query_current_profile_number(self)
        if current_profile is not None:
            self.profile = ZenProfile(protocol=protocol, controller=self, number=current_profile)
        self.connected = True
        return True
    async def _event_received(self, profile: int | None = None):
        protocol = self.protocol
        if profile is not None:
            self.profile = ZenProfile(protocol=protocol, controller=self, number=profile)
            cb = protocol.callbacks.profile_change
            if callable(cb):
                await cb(profile=self.profile)
    def get_sysvar(self, id: int) -> ZenSystemVariable:
        return ZenSystemVariable(protocol=self.protocol, controller=self, id=id)
    async def is_controller_ready(self) -> bool | None:
        return await self.protocol.query_controller_startup_complete(self)
    async def is_dali_ready(self) -> bool | None:
        return await self.protocol.query_is_dali_ready(self)
    async def switch_to_profile(self, profile: "ZenProfile|int|str") -> bool:
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
            self.protocol.logger.debug("Switching to profile %s", zp)
            result = await self.protocol.change_profile_number(self, zp.number)
            return bool(result)
        else:
            return False
    async def return_to_scheduled_profile(self) -> bool | None:
        return await self.protocol.return_to_scheduled_profile(self)


class ZenProfile:
    protocol: ZenProtocol
    controller: ZenController
    number: int
    label: str | None = None
    client_data: dict[str, Any] = {}

    def __new__(cls, protocol: ZenProtocol, controller: ZenController, number: int) -> ZenProfile:
        # Unique per protocol + controller + profile number
        compound_id = f"{controller.name} {number}"
        registry = protocol.entity_registry.profiles
        if compound_id not in registry:
            inst = super().__new__(cls)
            registry[compound_id] = inst
            inst.protocol = protocol
            inst.controller = controller
            inst.number = number
            inst._reset()
            # Don't call interview() here - it will be called async later
        return registry[compound_id]

    def __init__(self, protocol: ZenProtocol, controller: ZenController, number: int) -> None:
        self.protocol = protocol
        self.controller = controller
        self.number = number
    
    @classmethod
    async def create(cls, protocol: ZenProtocol, controller: ZenController, number: int) -> ZenProfile:
        """Async factory method for ZenProfile"""
        profile = cls(protocol, controller, number)
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
        self.label = await self.protocol.query_profile_label(self.controller, self.number)
        # Add self to controller's set of profiles
        self.controller.profiles.add(self)
        return True
    async def select(self) -> bool:
        result = await self.protocol.change_profile_number(self.controller, self.number)
        return bool(result)


class ZenLight:
    protocol: ZenProtocol
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

    def __new__(cls, protocol: ZenProtocol, address: ZenAddress) -> Self:
        # Inherited classes should bypass ZenLight __new__
        if cls is not ZenLight:
            return cast(Self, super().__new__(cls))
        # Unique per protocol + controller + address
        compound_id = f"{address.controller.name} {address.number}"
        registry = protocol.entity_registry.lights
        if compound_id not in registry:
            inst = super().__new__(cls)
            registry[compound_id] = inst
            inst.protocol = protocol
            inst.address = address
            inst._reset()
            # Don't call interview() here - it will be called async later
        return cast(Self, registry[compound_id])

    def __init__(self, protocol: ZenProtocol, address: ZenAddress) -> None:
        self.protocol = protocol
        self.address = address
    
    @classmethod
    async def create(cls, protocol: ZenProtocol, address: ZenAddress) -> ZenLight:
        """Async factory method for ZenLight"""
        instance = cls(protocol, address)
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
            group = ZenGroup(protocol=self.protocol, address=group_address)
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
            cast(ZenController, self.address.controller).lights.add(self)
            return True
        except Exception:
            return False
    async def interview(self) -> bool:
        cgstatus = await self.protocol.dali_query_control_gear_status(self.address)
        if cgstatus:
            self.label = await self.protocol.query_dali_device_label(self.address, generic_if_none=True)
            self.serial = await self.protocol.query_dali_serial(self.address)
            self.cgtype = await self.protocol.dali_query_cg_type(self.address) or []
            
            # If cgtype contains 6, it supports brightness
            if 6 in self.cgtype:
                self.features["brightness"] = True
            
            # If cgtype contains 8, it supports some kind of colour
            if 8 in self.cgtype:
                cgtype = await self.protocol.query_dali_colour_features(self.address)
                if cgtype and cgtype.get("supports_tunable", False) is True:
                    self.features["brightness"] = True
                    self.features["temperature"] = True
                    colour_temp_limits = await self.protocol.query_dali_colour_temp_limits(self.address)
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
            self._scene_levels = await self.protocol.query_scene_levels_by_address(self.address)
            self._scene_colours = await self.protocol.query_scene_colours_by_address(self.address)

            # Groups
            groups = await self.protocol.query_group_membership_by_address(self.address)
            self._apply_group_membership(groups or [])
            
            # Add to controller's set of lights
            cast(ZenController, self.address.controller).lights.add(self)

            return True
        else:
            self._reset()
            return False
    async def refresh_state_from_controller(self, verifying: bool = False):
        
        existing_level = self.level
        existing_colour = self.colour
        existing_scene = self.scene

        refreshed_level = await self.protocol.dali_query_level(self.address)
        refreshed_colour = None
        refreshed_scene = None
        if await self.protocol.dali_query_last_scene_is_current(self.address):
            refreshed_scene = await self.protocol.dali_query_last_scene(self.address)
        if self.features["temperature"] or self.features["RGB"] or self.features["RGBW"] or self.features["RGBWW"]:
            refreshed_colour = await self.protocol.query_dali_colour(self.address)
        
        if verifying:
            # Level is driven by LEVEL_CHANGE_V2 dimming-to events; query returns current arc mid-fade
            if refreshed_level is not None and self.level != refreshed_level:
                self.protocol.logger.debug(
                    f"Light {self.address.number} queried level {refreshed_level} "
                    f"differs from tracked destination {self.level} (expected during fade)"
                )
            refreshed_level = None
            if self.colour != refreshed_colour:
                self.protocol.logger.error(f"Light {self.address.number} colour mismatch! We had {self.colour}, actual colour is {refreshed_colour}")
            if self.scene != refreshed_scene:
                self.protocol.logger.error(f"Light {self.address.number} scene mismatch! We had {self.scene}, actual scene is {refreshed_scene}")
        
        # Mimic an incoming scene event when the controller reports the last
        # scene is current. This ensures we also update `self.scene`.
        await self._event_received(
            level=refreshed_level,
            colour=refreshed_colour,
            scene=refreshed_scene,
            active=(refreshed_scene is not None and not verifying),
            verifying=verifying,
        )

    def _start_refresh_timer(self):
        """Start a 2-second timer to refresh from controller after API user changes state."""
        # Cancel any existing timer
        if self._refresh_timer and not self._refresh_timer.done():
            self._refresh_timer.cancel()
        
        # Start new timer (which quietly dies if cancelled)
        async def delayed_refresh():
            try:
                await asyncio.sleep(2.0)
                await self.refresh_state_from_controller(verifying=True)
            except asyncio.CancelledError:
                pass
        
        self._refresh_timer = self.protocol.track_task(delayed_refresh())

    async def _event_received(self,
            level: int|None = 255,
            colour: ZenColour | None = None,
            scene: int | None = None,
            active: bool | None = None,
            cascaded_from: ZenGroup | None = None,
            verifying: bool = False
        ):
        # Called by ZenProtocol when a query command is issued or an event is received
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
                if callable(self.protocol.callbacks.group_change):
                    await self.protocol.callbacks.group_change(group=self,
                                    level=self.level if level_changed else None,
                                    colour=self.colour if colour_changed else None,
                                    scene=self.scene if scene_changed else None)
        elif type(self) is ZenLight:
            if level_changed or colour_changed or scene_changed:
                if callable(self.protocol.callbacks.light_change):
                    await self.protocol.callbacks.light_change(light=self,
                                    level=self.level if level_changed else None,
                                    colour=self.colour if colour_changed else None,
                                    scene=self.scene if scene_changed else None)
    def supports_colour(self, colour: "ZenColourType|ZenColour") -> bool:
        if type(colour) == ZenColour:
            colour_type = colour.type
        elif type(colour) == ZenColourType:
            colour_type = colour
        else:
            return False;
        if (colour_type == ZenColourType.TC and self.features["temperature"]) or \
            (colour_type == ZenColourType.RGBWAF and self.features["RGB"]) or \
            (colour_type == ZenColourType.RGBWAF and self.features["RGBW"]) or \
            (colour_type == ZenColourType.RGBWAF and self.features["RGBWW"]):
            return True
        return False
    # -----------------------------------------------------------------------------------------
    # REMINDER: None of the following methods should update the internal object state directly.
    #   These methods send commands to the controller. The controller sends events back.
    #   The events update the internal state.
    # -----------------------------------------------------------------------------------------
    async def on(self, fade: bool = True) -> bool | None:
        self._start_refresh_timer()
        if not fade: await self.protocol.dali_enable_dapc_sequence(self.address)
        return await self.protocol.dali_go_to_last_active_level(self.address)
    async def off(self, fade: bool = True) -> bool | None:
        self._start_refresh_timer()
        if fade: return await self.protocol.dali_arc_level(self.address, 0)
        else: return await self.protocol.dali_off(self.address)
    async def set_scene(self, scene: int|str|dict[str, Any], fade: bool = True) -> bool | None:
        self._start_refresh_timer()
        if type(scene) == str:
            scene = next((i for i, s in enumerate(self._scene_labels) if s == scene), False)
        if type(scene) == int:
            if not fade: await self.protocol.dali_enable_dapc_sequence(self.address)
            return await self.protocol.dali_scene(self.address, scene)
        return False
    async def set(self, level: int = 255, colour: ZenColour | None = None, fade: bool = True) -> bool | None:
        self._start_refresh_timer()
        if colour is not None and self.supports_colour(colour):
            if not fade: await self.protocol.dali_enable_dapc_sequence(self.address)
            return await self.protocol.dali_colour(self.address, colour, level)
        if 0 <= level <= 254:
            if fade:
                return await self.protocol.dali_arc_level(self.address, level)
            else:
                return await self.protocol.dali_custom_fade(self.address, level, 0)
        return False
    async def dali_on_step_up(self) -> bool | None:
        self._start_refresh_timer()
        return await self.protocol.dali_on_step_up(self.address)
    async def dali_step_down_off(self) -> bool | None:
        self._start_refresh_timer()
        return await self.protocol.dali_step_down_off(self.address)
    async def dali_up(self) -> bool | None:
        self._start_refresh_timer()
        return await self.protocol.dali_up(self.address)
    async def dali_down(self) -> bool | None:
        self._start_refresh_timer()
        return await self.protocol.dali_down(self.address)
    async def dali_recall_max(self) -> bool | None:
        self._start_refresh_timer()
        return await self.protocol.dali_recall_max(self.address)
    async def dali_recall_min(self) -> bool | None:
        self._start_refresh_timer()
        return await self.protocol.dali_recall_min(self.address)
    async def dali_go_to_last_active_level(self) -> bool | None:
        self._start_refresh_timer()
        return await self.protocol.dali_go_to_last_active_level(self.address)
    async def dali_off(self) -> bool | None:
        self._start_refresh_timer()
        return await self.protocol.dali_off(self.address)
    async def dali_custom_fade(self, level: int, duration: int) -> bool | None:
        self._start_refresh_timer()
        return await self.protocol.dali_custom_fade(self.address, level, duration)
    async def dali_stop_fade(self) -> bool | None:
        self._start_refresh_timer()
        return await self.protocol.dali_stop_fade(self.address)
    async def dali_enable_dapc_sequence(self) -> bool | None:
        return await self.protocol.dali_enable_dapc_sequence(self.address)
    async def dali_inhibit(self, inhibit: bool = True) -> bool | None:
        time_seconds = 65535 if inhibit else 0
        return await self.protocol.dali_inhibit(self.address, time_seconds)
        

class ZenGroup(ZenLight):
    lights: set[ZenLight] = set()

    def __new__(cls, protocol: ZenProtocol, address: ZenAddress) -> ZenGroup:
        # Unique per protocol + controller + group address
        compound_id = f"{address.controller.name} g{address.number}"
        registry = protocol.entity_registry.groups
        if compound_id not in registry:
            inst = super().__new__(cls, protocol=protocol, address=address)
            registry[compound_id] = inst
            inst.protocol = protocol
            inst.address = address
            inst.lights = set()  # member lights; managed via ZenLight._apply_group_membership
            inst._reset()
            # Don't call interview() here - it will be called async later
        return registry[compound_id]

    def __init__(self, protocol: ZenProtocol, address: ZenAddress) -> None:
        super().__init__(protocol, address)
    
    @classmethod
    async def create(cls, protocol: ZenProtocol, address: ZenAddress) -> ZenGroup:
        """Async factory method for ZenGroup"""
        group = cls(protocol, address)
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
            cast(ZenController, self.address.controller).groups.add(self)
            return True
        except Exception:
            return False
    async def interview(self) -> bool:
        self.label = await self.protocol.query_group_label(self.address, generic_if_none=True)
        self._scene_labels = await self.protocol.query_scenes_for_group(self.address, generic_if_none=True)
        # Add to controller's set of groups
        cast(ZenController, self.address.controller).groups.add(self)
        return True
    def supports_colour(self, colour: "ZenColourType|ZenColour") -> bool:
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
    async def declare_discoordination(self):
        # Only do something if the group claims to be coordinated
        if self.level is None and self.colour is None and self.scene is None:
            return
        # This is called when members of the group are no longer in a uniform state
        self.level = None
        self.colour = None
        self.scene = None
        if callable(self.protocol.callbacks.group_change):
            await self.protocol.callbacks.group_change(group=self,
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
    protocol: ZenProtocol
    instance: ZenInstance
    serial: (int | str) | None = None
    label: str | None = None
    instance_label: str | None = None
    last_press_time: float = 0.0
    long_press_count: int = 0
    client_data: dict[str, Any] = {}

    def __new__(cls, protocol: ZenProtocol, instance: ZenInstance) -> ZenButton:
        # Unique per protocol + controller + address + instance
        compound_id = f"{instance.address.controller.name} {instance.address.number} {instance.number}"
        registry = protocol.entity_registry.buttons
        if compound_id not in registry:
            inst = super().__new__(cls)
            registry[compound_id] = inst
            inst.protocol = protocol
            inst.instance = instance
            inst._reset()
            # Don't call interview() here - it will be called async later
        return registry[compound_id]

    def __init__(self, protocol: ZenProtocol, instance: ZenInstance) -> None:
        self.protocol = protocol
        self.instance = instance
    
    @classmethod
    async def create(cls, protocol: ZenProtocol, instance: ZenInstance) -> ZenButton:
        """Async factory method for ZenButton"""
        button = cls(protocol, instance)
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
            cast(ZenController, self.instance.address.controller).buttons.add(self)
            return True
        except Exception:
            return False
    async def interview(self) -> bool:
        inst = self.instance
        addr = inst.address
        ctrl = cast(ZenController, addr.controller)
        if addr.label is None: addr.label = await self.protocol.query_dali_device_label(addr, generic_if_none=True)
        if addr.serial is None: addr.serial = cast(str | None, await self.protocol.query_dali_serial(addr))
        self.label = addr.label
        self.serial = addr.serial
        self.instance_label = await self.protocol.query_dali_instance_label(inst, generic_if_none=True)
        # Add to controller's set of buttons
        ctrl.buttons.add(self)
        return True
    async def _event_received(self, held: bool = False):
        if not held:
            if callable(self.protocol.callbacks.button_press):
                await self.protocol.callbacks.button_press(button=self)
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
                if callable(self.protocol.callbacks.button_long_press):
                    await self.protocol.callbacks.button_long_press(button=self)



class ZenMotionSensor:
    protocol: ZenProtocol
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

    def __new__(cls, protocol: ZenProtocol, instance: ZenInstance) -> ZenMotionSensor:
        # Unique per protocol + controller + address + instance
        compound_id = f"{instance.address.controller.name} {instance.address.number} {instance.number}"
        registry = protocol.entity_registry.motion_sensors
        if compound_id not in registry:
            inst = super().__new__(cls)
            registry[compound_id] = inst
            inst.protocol = protocol
            inst.instance = instance
            inst._reset()
            # Don't call interview() here - it will be called async later
        return registry[compound_id]

    def __init__(self, protocol: ZenProtocol, instance: ZenInstance) -> None:
        self.protocol = protocol
        self.instance = instance
    
    @classmethod
    async def create(cls, protocol: ZenProtocol, instance: ZenInstance) -> ZenMotionSensor:
        """Async factory method for ZenMotionSensor"""
        sensor = cls(protocol, instance)
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
            cast(ZenController, self.instance.address.controller).motion_sensors.add(self)
            return True
        except Exception:
            return False
    async def interview(self) -> bool:
        inst = self.instance
        addr = inst.address
        ctrl = cast(ZenController, addr.controller)
        occupancy_timers = await self.protocol.query_occupancy_instance_timers(inst)
        if occupancy_timers is not None:
            self.serial = await self.protocol.query_dali_serial(addr)
            self.label = await self.protocol.query_dali_device_label(addr, generic_if_none=True)
            self.instance_label = await self.protocol.query_dali_instance_label(inst, generic_if_none=True)
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
        occupancy_timers = await self.protocol.query_occupancy_instance_timers(inst)
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
    async def _event_received(self):
        # Capture old state before the setter updates it so we can fire the
        # callback with await instead of asyncio.create_task (fire-and-forget).
        was_occupied = self._occupied or False
        self.occupied = True
        if not was_occupied and callable(self.protocol.callbacks.motion_event):
            await self.protocol.callbacks.motion_event(sensor=self, occupied=True)
    @property
    def occupied(self) -> bool:
        if self.last_detect is None:
            return False
        seconds_since_last_motion = time.time() - self.last_detect
        within_hold_time = seconds_since_last_motion < self.hold_time
        # if occupied but a hold task isn't running, start one with the time remaining
        if within_hold_time and self.hold_expiry_task is None:
            seconds_until_hold_time_expires = self.hold_time - seconds_since_last_motion
            self.hold_expiry_task = self.protocol.track_task(self._timeout_after_delay(seconds_until_hold_time_expires))
        return within_hold_time
    async def _timeout_after_delay(self, delay: float):
        """Async method to handle motion sensor timeout"""
        await asyncio.sleep(delay)
        self._occupied = False
        self.last_detect = None
        self.hold_expiry_task = None
        # Trigger motion event callback
        if callable(self.protocol.callbacks.motion_event):
            await self.protocol.callbacks.motion_event(sensor=self, occupied=False)

    @occupied.setter 
    def occupied(self, new_value: bool):
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
            self.hold_expiry_task = self.protocol.track_task(self._timeout_after_delay(self.hold_time))
            self._occupied = True
        else:
            self._occupied = False
            self.last_detect = None
            # If we're going from True to False, trigger motion event callback.
            # This branch is only reached when occupied is set to False directly
            # (not via _timeout_after_delay which handles the callback itself).
            if old_value is True:
                cb = self.protocol.callbacks.motion_event
                if callable(cb):
                    self.protocol.track_task(cast(Coroutine[Any, Any, None], cb(sensor=self, occupied=False)))


class ZenSystemVariable:
    protocol: ZenProtocol
    controller: ZenController
    id: int
    label: str | None = None
    _value: int | None = None
    _future_value: int | None = None
    client_data: dict[str, Any] = {}

    def __new__(cls, protocol: ZenProtocol, controller: ZenController, id: int, value: int | None = None, label: str | None = None) -> ZenSystemVariable:
        # Unique per protocol + controller + id
        compound_id = f"{controller.name} {id}"
        registry = protocol.entity_registry.system_variables
        if compound_id not in registry:
            inst = super().__new__(cls)
            registry[compound_id] = inst
            inst.protocol = protocol
            inst.controller = controller
            inst.id = id
            inst._reset()
            inst._value = value
            inst.label = label
            # Don't call interview() here - it will be called async later
        return registry[compound_id]

    def __init__(self, protocol: ZenProtocol, controller: ZenController, id: int, value: int | None = None, label: str | None = None) -> None:
        self.protocol = protocol
        self.controller = controller
        self.id = id
        if value is not None:
            self._value = value
        if label is not None:
            self.label = label
    
    @classmethod
    async def create(cls, protocol: ZenProtocol, controller: ZenController, id: int, value: int | None = None, label: str | None = None) -> ZenSystemVariable:
        """Async factory method for ZenSystemVariable"""
        sysvar = cls(protocol, controller, id, value, label)
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
            self.label = await self.protocol.query_system_variable_name(ctrl, self.id)
        if self._value is None:
            self._value = await self.protocol.query_system_variable(ctrl, self.id)
        # Add to controller's set of system variables
        ctrl.sysvars.add(self)
        return True
    async def _event_received(self, new_value: int | None):
        changed = (new_value != self._value)
        by_me = (new_value == self._future_value)
        self._value = new_value
        self._future_value = None
        if changed:
            if callable(self.protocol.callbacks.system_variable_change):
                await self.protocol.callbacks.system_variable_change(system_variable=self,
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
            self._value = await self.protocol.query_system_variable(self.controller, self.id)
        return self._value

    async def refresh_state_from_controller(self) -> None:
        """Query the controller and update this system variable's runtime value."""
        new_value = await self.protocol.query_system_variable(self.controller, self.id)
        await self._event_received(new_value)
    
    async def set_value(self, new_value: int) -> None:
        """Set the value of the system variable"""
        self._future_value = new_value # If we get this value back as an event, we'll know it's from us
        await self.protocol.set_system_variable(self.controller, self.id, new_value)


# Callback type definitions (moved here after class definitions)
type CallbackOnConnect = Callable[[], Awaitable[None]]
type CallbackOnDisconnect = Callable[[], Awaitable[None]]
type CallbackProfileChange = Callable[[ZenProfile], Awaitable[None]]
type CallbackGroupChange = Callable[[ZenGroup, int], Awaitable[None]]
type CallbackLightChange = Callable[[ZenLight, int, ZenColour, int], Awaitable[None]]
type CallbackButtonPress = Callable[[ZenButton], Awaitable[None]]
type CallbackButtonLongPress = Callable[[ZenButton], Awaitable[None]]
type CallbackMotionEvent = Callable[[ZenMotionSensor, bool], Awaitable[None]]
type CallbackSystemVariableChange = Callable[[ZenSystemVariable, int, bool, bool], Awaitable[None]]
type CallbackControllerDiscovered = Callable[[DiscoveredController], Awaitable[None]]
