"""Entity-layer context: callbacks, registry, and fire-and-forget tasks.

Keeps ``ZenCommandClient`` as a pure TPI/UDP command plane. High-level entity
identity and application callbacks live here, owned by ``ZenControl``.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Coroutine
from typing import TYPE_CHECKING, Any, Literal, Protocol

from ..api.commands import ZenCommandClient
from ..api.models import DiscoveredController

if TYPE_CHECKING:
    from ..api.models import ZenColour
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
    def __call__(
        self,
        *,
        group: ZenGroup,
        level: int | None = None,
        colour: ZenColour | None = None,
        scene: int | None = None,
        discoordinated: bool = False,
    ) -> Awaitable[None]: ...


class LightChangeHandler(Protocol):
    def __call__(
        self,
        *,
        light: ZenLight,
        level: int | None = None,
        colour: ZenColour | None = None,
        scene: int | None = None,
    ) -> Awaitable[None]: ...


class ButtonPressHandler(Protocol):
    def __call__(self, *, button: ZenButton) -> Awaitable[None]: ...


class AbsoluteInputChangeHandler(Protocol):
    def __call__(self, *, absolute_input: ZenAbsoluteInput, value: int) -> Awaitable[None]: ...


class MotionEventHandler(Protocol):
    def __call__(self, *, sensor: ZenMotionSensor, occupied: bool) -> Awaitable[None]: ...


class SystemVariableChangeHandler(Protocol):
    def __call__(
        self,
        *,
        system_variable: ZenSystemVariable,
        value: int | None,
        changed: bool,
        by_me: bool,
    ) -> Awaitable[None]: ...


class ControllerDiscoveredHandler(Protocol):
    def __call__(self, discovered: DiscoveredController) -> Awaitable[None]: ...


class ControllerIdentifiedHandler(Protocol):
    def __call__(self, controller: ZenController, mac: str) -> Awaitable[None]: ...


class ControllerStatusChangeHandler(Protocol):
    def __call__(self, controller: ZenController, status: ControllerRuntimeStatus) -> Awaitable[None]: ...


class ZenCallbacks:
    """Per-ZenControl high-level callback registry.

    Stored on ``EntityContext.callbacks`` so entity singletons reach their
    owning integration's callbacks via ``self.ctx.callbacks``.
    """

    def __init__(self) -> None:
        self.on_connect: OnConnectHandler | None = None
        self.on_disconnect: OnDisconnectHandler | None = None
        # Session gap after receiver restore (not a wire event).
        self.on_resync: OnResyncHandler | None = None
        self.profile_change: ProfileChangeHandler | None = None
        self.group_change: GroupChangeHandler | None = None
        self.light_change: LightChangeHandler | None = None
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

    Entities keyed here are unique within one ``EntityContext`` / ``ZenControl``
    instance, not process-wide.
    """

    def __init__(self) -> None:
        self.controllers: dict[str, Any] = {}
        self.profiles: dict[str, Any] = {}
        self.lights: dict[str, Any] = {}
        self.groups: dict[str, Any] = {}
        self.buttons: dict[str, Any] = {}
        self.absolute_inputs: dict[str, Any] = {}
        self.motion_sensors: dict[str, Any] = {}
        self.system_variables: dict[str, Any] = {}

    def clear(self) -> None:
        self.controllers.clear()
        self.profiles.clear()
        self.lights.clear()
        self.groups.clear()
        self.buttons.clear()
        self.absolute_inputs.clear()
        self.motion_sensors.clear()
        self.system_variables.clear()

    def purge_controller(self, controller_name: str) -> None:
        """Drop cached entities that belong to ``controller_name``."""
        self.controllers.pop(controller_name, None)
        prefix = f"{controller_name} "
        for store in (
            self.profiles,
            self.lights,
            self.groups,
            self.buttons,
            self.absolute_inputs,
            self.motion_sensors,
            self.system_variables,
        ):
            for key in [k for k in store if k == controller_name or k.startswith(prefix)]:
                store.pop(key, None)


class EntityContext:
    """Owns entity callbacks, identity registry, and deferred interface tasks.

    Advanced/command-only surface. Prefer ``ZenControl`` for applications that
    need event monitoring, discovery, or session lifecycle — it creates and
    owns an ``EntityContext``. Use this directly only when you drive
    ``ZenCommandClient`` yourself without the event session.
    """

    def __init__(
        self,
        commands: ZenCommandClient,
        logger: logging.Logger | None = None,
    ) -> None:
        self.commands = commands
        self.logger = logger or commands.logger
        self.callbacks = ZenCallbacks()
        self.registry = EntityRegistry()
        self._bg_tasks: set[asyncio.Task[Any]] = set()

    def clear_entity_caches(self) -> None:
        """Drop all interface entity singletons owned by this context."""
        self.registry.clear()

    def purge_controller_entities(self, controller_name: str) -> None:
        """Drop interface-layer singletons for one controller."""
        self.registry.purge_controller(controller_name)

    def track_task(self, coro: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
        """Schedule fire-and-forget work and track it for cancellation on shutdown."""
        task = asyncio.create_task(coro)
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_task_done)
        return task

    def _bg_task_done(self, task: asyncio.Task[Any]) -> None:
        self._bg_tasks.discard(task)
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
        tasks = list(self._bg_tasks)
        self._bg_tasks.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
