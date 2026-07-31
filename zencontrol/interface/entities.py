"""Interface-layer entity models and interview helpers."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Coroutine
from typing import Any, cast

from ..api import (
    ZenRgbColour,
    ZenTcColour,
    ZenXyColour,
    ZenAddress,
    ZenAddressType,
    ZenColour,
    ZenColourType,
    ZenInstance,
    colour_from_bytes,
)
from ..api import ZenController as SuperZenController
from ..api.commands import ZenCommandClient
from ..api.models import ControllerRef
from ..api.types import Const
from .context import EntityContext


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


async def _group_scene_labels(commands: ZenCommandClient, address: ZenAddress) -> list[str | None]:
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
    fans: set[ZenFan]
    blinds: set[ZenBlind]
    groups: set[ZenGroup]
    buttons: set[ZenButton]
    absolute_inputs: set[ZenAbsoluteInput]
    motion_sensors: set[ZenMotionSensor]
    sysvars: set[ZenSystemVariable]
    client_data: dict[str, Any]

    def __init__(
        self,
        ctx: EntityContext,
        id: int,
        name: str,
        label: str,
        host: str,
        port: int = 5108,
        mac: str | None = None,
        filtering: bool = False,
    ) -> None:
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
        self.connected = False
        self._reset()

    def __repr__(self) -> str:
        return f"ZenController<{self.name}>"
    def _reset(self) -> None:
        # label is set from config via EntityContext.controller() or interview()
        self.version = None
        self.profile = None
        self.profiles = set()
        self.lights = set()
        self.fans = set()
        self.blinds = set()
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
            self.profile = self.ctx.profile(self, current_profile)
        self.connected = True
        return True
    async def _event_received(self, profile: int | None = None) -> None:
        if profile is not None:
            self.profile = self.ctx.profile(self, profile)
            cb = self.ctx.callbacks.profile_change
            if callable(cb):
                await cb(profile=self.profile)
    def get_sysvar(self, id: int) -> ZenSystemVariable:
        return self.ctx.system_variable(self, id)
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
    """Narrow address.controller to the interface subclass.

    Addresses are typed with ControllerRef so api does not import this
    layer; every registered controller is a ZenController instance.
    """
    return cast(ZenController, controller)


class ZenProfile:
    ctx: EntityContext
    commands: ZenCommandClient
    controller: ZenController
    number: int
    label: str | None = None
    client_data: dict[str, Any]

    def __init__(self, ctx: EntityContext, controller: ZenController, number: int) -> None:
        self.ctx = ctx
        self.commands = ctx.commands
        self.controller = controller
        self.number = number
        self._reset()

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
        except Exception: # pylint: disable=broad-exception-caught
            return False
    async def interview(self) -> bool:
        if self.label is None:
            self.label = await self.commands.query_profile_label(self.controller, self.number)
        self.controller.profiles.add(self)
        return True
    async def select(self) -> bool:
        result = await self.commands.change_profile_number(self.controller, self.number)
        return bool(result)


class ZenControlGear:
    """Shared base for addressable DALI control gear (lights and groups).

    Holds runtime level/colour/scene state and the command helpers that drive
    the controller. Subclasses own interview/identity and override the
    notification and discoordination hooks used by _event_received.
    """

    ctx: EntityContext
    commands: ZenCommandClient
    address: ZenAddress
    label: str | None = None
    _scene_labels: list[str | None]
    _scene_levels: list[int | None]
    _scene_colours: list[ZenColour | None]
    level: int | None = None
    colour: ZenColour | None = None
    scene: int | None = None
    client_data: dict[str, Any]

    def _reset_gear_state(self) -> None:
        self.label = None
        self._scene_labels = [None] * Const.MAX_SCENE
        self._scene_levels = [None] * Const.MAX_SCENE
        self._scene_colours = [None] * Const.MAX_SCENE
        self.level = None
        self.colour = None
        self.scene = None
        self.client_data = {}

    def supports_colour(self, colour: ZenColourType | ZenColour) -> bool:
        return False

    def _should_query_colour(self) -> bool:
        return False

    async def refresh_state_from_controller(self) -> None:
        refreshed_level = await self.commands.dali_query_level(self.address)
        refreshed_colour = None
        refreshed_scene = None
        if await self.commands.dali_query_last_scene_is_current(self.address):
            refreshed_scene = await self.commands.dali_query_last_scene(self.address)
        if self._should_query_colour():
            refreshed_colour = await self.commands.query_dali_colour(self.address)

        # Mimic incoming events when the controller reports last scene / level / colour.
        if refreshed_level is not None:
            await self._event_received_level(refreshed_level)
        if refreshed_colour is not None:
            await self._event_received_colour(refreshed_colour)
        if refreshed_scene is not None:
            await self._event_received_scene(refreshed_scene, active=True)

    async def _event_received_level(self, level: int, cascaded_from: ZenGroup | None = None) -> None:
        if level == 255 or level == self.level:
            return
        self.level = level
        if self.scene is not None:
            self.scene = None
        await self._after_direct_change(level_changed=True, colour_changed=False, cascaded_from=cascaded_from)
        await self._notify_change()

    async def _event_received_colour(self, colour: ZenColour, cascaded_from: ZenGroup | None = None) -> None:
        if colour == self.colour:
            return
        self.colour = colour
        if self.scene is not None:
            self.scene = None
        await self._after_direct_change(level_changed=False, colour_changed=True, cascaded_from=cascaded_from)
        await self._notify_change()

    async def _event_received_scene(self, scene: int, active: bool, cascaded_from: ZenGroup | None = None) -> None:
        if active:
            self.scene = scene
            scene_level = self._scene_levels[scene]
            scene_colour = self._scene_colours[scene]
            if scene_level is not None and scene_level != self.level:
                self.level = scene_level
            if scene_colour is not None and scene_colour != self.colour:
                self.colour = scene_colour
            await self._after_scene_activated(cascaded_from=cascaded_from)
            await self._notify_change()
            return
        if self.scene is not None:
            self.scene = None
            await self._notify_change()

    async def _after_scene_activated(self, cascaded_from: ZenGroup | None = None) -> None:
        """Hook: light membership may discoordinate groups after a scene event."""

    async def _after_direct_change(self, *, level_changed: bool, colour_changed: bool, cascaded_from: ZenGroup | None = None) -> None:
        """Hook: light membership may discoordinate groups after level/colour events."""

    async def _notify_change(self) -> None:
        """Hook: subclass fires light_change / group_change."""

    # -----------------------------------------------------------------------------------------
    # REMINDER: None of the following methods should update the internal object state directly.
    #   These methods send commands to the controller. The controller sends events back.
    #   The events update the internal state.
    # -----------------------------------------------------------------------------------------
    async def on(self, fade: bool = True) -> bool | None:
        if not fade: await self.commands.dali_enable_dapc_sequence(self.address)
        return await self.commands.dali_go_to_last_active_level(self.address)
    async def off(self, fade: bool = True) -> bool | None:
        if fade: return await self.commands.dali_arc_level(self.address, 0)
        else: return await self.commands.dali_off(self.address)
    async def set_scene(self, scene: int|str|dict[str, Any], fade: bool = True) -> bool | None:
        if type(scene) is str:
            scene = next((i for i, s in enumerate(self._scene_labels) if s == scene), False)
        if type(scene) is int:
            if not fade: await self.commands.dali_enable_dapc_sequence(self.address)
            return await self.commands.dali_scene(self.address, scene)
        return False
    async def set(self, level: int = 255, colour: ZenColour | None = None, fade: bool = True) -> bool | None:
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
        return await self.commands.dali_on_step_up(self.address)
    async def dali_step_down_off(self) -> bool | None:
        return await self.commands.dali_step_down_off(self.address)
    async def dali_up(self) -> bool | None:
        return await self.commands.dali_up(self.address)
    async def dali_down(self) -> bool | None:
        return await self.commands.dali_down(self.address)
    async def dali_recall_max(self) -> bool | None:
        return await self.commands.dali_recall_max(self.address)
    async def dali_recall_min(self) -> bool | None:
        return await self.commands.dali_recall_min(self.address)
    async def dali_go_to_last_active_level(self) -> bool | None:
        return await self.commands.dali_go_to_last_active_level(self.address)
    async def dali_off(self) -> bool | None:
        return await self.commands.dali_off(self.address)
    async def dali_custom_fade(self, level: int, duration: int) -> bool | None:
        return await self.commands.dali_custom_fade(self.address, level, duration)
    async def dali_stop_fade(self) -> bool | None:
        return await self.commands.dali_stop_fade(self.address)
    async def dali_enable_dapc_sequence(self) -> bool | None:
        return await self.commands.dali_enable_dapc_sequence(self.address)
    async def dali_inhibit(self, inhibit: bool = True) -> bool | None:
        time_seconds = 65535 if inhibit else 0
        return await self.commands.dali_inhibit(self.address, time_seconds)


class ZenLight(ZenControlGear):
    sub_label: str | None = None
    serial: (int | str) | None = None
    ean: int | None = None
    cgtype: list[int]
    groups: set[ZenGroup]
    group_membership: list[ZenAddress]
    features: dict[str, bool]
    properties: dict[str, int | None]

    def __init__(self, ctx: EntityContext, address: ZenAddress) -> None:
        self.ctx = ctx
        self.commands = ctx.commands
        self.address = address
        self._reset()

    def __repr__(self) -> str:
        return f"ZenLight<{self.address.controller.name} ecg {self.address.number}: {self.label}>"
    def _reset(self) -> None:
        self._reset_gear_state()
        self.sub_label = None
        self.serial = None
        self.ean = None
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

    def _apply_group_membership(self, membership: list[ZenAddress]) -> None:
        for existing_group in self.groups:
            existing_group.lights.discard(self)
        self.groups.clear()
        self.group_membership = list(membership)
        for group_address in self.group_membership:
            group = self.ctx.group(group_address)
            group.lights.add(self)
            self.groups.add(group)
    def interview_serialize(self) -> str:
        return json.dumps({
            "label": self.label,
            "sub_label": self.sub_label,
            "serial": self.serial,
            "ean": self.ean,
            "cgtype": list(self.cgtype),
            "group_membership": [_serialize_group_address(group) for group in self.group_membership],
            "features": dict(self.features),
            "properties": dict(self.properties),
            "scene_levels": list(self._scene_levels),
            "scene_colours": [
                list(colour.to_bytes()) if colour is not None else None
                for colour in self._scene_colours
            ],
        })
    def interview_hydrate(self, data: str | dict[str, Any]) -> bool:
        try:
            data = _loads_interview_data(data)
            self.label = data.get("label")
            self.sub_label = data.get("sub_label")
            self.serial = data.get("serial")
            self.ean = data.get("ean")
            self.cgtype = list(data.get("cgtype", []))
            self.features.update(data.get("features", {}))
            self.properties.update(data.get("properties", {}))
            self._scene_levels = list(data.get("scene_levels", []))
            self._scene_colours = [
                colour_from_bytes(bytes(raw)) if raw is not None else None
                for raw in data.get("scene_colours", [])
            ]
            membership = [
                ZenAddress(controller=self.address.controller, type=ZenAddressType.GROUP, number=group["number"])
                for group in data.get("group_membership", [])
            ]
            self._apply_group_membership(membership)
            _registered(self.address.controller).lights.add(self)
            return True
        except Exception: # pylint: disable=broad-exception-caught
            return False
    async def interview(self) -> bool:
        cgstatus = await self.commands.dali_query_control_gear_status(self.address)
        if cgstatus:
            if self.label is None:
                self.label = _or_device_label(await self.commands.query_dali_device_label(self.address), self.address)
            if self.serial is None:
                self.serial = await self.commands.query_dali_serial(self.address)
            if self.ean is None:
                self.ean = await self.commands.query_dali_ean(self.address)
            self.cgtype = await self.commands.dali_query_cg_type(self.address) or []
            
            # If cgtype contains 6, it supports brightness
            if 6 in self.cgtype:
                self.features["brightness"] = True

            # If cgtype contains 8, it supports some kind of colour
            colour_known = any(self.features.get(k) for k in ("XY", "temperature", "RGB", "RGBW", "RGBWW"))
            if 8 in self.cgtype and not colour_known:
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

    def _should_query_colour(self) -> bool:
        return bool(
            self.features.get("temperature")
            or self.features.get("RGB")
            or self.features.get("RGBW")
            or self.features.get("RGBWW")
            or self.features.get("XY")
        )

    def supports_colour(self, colour: ZenColourType | ZenColour) -> bool:
        match colour:
            case ZenTcColour() | ZenColourType.TC:
                return bool(self.features.get("temperature"))
            case ZenRgbColour() | ZenColourType.RGBWAF:
                return bool(
                    self.features.get("RGB")
                    or self.features.get("RGBW")
                    or self.features.get("RGBWW")
                )
            case ZenXyColour() | ZenColourType.XY:
                return bool(self.features.get("XY"))
            case _:
                return False

    async def _after_scene_activated(self, cascaded_from: ZenGroup | None = None) -> None:
        for group in self.groups:
            if group.scene != self.scene:
                await group.declare_discoordination()

    async def _after_direct_change(self, *, level_changed: bool, colour_changed: bool, cascaded_from: ZenGroup | None = None) -> None:
        for group in self.groups:
            if (level_changed and group.level != self.level) or (
                colour_changed and self.colour is not None and group.colour != self.colour
            ):
                await group.declare_discoordination()

    async def _notify_change(self) -> None:
        if callable(self.ctx.callbacks.light_change):
            await self.ctx.callbacks.light_change(light=self)


class ZenFan(ZenControlGear):
    kind = "fan"
    # Off + mid-band command arcs for speeds 1-3 + full for speed 4.
    _SPEED_ARCS: tuple[int, ...] = (0, 32, 95, 159, 254)
    serial: (int | str) | None = None
    ean: int | None = None
    bus_unit: int | None = None
    operating_mode: int | None = None
    cgtype: list[int]
    groups: set[ZenGroup]
    group_membership: list[ZenAddress]

    def __init__(self, ctx: EntityContext, address: ZenAddress) -> None:
        self.ctx = ctx
        self.commands = ctx.commands
        self.address = address
        self._reset()

    def __repr__(self) -> str:
        return f"ZenFan<{self.address.controller.name} ecg {self.address.number}: {self.label}>"

    def _reset(self) -> None:
        self._reset_gear_state()
        self.serial = None
        self.ean = None
        self.bus_unit = None
        self.operating_mode = None
        self.cgtype = []
        self.groups = set()
        self.group_membership = []

    def _apply_group_membership(self, membership: list[ZenAddress]) -> None:
        for existing_group in self.groups:
            existing_group.fans.discard(self)
        self.groups.clear()
        self.group_membership = list(membership)
        for group_address in self.group_membership:
            group = self.ctx.group(group_address)
            group.fans.add(self)
            self.groups.add(group)

    def interview_serialize(self) -> str:
        return json.dumps({
            "kind": self.kind,
            "label": self.label,
            "serial": self.serial,
            "ean": self.ean,
            "bus_unit": self.bus_unit,
            "operating_mode": self.operating_mode,
            "cgtype": list(self.cgtype),
            "group_membership": [_serialize_group_address(group) for group in self.group_membership],
            "scene_levels": list(self._scene_levels),
        })

    def interview_hydrate(self, data: str | dict[str, Any]) -> bool:
        try:
            data = _loads_interview_data(data)
            self.label = data.get("label")
            self.serial = data.get("serial")
            self.ean = data.get("ean")
            self.bus_unit = data.get("bus_unit")
            self.operating_mode = data.get("operating_mode")
            self.cgtype = list(data.get("cgtype", []))
            self._scene_levels = list(data.get("scene_levels", [None] * Const.MAX_SCENE))
            if len(self._scene_levels) < Const.MAX_SCENE:
                self._scene_levels.extend([None] * (Const.MAX_SCENE - len(self._scene_levels)))
            membership = [
                ZenAddress(controller=self.address.controller, type=ZenAddressType.GROUP, number=group["number"])
                for group in data.get("group_membership", [])
            ]
            self._apply_group_membership(membership)
            _registered(self.address.controller).fans.add(self)
            return True
        except Exception:  # pylint: disable=broad-exception-caught
            return False

    async def interview(self) -> bool:
        cgstatus = await self.commands.dali_query_control_gear_status(self.address)
        if cgstatus:
            if self.label is None:
                self.label = _or_device_label(await self.commands.query_dali_device_label(self.address), self.address)
            if self.serial is None:
                self.serial = await self.commands.query_dali_serial(self.address)
            if self.ean is None:
                self.ean = await self.commands.query_dali_ean(self.address)
            if self.operating_mode is None:
                self.operating_mode = await self.commands.query_operating_mode_by_address(self.address)
            self.cgtype = await self.commands.dali_query_cg_type(self.address) or []
            self._scene_levels = await self.commands.query_scene_levels_by_address(self.address) or [None] * Const.MAX_SCENE
            groups = await self.commands.query_group_membership_by_address(self.address)
            self._apply_group_membership(groups or [])
            _registered(self.address.controller).fans.add(self)
            return True
        self._reset()
        return False

    @staticmethod
    def speed_from_arc(arc: int | None) -> int:
        """Map arc level to speed 0-4 using zencontrol default bands."""
        if arc is None or arc <= 0:
            return 0
        if arc <= 63:
            return 1
        if arc <= 127:
            return 2
        if arc <= 191:
            return 3
        return 4

    @staticmethod
    def arc_for_speed(speed: int) -> int:
        """Command arc for speed 0-4."""
        if not 0 <= speed <= 4:
            raise ValueError(f"Fan speed must be 0-4, got {speed}")
        return ZenFan._SPEED_ARCS[speed]

    @property
    def speed(self) -> int:
        return self.speed_from_arc(self.level)

    async def set_speed(self, speed: int, fade: bool = True) -> bool | None:
        return await self.set(level=self.arc_for_speed(speed), fade=fade)

    async def _notify_change(self) -> None:
        if callable(self.ctx.callbacks.fan_change):
            await self.ctx.callbacks.fan_change(fan=self)


class ZenBlind(ZenControlGear):
    kind = "blind"
    serial: (int | str) | None = None
    ean: int | None = None
    bus_unit: int | None = None
    operating_mode: int | None = None
    cgtype: list[int]
    groups: set[ZenGroup]
    group_membership: list[ZenAddress]

    def __init__(self, ctx: EntityContext, address: ZenAddress) -> None:
        self.ctx = ctx
        self.commands = ctx.commands
        self.address = address
        self._reset()

    def __repr__(self) -> str:
        return f"ZenBlind<{self.address.controller.name} ecg {self.address.number}: {self.label}>"

    def _reset(self) -> None:
        self._reset_gear_state()
        self.serial = None
        self.ean = None
        self.bus_unit = None
        self.operating_mode = None
        self.cgtype = []
        self.groups = set()
        self.group_membership = []

    def _apply_group_membership(self, membership: list[ZenAddress]) -> None:
        for existing_group in self.groups:
            existing_group.blinds.discard(self)
        self.groups.clear()
        self.group_membership = list(membership)
        for group_address in self.group_membership:
            group = self.ctx.group(group_address)
            group.blinds.add(self)
            self.groups.add(group)

    def interview_serialize(self) -> str:
        return json.dumps({
            "kind": self.kind,
            "label": self.label,
            "serial": self.serial,
            "ean": self.ean,
            "bus_unit": self.bus_unit,
            "operating_mode": self.operating_mode,
            "cgtype": list(self.cgtype),
            "group_membership": [_serialize_group_address(group) for group in self.group_membership],
            "scene_levels": list(self._scene_levels),
        })

    def interview_hydrate(self, data: str | dict[str, Any]) -> bool:
        try:
            data = _loads_interview_data(data)
            self.label = data.get("label")
            self.serial = data.get("serial")
            self.ean = data.get("ean")
            self.bus_unit = data.get("bus_unit")
            self.operating_mode = data.get("operating_mode")
            self.cgtype = list(data.get("cgtype", []))
            self._scene_levels = list(data.get("scene_levels", [None] * Const.MAX_SCENE))
            if len(self._scene_levels) < Const.MAX_SCENE:
                self._scene_levels.extend([None] * (Const.MAX_SCENE - len(self._scene_levels)))
            membership = [
                ZenAddress(controller=self.address.controller, type=ZenAddressType.GROUP, number=group["number"])
                for group in data.get("group_membership", [])
            ]
            self._apply_group_membership(membership)
            _registered(self.address.controller).blinds.add(self)
            return True
        except Exception:  # pylint: disable=broad-exception-caught
            return False

    async def interview(self) -> bool:
        cgstatus = await self.commands.dali_query_control_gear_status(self.address)
        if cgstatus:
            if self.label is None:
                self.label = _or_device_label(await self.commands.query_dali_device_label(self.address), self.address)
            if self.serial is None:
                self.serial = await self.commands.query_dali_serial(self.address)
            if self.ean is None:
                self.ean = await self.commands.query_dali_ean(self.address)
            if self.operating_mode is None:
                self.operating_mode = await self.commands.query_operating_mode_by_address(self.address)
            self.cgtype = await self.commands.dali_query_cg_type(self.address) or []
            self._scene_levels = await self.commands.query_scene_levels_by_address(self.address) or [None] * Const.MAX_SCENE
            groups = await self.commands.query_group_membership_by_address(self.address)
            self._apply_group_membership(groups or [])
            _registered(self.address.controller).blinds.add(self)
            return True
        self._reset()
        return False

    @staticmethod
    def position_from_arc(arc: int | None) -> int | None:
        """Linear 0-100 position; None if unknown (incl. MASK 255)."""
        if arc is None or arc == 255:
            return None
        if arc <= 0:
            return 0
        if arc >= 254:
            return 100
        return round(arc / 254 * 100)

    @staticmethod
    def arc_for_position(position: int) -> int:
        """Linear position 0-100 → arc 0-254."""
        if not 0 <= position <= 100:
            raise ValueError(f"Position must be 0-100, got {position}")
        if position <= 0:
            return 0
        if position >= 100:
            return 254
        return round(position / 100 * 254)

    @property
    def position(self) -> int | None:
        return self.position_from_arc(self.level)

    async def set_position(self, position: int, fade: bool = True) -> bool | None:
        return await self.set(level=self.arc_for_position(position), fade=fade)

    async def open(self, fade: bool = True) -> bool | None:
        return await self.set(level=Const.MAX_LEVEL, fade=fade)

    async def close(self, fade: bool = True) -> bool | None:
        return await self.off(fade=fade)

    async def stop(self) -> bool | None:
        return await self.dali_stop_fade()

    async def _event_received_level(self, level: int, cascaded_from: ZenGroup | None = None) -> None:
        # MASK 255 = unknown / mid-travel — still notify (lights ignore 255).
        if level == self.level:
            return
        self.level = level
        if self.scene is not None:
            self.scene = None
        await self._after_direct_change(level_changed=True, colour_changed=False, cascaded_from=cascaded_from)
        await self._notify_change()

    async def refresh_state_from_controller(self) -> None:
        refreshed_level = await self.commands.dali_query_level(self.address)
        refreshed_scene = None
        if await self.commands.dali_query_last_scene_is_current(self.address):
            refreshed_scene = await self.commands.dali_query_last_scene(self.address)
        # None from query is failure/MASK collapse — do not clear a known position.
        if refreshed_level is not None:
            await self._event_received_level(refreshed_level)
        if refreshed_scene is not None:
            await self._event_received_scene(refreshed_scene, active=True)

    async def _notify_change(self) -> None:
        if callable(self.ctx.callbacks.blind_change):
            await self.ctx.callbacks.blind_change(blind=self)


class ZenGroup(ZenControlGear):
    lights: set[ZenLight]
    fans: set[ZenFan]
    blinds: set[ZenBlind]

    def __init__(self, ctx: EntityContext, address: ZenAddress) -> None:
        self.ctx = ctx
        self.commands = ctx.commands
        self.address = address
        self.lights = set()
        self.fans = set()
        self.blinds = set()
        self._reset()

    def __repr__(self) -> str:
        return f"ZenGroup<{self.address.controller.name} group {self.address.number}: {self.label}>"

    def _reset(self) -> None:
        self._reset_gear_state()

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
        except Exception: # pylint: disable=broad-exception-caught
            return False
    async def interview(self) -> bool:
        if self.label is None:
            self.label = _or_group_label(await self.commands.query_group_label(self.address), self.address.number)
        if not any(self._scene_labels):
            self._scene_labels = await _group_scene_labels(self.commands, self.address)
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
        return self._scene_labels

    async def _notify_change(self) -> None:
        if callable(self.ctx.callbacks.group_change):
            await self.ctx.callbacks.group_change(group=self)

    async def declare_discoordination(self) -> None:
        # Only do something if the group claims to be coordinated
        if self.level is None and self.colour is None and self.scene is None:
            return
        # This is called when members of the group are no longer in a uniform state
        self.level = None
        self.colour = None
        self.scene = None
        if callable(self.ctx.callbacks.group_change):
            await self.ctx.callbacks.group_change(group=self, discoordinated=True)

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
    ean: int | None = None
    label: str | None = None
    instance_label: str | None = None
    last_press_time: float = 0.0
    long_press_count: int = 0
    client_data: dict[str, Any]

    def __init__(self, ctx: EntityContext, instance: ZenInstance) -> None:
        self.ctx = ctx
        self.commands = ctx.commands
        self.instance = instance
        self._reset()

    def __repr__(self) -> str:
        return f"ZenButton<{self.instance.address.controller.name} ecd {self.instance.address.number} inst {self.instance.number}: {self.label} / {self.instance_label}>"
    def _reset(self) -> None:
        self.serial = None
        self.ean = None
        self.label = None
        self.instance_label = None
        self.last_press_time = time.time()
        self.long_press_count = 0
        self.client_data = {}
    def interview_serialize(self) -> str:
        return json.dumps({
            "serial": self.serial,
            "ean": self.ean,
            "label": self.label,
            "instance_label": self.instance_label,
        })
    def interview_hydrate(self, data: str | dict[str, Any]) -> bool:
        try:
            data = _loads_interview_data(data)
            self.serial = data.get("serial")
            self.ean = data.get("ean")
            self.label = data.get("label")
            self.instance_label = data.get("instance_label")
            self.instance.address.label = self.label
            self.instance.address.serial = cast(str | None, self.serial)
            self.instance.address.ean = self.ean
            _registered(self.instance.address.controller).buttons.add(self)
            return True
        except Exception: # pylint: disable=broad-exception-caught
            return False
    async def interview(self) -> bool:
        inst = self.instance
        addr = inst.address
        ctrl = _registered(addr.controller)
        if addr.label is None:
            addr.label = _or_device_label(await self.commands.query_dali_device_label(addr), addr)
        if addr.serial is None:
            addr.serial = cast(str | None, await self.commands.query_dali_serial(addr))
        if addr.ean is None:
            addr.ean = await self.commands.query_dali_ean(addr)
        self.label = addr.label
        self.serial = addr.serial
        self.ean = addr.ean
        if self.instance_label is None:
            self.instance_label = _or_instance_label(await self.commands.query_dali_instance_label(inst), inst)
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
    the current value, so value stays None until the first event.
    Payload matches _protocol.txt: [instance, value_hi, value_lo].
    """

    ctx: EntityContext
    commands: ZenCommandClient
    instance: ZenInstance
    serial: (int | str) | None = None
    ean: int | None = None
    label: str | None = None
    instance_label: str | None = None
    _value: int | None = None
    client_data: dict[str, Any]

    def __init__(self, ctx: EntityContext, instance: ZenInstance) -> None:
        self.ctx = ctx
        self.commands = ctx.commands
        self.instance = instance
        self._reset()

    def __repr__(self) -> str:
        return (
            f"ZenAbsoluteInput<{self.instance.address.controller.name} "
            f"ecd {self.instance.address.number} inst {self.instance.number}: "
            f"{self.label} / {self.instance_label}>"
        )

    def _reset(self) -> None:
        self.serial = None
        self.ean = None
        self.label = None
        self.instance_label = None
        self._value = None
        self.client_data = {}

    def interview_serialize(self) -> str:
        return json.dumps({
            "serial": self.serial,
            "ean": self.ean,
            "label": self.label,
            "instance_label": self.instance_label,
        })

    def interview_hydrate(self, data: str | dict[str, Any]) -> bool:
        try:
            data = _loads_interview_data(data)
            self.serial = data.get("serial")
            self.ean = data.get("ean")
            self.label = data.get("label")
            self.instance_label = data.get("instance_label")
            self.instance.address.label = self.label
            self.instance.address.serial = cast(str | None, self.serial)
            self.instance.address.ean = self.ean
            _registered(self.instance.address.controller).absolute_inputs.add(self)
            return True
        except Exception: # pylint: disable=broad-exception-caught
            return False

    async def interview(self) -> bool:
        inst = self.instance
        addr = inst.address
        ctrl = _registered(addr.controller)
        if addr.label is None:
            addr.label = _or_device_label(await self.commands.query_dali_device_label(addr), addr)
        if addr.serial is None:
            addr.serial = cast(str | None, await self.commands.query_dali_serial(addr))
        if addr.ean is None:
            addr.ean = await self.commands.query_dali_ean(addr)
        self.label = addr.label
        self.serial = addr.serial
        self.ean = addr.ean
        if self.instance_label is None:
            self.instance_label = _or_instance_label(await self.commands.query_dali_instance_label(inst), inst)
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
            await self.ctx.callbacks.absolute_input_change(absolute_input=self)


class ZenMotionSensor:
    ctx: EntityContext
    commands: ZenCommandClient
    instance: ZenInstance
    hold_time: int = Const.DEFAULT_HOLD_TIME
    hold_expiry_task: asyncio.Task[None] | None = None
    serial: (int | str) | None = None
    ean: int | None = None
    label: str | None = None
    instance_label: str | None = None
    deadtime: int | None = None
    last_detect: float | None = None
    _occupied: bool | None = None
    client_data: dict[str, Any]

    def __init__(self, ctx: EntityContext, instance: ZenInstance) -> None:
        self.ctx = ctx
        self.commands = ctx.commands
        self.instance = instance
        self._reset()

    def __repr__(self) -> str:
        return f"ZenMotionSensor<{self.instance.address.controller.name} ecd {self.instance.address.number} inst {self.instance.number}: {self.label} / {self.instance_label}>"
    def _reset(self) -> None:
        self.hold_time = Const.DEFAULT_HOLD_TIME
        self.hold_expiry_task = None
        #
        self.serial = None
        self.ean = None
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
            "ean": self.ean,
            "label": self.label,
            "instance_label": self.instance_label,
            "deadtime": self.deadtime,
            "hold_time": self.hold_time,
        })
    def interview_hydrate(self, data: str | dict[str, Any]) -> bool:
        try:
            data = _loads_interview_data(data)
            self.serial = data.get("serial")
            self.ean = data.get("ean")
            self.label = data.get("label")
            self.instance_label = data.get("instance_label")
            self.deadtime = data.get("deadtime")
            self.hold_time = data.get("hold_time", Const.DEFAULT_HOLD_TIME)
            self._occupied = None
            self.instance.address.label = self.label
            self.instance.address.serial = cast(str | None, self.serial)
            self.instance.address.ean = self.ean
            _registered(self.instance.address.controller).motion_sensors.add(self)
            return True
        except Exception: # pylint: disable=broad-exception-caught
            return False
    async def interview(self) -> bool:
        inst = self.instance
        addr = inst.address
        ctrl = _registered(addr.controller)
        occupancy_timers = await self.commands.query_occupancy_instance_timers(inst)
        if occupancy_timers is not None:
            if addr.serial is None:
                addr.serial = cast(str | None, await self.commands.query_dali_serial(addr))
            if addr.ean is None:
                addr.ean = await self.commands.query_dali_ean(addr)
            if addr.label is None:
                addr.label = _or_device_label(await self.commands.query_dali_device_label(addr), addr)
            self.serial = addr.serial
            self.ean = addr.ean
            self.label = addr.label
            if self.instance_label is None:
                self.instance_label = _or_instance_label(await self.commands.query_dali_instance_label(inst), inst)
            self.deadtime = occupancy_timers["deadtime"]
            self.hold_time = occupancy_timers["hold"]
            self.last_detect = time.time() - occupancy_timers["last_detect"]
            self._occupied = None
        else:
            self._reset()
            return False
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
                    self.ctx.track_task(cast(Coroutine[Any, Any, None], cb(sensor=self)))

    async def _timeout_after_delay(self, delay: float) -> None:
        """Async method to handle motion sensor timeout"""
        await asyncio.sleep(delay)
        self._occupied = False
        self.last_detect = None
        self.hold_expiry_task = None
        # Trigger motion event callback
        if callable(self.ctx.callbacks.motion_event):
            await self.ctx.callbacks.motion_event(sensor=self)

    async def _event_received(self) -> None:
        # Capture old state before the setter updates it so we can fire the
        # callback with await instead of asyncio.create_task (fire-and-forget).
        was_occupied = self._occupied or False
        self.occupied = True
        if not was_occupied and callable(self.ctx.callbacks.motion_event):
            await self.ctx.callbacks.motion_event(sensor=self)


class ZenSystemVariable:
    ctx: EntityContext
    commands: ZenCommandClient
    controller: ZenController
    id: int
    label: str | None = None
    _value: int | None = None
    _future_value: int | None = None
    client_data: dict[str, Any]

    def __init__(
        self,
        ctx: EntityContext,
        controller: ZenController,
        id: int,
        value: int | None = None,
        label: str | None = None,
    ) -> None:
        self.ctx = ctx
        self.commands = ctx.commands
        self.controller = controller
        self.id = id
        self._reset()
        self._value = value
        self.label = label

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
        except Exception: # pylint: disable=broad-exception-caught
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
        changed = new_value != self._value
        by_me = new_value == self._future_value
        self._value = new_value
        self._future_value = None
        if changed:
            if callable(self.ctx.callbacks.system_variable_change):
                await self.ctx.callbacks.system_variable_change(
                    system_variable=self, by_me=by_me
                )

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

