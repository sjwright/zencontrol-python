"""Test harness combining command and event planes for simulator tests.

Not public API — use ``ZenControl`` in application code.

Example::

    from zencontrol.testing import ZenTestClient
    from zencontrol import ZenController

    p = ZenTestClient(unicast=True, listen_ip="127.0.0.1", listen_port=0)
    ctrl = ZenController(..., ctx=p.context)
    p.set_controllers([ctrl])
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Self, cast

from ..api.commands import ZenCommandClient
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
from ..api.event_router import ZenEventReceiver
from ..api.models import ControllerRef, ZenAddress, ZenColour, ZenController, ZenInstance
from ..api.types import Transport, ZenAddressType, ZenEventMode, ZenInstanceType
from ..interface.context import EntityContext
from ..interface.wiring import ZenEventWiring

LegacyCallback = Callable[..., Awaitable[None]]


class ZenTestClient:
    """Test harness: ``ZenCommandClient`` commands + legacy callback event monitoring."""

    def __init__(
        self,
        logger: logging.Logger | None = None,
        print_traffic: bool = False,
        unicast: bool = False,
        listen_ip: str | None = None,
        listen_port: int | None = None,
    ) -> None:
        self.unicast = unicast
        self.controllers: list[ControllerRef] = []
        self.commands = ZenCommandClient(
            logger=logger,
            print_traffic=print_traffic,
        )
        self.context = EntityContext(commands=self.commands, logger=self.commands.logger)
        self.event_receiver = ZenEventReceiver(
            logger=self.commands.logger,
            unicast_listen_ip=(listen_ip if listen_ip else "0.0.0.0") if unicast else "0.0.0.0",
            unicast_port=(listen_port if listen_port is not None else 0) if unicast else 0,
        )
        self._wiring: ZenEventWiring | None = None

        self.button_press_callback: LegacyCallback | None = None
        self.button_hold_callback: LegacyCallback | None = None
        self.absolute_input_callback: LegacyCallback | None = None
        self.level_change_callback: LegacyCallback | None = None
        self.group_level_change_callback: LegacyCallback | None = None
        self.scene_change_callback: LegacyCallback | None = None
        self.is_occupied_callback: LegacyCallback | None = None
        self.colour_change_callback: LegacyCallback | None = None
        self.profile_change_callback: LegacyCallback | None = None
        self.system_variable_change_callback: LegacyCallback | None = None
        self.disconnect_callback: Callable[[], Awaitable[None]] | None = None

    def __getattr__(self, name: str) -> Any:
        return getattr(self.commands, name)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        await self.aclose()

    def set_controllers(self, controllers: Sequence[ControllerRef]) -> None:
        self.controllers = list(controllers)

    def set_callbacks(
        self,
        *,
        button_press_callback: LegacyCallback | None = None,
        button_hold_callback: LegacyCallback | None = None,
        absolute_input_callback: LegacyCallback | None = None,
        level_change_callback: LegacyCallback | None = None,
        group_level_change_callback: LegacyCallback | None = None,
        scene_change_callback: LegacyCallback | None = None,
        is_occupied_callback: LegacyCallback | None = None,
        colour_change_callback: LegacyCallback | None = None,
        profile_change_callback: LegacyCallback | None = None,
        system_variable_change_callback: LegacyCallback | None = None,
        disconnect_callback: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self.button_press_callback = button_press_callback
        self.button_hold_callback = button_hold_callback
        self.absolute_input_callback = absolute_input_callback
        self.level_change_callback = level_change_callback
        self.group_level_change_callback = group_level_change_callback
        self.scene_change_callback = scene_change_callback
        self.is_occupied_callback = is_occupied_callback
        self.colour_change_callback = colour_change_callback
        self.profile_change_callback = profile_change_callback
        self.system_variable_change_callback = system_variable_change_callback
        self.disconnect_callback = disconnect_callback

    def _event_mode_for(self, controller: ZenController) -> ZenEventMode:
        return ZenEventMode(
            enabled=True,
            filtering=controller.filtering,
            transport=(Transport.UNICAST if self.unicast else Transport.MULTICAST),
        )

    async def start_event_monitoring(self) -> None:
        if self.is_event_monitoring_active():
            return

        self._wiring = ZenEventWiring(
            self.event_receiver,
            self.commands,
            event_handler=self._on_controller_event,
            logger=self.commands.logger,
        )
        for controller in self.controllers:
            ctrl = cast(ZenController, controller)
            await self._wiring.attach(ctrl, self._event_mode_for(ctrl))

    async def stop_event_monitoring(self) -> None:
        if self._wiring is not None:
            await self._wiring.detach_all()
            self._wiring = None

    def is_event_monitoring_active(self) -> bool:
        if self._wiring is None:
            return False
        task = self.event_receiver.consumer_task
        if task is None or task.done():
            return False
        return self.event_receiver.leased_transports_open()

    async def aclose(self) -> None:
        await self.stop_event_monitoring()
        await self.context.cancel_background_tasks()
        self.context.clear_entity_caches()
        await self.commands.aclose()
        await self.event_receiver.close()

    def _ecd_address(self, controller: ZenController, target: int) -> ZenAddress | None:
        number = target - 64
        if not 0 <= number <= 63:
            self.commands.logger.error(f"Invalid ECD event target: {target}")
            return None
        return ZenAddress(controller=controller, type=ZenAddressType.ECD, number=number)

    def _ecg_or_group(self, controller: ZenController, target: int) -> ZenAddress | None:
        if target <= 63:
            return ZenAddress(controller=controller, type=ZenAddressType.ECG, number=target)
        if 64 <= target <= 79:
            return ZenAddress(
                controller=controller,
                type=ZenAddressType.GROUP,
                number=target - 64,
            )
        self.commands.logger.error(f"Invalid gear/group event target: {target}")
        return None

    async def _call(self, callback: LegacyCallback | None, **kwargs: Any) -> None:
        if not callable(callback):
            return
        try:
            await callback(**kwargs)
        except Exception as err:
            self.commands.logger.error(f"Event callback error: {err}", exc_info=err)

    async def _on_controller_event(self, controller: ZenController, ev: ZenDecodedEvent) -> None:
        match ev:
            case ButtonPress(target, instance_num):
                address = self._ecd_address(controller, target)
                if address is None:
                    return
                instance = ZenInstance(
                    address=address,
                    type=ZenInstanceType.PUSH_BUTTON,
                    number=instance_num,
                )
                await self._call(
                    self.button_press_callback,
                    instance=instance,
                    payload=bytes([instance_num]),
                )

            case ButtonHold(target, instance_num):
                address = self._ecd_address(controller, target)
                if address is None:
                    return
                instance = ZenInstance(
                    address=address,
                    type=ZenInstanceType.PUSH_BUTTON,
                    number=instance_num,
                )
                await self._call(
                    self.button_hold_callback,
                    instance=instance,
                    payload=bytes([instance_num]),
                )

            case AbsoluteInput(target, instance_num, value):
                address = self._ecd_address(controller, target)
                if address is None:
                    return
                instance = ZenInstance(
                    address=address,
                    type=ZenInstanceType.ABSOLUTE_INPUT,
                    number=instance_num,
                )
                await self._call(
                    self.absolute_input_callback,
                    instance=instance,
                    payload=bytes([instance_num, (value >> 8) & 0xFF, value & 0xFF]),
                )

            case LevelChangeV2(target, current, level):
                if not self.level_change_callback:
                    return
                address = self._ecg_or_group(controller, target)
                if address is None:
                    return
                payload = bytes([current, level])
                if address.type == ZenAddressType.ECG:
                    await self._call(
                        self.level_change_callback,
                        address=address,
                        arc_level=level,
                        payload=payload,
                    )
                elif self.group_level_change_callback:
                    await self._call(
                        self.group_level_change_callback,
                        address=address,
                        arc_level=level,
                        payload=payload,
                    )

            case SceneChange(target, scene, active):
                address = self._ecg_or_group(controller, target)
                if address is None:
                    return
                await self._call(
                    self.scene_change_callback,
                    address=address,
                    scene=scene,
                    active=active,
                    payload=bytes([scene, int(active)]),
                )

            case IsOccupied(target, instance_num):
                address = self._ecd_address(controller, target)
                if address is None:
                    return
                instance = ZenInstance(
                    address=address,
                    type=ZenInstanceType.OCCUPANCY_SENSOR,
                    number=instance_num,
                )
                # Wire layout: instance byte + unused 0x01 (motion-shaped OCCUPANCY).
                await self._call(
                    self.is_occupied_callback,
                    instance=instance,
                    payload=bytes([instance_num, 0x01]),
                )

            case ColourChange(target, colour_bytes):
                address = self._ecg_or_group(controller, target)
                if address is None:
                    return
                colour = ZenColour.from_bytes(colour_bytes)
                if colour is None:
                    return
                await self._call(
                    self.colour_change_callback,
                    address=address,
                    colour=colour,
                    payload=colour_bytes,
                )

            case ProfileChange(profile):
                await self._call(
                    self.profile_change_callback,
                    controller=controller,
                    profile=profile,
                    payload=bytes([profile & 0xFF]),
                )

            case SystemVariableChange(target, value):
                await self._call(
                    self.system_variable_change_callback,
                    controller=controller,
                    target=target,
                    value=value,
                    payload=b"",
                )

            case _:
                return
