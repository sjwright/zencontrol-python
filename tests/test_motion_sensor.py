"""ZenMotionSensor occupancy state and hold-timer handling."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from zencontrol.api.models import ZenAddress, ZenInstance
from zencontrol.api.types import OccupancyInstanceTimers, ZenAddressType, ZenInstanceType
from zencontrol.interface.interface import ZenControl, ZenMotionSensor


def _sensor(zen: ZenControl) -> ZenMotionSensor:
    ctrl = zen.add_controller(id=1, name="house", label="House", host="127.0.0.1", port=5108)
    addr = ZenAddress(ctrl=ctrl, type=ZenAddressType.ECD, number=0)
    return zen.ctx.motion_sensor(ZenInstance(address=addr, type=ZenInstanceType.OCCUPANCY_SENSOR, number=0))


def _record(zen: ZenControl) -> list[bool]:
    seen: list[bool] = []

    async def on_motion(*, sensor: ZenMotionSensor) -> None:
        seen.append(sensor.occupied)

    zen.callbacks.motion_event = on_motion
    return seen


@pytest.mark.asyncio
async def test_motion_holds_then_expires() -> None:
    zen = ZenControl()
    sensor = _sensor(zen)
    seen = _record(zen)
    sensor.hold_time = 0.02

    await sensor._handle_event()
    await sensor._handle_event()  # repeat motion while occupied: no second callback
    assert sensor.occupied and seen == [True]

    await asyncio.sleep(0.1)
    assert not sensor.occupied and seen == [True, False]
    await zen.aclose()


@pytest.mark.asyncio
async def test_failed_refresh_does_not_orphan_hold_timer() -> None:
    """Regression: a failed refresh used to drop the timer reference without cancelling it,
    so the old timer later cleared a newer hold early."""
    zen = ZenControl()
    sensor = _sensor(zen)
    zen.commands.query_occupancy_instance_timers = AsyncMock(return_value=None)

    await sensor._handle_event()
    first_timer = sensor.hold_expiry_task
    assert not await sensor.refresh_state_from_controller()
    assert sensor.occupied and sensor.hold_expiry_task is first_timer  # failure keeps state

    await sensor._handle_event()
    await asyncio.sleep(0)
    assert first_timer is not None and first_timer.cancelled()
    await zen.aclose()


@pytest.mark.asyncio
async def test_refresh_adopts_controller_timers_and_notifies_on_change() -> None:
    zen = ZenControl()
    sensor = _sensor(zen)
    seen = _record(zen)
    timers = AsyncMock(return_value=OccupancyInstanceTimers(deadtime=0, hold=60, report=0, last_detect=5))
    zen.commands.query_occupancy_instance_timers = timers

    assert await sensor.refresh_state_from_controller()
    assert sensor.occupied and seen == [True]
    assert sensor.hold_time == 60 and sensor.hold_expiry_task is not None

    timers.return_value = OccupancyInstanceTimers(deadtime=0, hold=60, report=0, last_detect=90)
    assert await sensor.refresh_state_from_controller()
    assert not sensor.occupied and seen == [True, False]
    assert sensor.hold_expiry_task is None
    await zen.aclose()
