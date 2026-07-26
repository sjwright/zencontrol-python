"""Interface-layer entity models and interview helpers."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Coroutine
from typing import Any, Self, cast

from ..api import (
    ZenAddress,
    ZenAddressType,
    ZenColour,
    ZenColourType,
    ZenInstance,
    ZenInstanceType,
)
from ..api import ZenController as SuperZenController
from ..api.commands import ZenCommandClient
from ..api.models import ControllerRef, mac_to_bytes
from ..api.types import Const
from .context import EntityContext


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
    profiles: set[ZenProfile]
    lights: set[ZenLight]
    groups: set[ZenGroup]
    buttons: set[ZenButton]
    absolute_inputs: set[ZenAbsoluteInput]
    motion_sensors: set[ZenMotionSensor]
    sysvars: set[ZenSystemVariable]
    client_data: dict[str, Any]

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
        commands = self.commands
        if self.label is None or self.label == "":
            queried = await commands.query_controller_label(self)
            if queried is not None:
                self.label = queried
        self.version = await commands.query_controller_version_number(self)
        current_profile = await commands.query_current_profile_number(self)
        if current_profile is not None:
            self.profile = ZenProfile(ctx=self.ctx, controller=self, number=current_profile)
        self.connected = True
        return True
    async def _event_received(self, profile: int | None = None) -> None:
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
    client_data: dict[str, Any]

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
    cgtype: list[int]
    groups: set[ZenGroup]
    group_membership: list[ZenAddress]
    features: dict[str, bool]
    properties: dict[str, int | None]
    _scene_labels: list[str | None]
    _scene_levels: list[int | None]
    _scene_colours: list[ZenColour | None]
    level: int | None = None
    colour: ZenColour | None = None
    scene: int | None = None
    client_data: dict[str, Any]
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
    lights: set[ZenLight]

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
    client_data: dict[str, Any]

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
    client_data: dict[str, Any]

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
    client_data: dict[str, Any]

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
    client_data: dict[str, Any]

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

