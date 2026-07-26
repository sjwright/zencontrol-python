"""Decoded event → entity updates (off the funnel consumer path)."""

from __future__ import annotations

import asyncio
import logging
from typing import cast

from ..api import ZenAddress, ZenAddressType, ZenColour, ZenInstance, ZenInstanceType
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
from .context import EntityContext
from .entities import (
    ZenAbsoluteInput,
    ZenButton,
    ZenController,
    ZenGroup,
    ZenLight,
    ZenMotionSensor,
    ZenSystemVariable,
)


class EventDispatcher:
    """Per-controller dispatch chain; application callbacks run after the funnel (I8)."""

    def __init__(self, ctx: EntityContext, logger: logging.Logger) -> None:
        self.ctx = ctx
        self.logger = logger
        self.tail: dict[str, asyncio.Task[None]] = {}

    def forget(self, name: str) -> None:
        """Drop the per-controller dispatch-chain tail when a binding goes away."""
        self.tail.pop(name, None)

    def clear(self) -> None:
        self.tail.clear()

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

    def _ecg_or_group(self, controller: ZenController, target: int) -> ZenAddress | None:
        if target <= 63:
            return ZenAddress(controller=controller, type=ZenAddressType.ECG, number=target)
        if 64 <= target <= 79:
            return ZenAddress(controller=controller, type=ZenAddressType.GROUP, number=target - 64)
        self.logger.error(f"Invalid gear/group event target: {target}")
        return None

    async def handle(self, controller: SuperZenController, ev: ZenDecodedEvent) -> None:
        """Subscription handler entry — returns immediately (I8)."""
        ctrl = cast(ZenController, controller)
        name = ctrl.name
        previous = self.tail.get(name)

        async def run() -> None:
            if previous is not None and not previous.done():
                try:
                    await previous
                except asyncio.CancelledError:
                    me = asyncio.current_task()
                    if me is not None and me.cancelling():
                        raise
                except Exception:
                    pass
            try:
                await self.dispatch(ctrl, ev)
            except asyncio.CancelledError:
                raise
            except Exception as err:
                self.logger.error("Event dispatch error for %s: %s", name, err, exc_info=True)

        self.tail[name] = self.ctx.track_task(run())

    async def dispatch(self, ctrl: ZenController, ev: ZenDecodedEvent) -> None:
        """Apply a decoded event to entities and fire application callbacks."""
        match ev:
            case ButtonPress(target, instance_num):
                instance = self._ecd_instance(ctrl, target, ZenInstanceType.PUSH_BUTTON, instance_num)
                if instance is None:
                    return
                await ZenButton(ctx=self.ctx, instance=instance)._event_received()

            case ButtonHold(target, instance_num):
                instance = self._ecd_instance(ctrl, target, ZenInstanceType.PUSH_BUTTON, instance_num)
                if instance is None:
                    return
                await ZenButton(ctx=self.ctx, instance=instance)._event_received(held=True)

            case AbsoluteInput(target, instance_num, value):
                instance = self._ecd_instance(ctrl, target, ZenInstanceType.ABSOLUTE_INPUT, instance_num)
                if instance is None:
                    return
                payload = bytes([instance_num, (value >> 8) & 0xFF, value & 0xFF])
                await ZenAbsoluteInput(ctx=self.ctx, instance=instance)._event_received(payload)

            case IsOccupied(target, instance_num):
                instance = self._ecd_instance(ctrl, target, ZenInstanceType.OCCUPANCY_SENSOR, instance_num)
                if instance is None:
                    return
                await ZenMotionSensor(ctx=self.ctx, instance=instance)._event_received()

            case LevelChangeV2(target, _current, level):
                await self._dispatch_level(ctrl, target, level)

            # LEVEL_CHANGE / GROUP_LEVEL_CHANGE / GROUP_OCCUPIED: not subscribed
            # (see ZenEventMask.all_events) and ignored here if they arrive.

            case ColourChange(target, colour_bytes):
                address = self._ecg_or_group(ctrl, target)
                if address is None:
                    return
                colour = ZenColour.from_bytes(colour_bytes)
                if colour is None:
                    return
                if address.type == ZenAddressType.ECG:
                    await ZenLight(ctx=self.ctx, address=address)._event_received(colour=colour)
                elif address.type == ZenAddressType.GROUP:
                    group = ZenGroup(ctx=self.ctx, address=address)
                    await group._event_received(colour=colour)
                    for light in group.lights:
                        await light._event_received(colour=colour, cascaded_from=group)

            case SceneChange(target, scene, active):
                address = self._ecg_or_group(ctrl, target)
                if address is None:
                    return
                if address.type == ZenAddressType.ECG:
                    await ZenLight(ctx=self.ctx, address=address)._event_received(scene=scene, active=active)
                elif address.type == ZenAddressType.GROUP:
                    group = ZenGroup(ctx=self.ctx, address=address)
                    await group._event_received(scene=scene, active=active)
                    for light in group.lights:
                        await light._event_received(scene=scene, active=active, cascaded_from=group)

            case SystemVariableChange(target, value):
                await ZenSystemVariable(ctx=self.ctx, controller=ctrl, id=target)._event_received(value)

            case ProfileChange(profile):
                await ctrl._event_received(profile=profile)

            case _:
                return

    async def _dispatch_level(self, ctrl: ZenController, target: int, level: int) -> None:
        address = self._ecg_or_group(ctrl, target)
        if address is None:
            return
        if address.type == ZenAddressType.ECG:
            light = ZenLight(ctx=self.ctx, address=address)
            await light._event_received(level=level)
        elif address.type == ZenAddressType.GROUP:
            group = ZenGroup(ctx=self.ctx, address=address)
            await group._event_received(level=level)
