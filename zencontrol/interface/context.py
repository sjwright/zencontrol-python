"""Entity-layer context: callbacks, registry, and fire-and-forget tasks.

Keeps ZenCommandClient as a pure TPI/UDP command plane. High-level entity
identity and application callbacks live here, owned by ZenControl.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Coroutine
from typing import TYPE_CHECKING, Any, Literal, Protocol

from ..api.commands import ZenCommandClient
from ..api.models import DiscoveredController, ZenAddress, ZenInstance, mac_to_bytes

if TYPE_CHECKING:
    from .entities import (
        ZenAbsoluteInput,
        ZenBlind,
        ZenButton,
        ZenController,
        ZenFan,
        ZenGroup,
        ZenLight,
        ZenMotionSensor,
        ZenProfile,
        ZenSystemVariable,
    )

ControllerRuntimeStatus = Literal["online", "starting", "unreachable"]


class OnConnectHandler(Protocol):
    def __call__(self) -> Awaitable[None]: ...


class OnDisconnectHandler(Protocol):
    def __call__(self) -> Awaitable[None]: ...


class OnResyncHandler(Protocol):
    def __call__(self) -> Awaitable[None]: ...


class ProfileChangeHandler(Protocol):
    def __call__(self, *, profile: ZenProfile) -> Awaitable[None]: ...


class GroupChangeHandler(Protocol):
    def __call__(self, *, group: ZenGroup, discoordinated: bool = False) -> Awaitable[None]: ...


class LightChangeHandler(Protocol):
    def __call__(self, *, light: ZenLight) -> Awaitable[None]: ...


class FanChangeHandler(Protocol):
    def __call__(self, *, fan: ZenFan) -> Awaitable[None]: ...


class BlindChangeHandler(Protocol):
    def __call__(self, *, blind: ZenBlind) -> Awaitable[None]: ...


class ButtonPressHandler(Protocol):
    def __call__(self, *, button: ZenButton) -> Awaitable[None]: ...


class AbsoluteInputChangeHandler(Protocol):
    def __call__(self, *, absolute_input: ZenAbsoluteInput) -> Awaitable[None]: ...


class MotionEventHandler(Protocol):
    def __call__(self, *, sensor: ZenMotionSensor) -> Awaitable[None]: ...


class SystemVariableChangeHandler(Protocol):
    def __call__(self, *, system_variable: ZenSystemVariable, by_me: bool = False) -> Awaitable[None]: ...


class ControllerDiscoveredHandler(Protocol):
    def __call__(self, discovered: DiscoveredController) -> Awaitable[None]: ...


class ControllerIdentifiedHandler(Protocol):
    def __call__(self, ctrl: ZenController, mac: str) -> Awaitable[None]: ...


class ControllerStatusChangeHandler(Protocol):
    def __call__(self, ctrl: ZenController, status: ControllerRuntimeStatus) -> Awaitable[None]: ...


class ZenCallbacks:
    """Per-ZenControl high-level callback registry.

    Stored on EntityContext.callbacks so entity singletons reach their
    owning integration's callbacks via self.ctx.callbacks.
    """

    def __init__(self) -> None:
        self.on_connect: OnConnectHandler | None = None
        self.on_disconnect: OnDisconnectHandler | None = None
        # Session gap after receiver restore (not a wire event).
        self.on_resync: OnResyncHandler | None = None
        self.profile_change: ProfileChangeHandler | None = None
        self.group_change: GroupChangeHandler | None = None
        self.light_change: LightChangeHandler | None = None
        self.fan_change: FanChangeHandler | None = None
        self.blind_change: BlindChangeHandler | None = None
        self.button_press: ButtonPressHandler | None = None
        self.button_long_press: ButtonPressHandler | None = None
        self.absolute_input_change: AbsoluteInputChangeHandler | None = None
        self.motion_event: MotionEventHandler | None = None
        self.system_variable_change: SystemVariableChangeHandler | None = None
        self.controller_discovered: ControllerDiscoveredHandler | None = None
        # Fired once when a provisional binding learns its MAC (persist for HA).
        self.controller_identified: ControllerIdentifiedHandler | None = None
        # online / starting / unreachable (keepalive / binding loss).
        self.controller_status_change: ControllerStatusChangeHandler | None = None


class EntityRegistry:
    """Per-context caches for interface-layer entity identity.

    Entities keyed here are unique within one EntityContext / ZenControl
    instance, not process-wide. Non-controller keys are tuples whose first
    element is always the controller name.
    """

    def __init__(self) -> None:
        self.controllers: dict[str, ZenController] = {}
        self.profiles: dict[tuple[str, int], ZenProfile] = {}
        self.lights: dict[tuple[str, int], ZenLight] = {}
        self.fans: dict[tuple[str, int], ZenFan] = {}
        self.blinds: dict[tuple[str, int], ZenBlind] = {}
        self.groups: dict[tuple[str, int], ZenGroup] = {}
        self.buttons: dict[tuple[str, int, int], ZenButton] = {}
        self.absolute_inputs: dict[tuple[str, int, int], ZenAbsoluteInput] = {}
        self.motion_sensors: dict[tuple[str, int, int], ZenMotionSensor] = {}
        self.system_variables: dict[tuple[str, int], ZenSystemVariable] = {}

    def clear(self) -> None:
        self.controllers.clear()
        self.profiles.clear()
        self.lights.clear()
        self.fans.clear()
        self.blinds.clear()
        self.groups.clear()
        self.buttons.clear()
        self.absolute_inputs.clear()
        self.motion_sensors.clear()
        self.system_variables.clear()

    def purge_controller(self, controller_name: str) -> None:
        """Drop cached entities that belong to controller_name."""
        self.controllers.pop(controller_name, None)
        for profile_key in [k for k in self.profiles if k[0] == controller_name]:
            del self.profiles[profile_key]
        for light_key in [k for k in self.lights if k[0] == controller_name]:
            del self.lights[light_key]
        for fan_key in [k for k in self.fans if k[0] == controller_name]:
            del self.fans[fan_key]
        for blind_key in [k for k in self.blinds if k[0] == controller_name]:
            del self.blinds[blind_key]
        for group_key in [k for k in self.groups if k[0] == controller_name]:
            del self.groups[group_key]
        for button_key in [k for k in self.buttons if k[0] == controller_name]:
            del self.buttons[button_key]
        for absolute_key in [k for k in self.absolute_inputs if k[0] == controller_name]:
            del self.absolute_inputs[absolute_key]
        for motion_key in [k for k in self.motion_sensors if k[0] == controller_name]:
            del self.motion_sensors[motion_key]
        for sysvar_key in [k for k in self.system_variables if k[0] == controller_name]:
            del self.system_variables[sysvar_key]


class EntityContext:
    """Owns entity callbacks, identity registry, and deferred interface tasks.

    Advanced/command-only surface. Prefer ZenControl for applications that
    need event monitoring, discovery, or session lifecycle - it creates and
    owns an EntityContext. Use this directly only when you drive
    ZenCommandClient yourself without the event session.

    Entity identity is obtained via factory methods (light, group, …);
    construct entities only through those (or the async create_* wrappers).
    """

    def __init__(self, commands: ZenCommandClient, logger: logging.Logger | None = None) -> None:
        self.commands = commands
        self.logger = logger or commands.logger
        self.callbacks = ZenCallbacks()
        self.registry = EntityRegistry()
        self._background_tasks: set[asyncio.Task[Any]] = set()

    def clear_entity_caches(self) -> None:
        """Drop all interface entity singletons owned by this context."""
        self.registry.clear()

    def purge_controller_entities(self, controller_name: str) -> None:
        """Drop interface-layer singletons for one controller."""
        self.registry.purge_controller(controller_name)

    # ----- identity factories (hit-path: A1/B1/C1/D1) -----

    def ctrl(
        self,
        id: int,
        name: str,
        label: str,
        host: str,
        port: int = 5108,
        mac: str | None = None,
        filtering: bool = False,
    ) -> ZenController:
        from .entities import ZenController

        store = self.registry.controllers
        if name not in store:
            store[name] = ZenController(
                self,
                id=id,
                name=name,
                label=label,
                host=host,
                port=port,
                mac=mac,
                filtering=filtering,
            )
            return store[name]

        ctrl = store[name]
        ctrl.ctx = self
        ctrl.id = str(id)
        ctrl.name = name
        ctrl.label = label
        ctrl.host = host
        ctrl.port = port
        ctrl.mac = mac
        ctrl.filtering = filtering
        mac_to_bytes(mac)  # eager validate on config refresh
        return ctrl

    def profile(self, ctrl: ZenController, number: int) -> ZenProfile:
        from .entities import ZenProfile

        key = (ctrl.name, number)
        store = self.registry.profiles
        if key not in store:
            store[key] = ZenProfile(self, ctrl, number)
        return store[key]

    def light(self, address: ZenAddress) -> ZenLight:
        from .entities import ZenLight

        key = (address.ctrl.name, address.number)
        self.registry.fans.pop(key, None)
        self.registry.blinds.pop(key, None)
        store = self.registry.lights
        if key not in store:
            store[key] = ZenLight(self, address)
        return store[key]

    def fan(self, address: ZenAddress) -> ZenFan:
        from .entities import ZenFan

        key = (address.ctrl.name, address.number)
        self.registry.lights.pop(key, None)
        self.registry.blinds.pop(key, None)
        store = self.registry.fans
        if key not in store:
            store[key] = ZenFan(self, address)
        return store[key]

    def blind(self, address: ZenAddress) -> ZenBlind:
        from .entities import ZenBlind

        key = (address.ctrl.name, address.number)
        self.registry.lights.pop(key, None)
        self.registry.fans.pop(key, None)
        store = self.registry.blinds
        if key not in store:
            store[key] = ZenBlind(self, address)
        return store[key]

    def ecg_lookup(self, address: ZenAddress) -> ZenLight | ZenFan | ZenBlind | None:
        """Lookup-only across light/fan/blind registries (no lazy create)."""
        key = (address.ctrl.name, address.number)
        if key in self.registry.lights:
            return self.registry.lights[key]
        if key in self.registry.fans:
            return self.registry.fans[key]
        if key in self.registry.blinds:
            return self.registry.blinds[key]
        return None

    def group(self, address: ZenAddress) -> ZenGroup:
        from .entities import ZenGroup

        key = (address.ctrl.name, address.number)
        store = self.registry.groups
        if key not in store:
            store[key] = ZenGroup(self, address)
        return store[key]

    def button(self, instance: ZenInstance) -> ZenButton:
        from .entities import ZenButton

        key = (instance.address.ctrl.name, instance.address.number, instance.number)
        store = self.registry.buttons
        if key not in store:
            store[key] = ZenButton(self, instance)
        return store[key]

    def absolute_input(self, instance: ZenInstance) -> ZenAbsoluteInput:
        from .entities import ZenAbsoluteInput

        key = (instance.address.ctrl.name, instance.address.number, instance.number)
        store = self.registry.absolute_inputs
        if key not in store:
            store[key] = ZenAbsoluteInput(self, instance)
        return store[key]

    def motion_sensor(self, instance: ZenInstance) -> ZenMotionSensor:
        from .entities import ZenMotionSensor

        key = (instance.address.ctrl.name, instance.address.number, instance.number)
        store = self.registry.motion_sensors
        if key not in store:
            store[key] = ZenMotionSensor(self, instance)
        return store[key]

    def system_variable(
        self,
        ctrl: ZenController,
        id: int,
        value: int | None = None,
        label: str | None = None,
    ) -> ZenSystemVariable:
        from .entities import ZenSystemVariable

        key = (ctrl.name, id)
        store = self.registry.system_variables
        if key not in store:
            store[key] = ZenSystemVariable(self, ctrl, id, value, label)
            return store[key]

        sv = store[key]
        if value is not None:
            sv._value = value
        if label is not None:
            sv.label = label
        return sv

    # ----- async create (factory + interview) -----

    async def create_controller(
        self,
        id: int,
        name: str,
        label: str,
        host: str,
        port: int = 5108,
        mac: str | None = None,
        filtering: bool = False,
    ) -> ZenController:
        ctrl = self.ctrl(
            id=id, name=name, label=label, host=host, port=port, mac=mac, filtering=filtering
        )
        await ctrl.interview()
        return ctrl

    async def create_profile(self, ctrl: ZenController, number: int) -> ZenProfile:
        profile = self.profile(ctrl, number)
        await profile.interview()
        return profile

    async def create_light(self, address: ZenAddress, *, label: str | None = None, ean: int | None = None) -> ZenLight:
        light = self.light(address)
        if label is not None: light.label = label
        if ean is not None: light.ean = ean
        await light.interview()
        return light

    async def create_fan(self, address: ZenAddress, *, label: str | None = None, ean: int | None = None) -> ZenFan:
        fan = self.fan(address)
        if label is not None: fan.label = label
        if ean is not None: fan.ean = ean
        await fan.interview()
        return fan

    async def create_blind(self, address: ZenAddress, *, label: str | None = None, ean: int | None = None) -> ZenBlind:
        blind = self.blind(address)
        if label is not None: blind.label = label
        if ean is not None: blind.ean = ean
        await blind.interview()
        return blind

    async def create_group(self, address: ZenAddress) -> ZenGroup:
        group = self.group(address)
        await group.interview()
        return group

    async def create_button(self, instance: ZenInstance) -> ZenButton:
        button = self.button(instance)
        await button.interview()
        return button

    async def create_absolute_input(self, instance: ZenInstance) -> ZenAbsoluteInput:
        absolute_input = self.absolute_input(instance)
        await absolute_input.interview()
        return absolute_input

    async def create_motion_sensor(self, instance: ZenInstance) -> ZenMotionSensor:
        sensor = self.motion_sensor(instance)
        await sensor.interview()
        return sensor

    async def create_system_variable(
        self,
        ctrl: ZenController,
        id: int,
        value: int | None = None,
        label: str | None = None,
    ) -> ZenSystemVariable:
        sysvar = self.system_variable(ctrl, id, value, label)
        await sysvar.interview()
        return sysvar

    def track_task(self, coro: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
        """Schedule fire-and-forget work and track it for cancellation on shutdown."""
        task = asyncio.create_task(coro)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_task_done)
        return task

    def _background_task_done(self, task: asyncio.Task[Any]) -> None:
        self._background_tasks.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            self.logger.error(f"Background task failed: {exc}", exc_info=exc)

    @staticmethod
    async def cancel_and_await(task: asyncio.Task[Any] | None) -> None:
        """Cancel a task and wait for it to finish. Ignores cancel/exit errors."""
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            pass

    async def cancel_background_tasks(self) -> None:
        """Cancel tracked fire-and-forget work (timers, deferred callbacks)."""
        tasks = list(self._background_tasks)
        self._background_tasks.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
