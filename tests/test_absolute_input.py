"""Unit tests for ZenAbsoluteInput discovery and events."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from zencontrol.api.event_decode import AbsoluteInput
from zencontrol.api.models import ZenAddress, ZenInstance
from zencontrol.api.types import ZenAddressType, ZenInstanceType
from zencontrol.interface.interface import ZenAbsoluteInput, ZenControl, ZenController


def _ecd_instance(zen: ZenControl, *, number: int = 0, inst: int = 1) -> tuple[ZenController, ZenInstance]:
    ctrl = zen.add_controller(id=1, name="house", label="House", host="127.0.0.1", port=5108)
    addr = ZenAddress(controller=ctrl, type=ZenAddressType.ECD, number=number)
    return ctrl, ZenInstance(address=addr, type=ZenInstanceType.ABSOLUTE_INPUT, number=inst)


@pytest.mark.asyncio
async def test_absolute_input_event_parses_16bit_value() -> None:
    zen = ZenControl()
    ctrl, instance = _ecd_instance(zen)
    changes: list[tuple[ZenAbsoluteInput, int]] = []

    async def on_change(*, absolute_input: ZenAbsoluteInput) -> None:
        changes.append((absolute_input, absolute_input.value))

    zen.callbacks.absolute_input_change = on_change
    absolute = ZenAbsoluteInput(ctx=zen.context, instance=instance)
    assert absolute.interview_hydrate({"serial": "1", "label": "Panel", "instance_label": "Dial"})

    async def _dispatch(ev: AbsoluteInput) -> None:
        await zen._on_controller_event(ctrl, ev)
        tail = zen._dispatcher.tail.get(ctrl.name)
        if tail is not None:
            await tail

    await _dispatch(AbsoluteInput(target=64, instance=1, value=0x1234))
    assert absolute.value == 0x1234
    assert changes == [(absolute, 0x1234)]

    await _dispatch(AbsoluteInput(target=64, instance=1, value=0x1234))
    assert changes == [(absolute, 0x1234)]

    await _dispatch(AbsoluteInput(target=64, instance=1, value=1))
    assert absolute.value == 1
    assert changes[-1] == (absolute, 1)


@pytest.mark.asyncio
async def test_absolute_input_event_ignores_short_payload() -> None:
    zen = ZenControl()
    _ctrl, instance = _ecd_instance(zen)
    absolute = ZenAbsoluteInput(ctx=zen.context, instance=instance)
    await absolute._event_received(bytes([1]))
    assert absolute.value is None


@pytest.mark.asyncio
async def test_get_absolute_inputs_filters_instance_type() -> None:
    zen = ZenControl()
    ctrl = zen.add_controller(id=1, name="house", label="House", host="127.0.0.1", port=5108)
    addr = ZenAddress(controller=ctrl, type=ZenAddressType.ECD, number=2)
    abs_inst = ZenInstance(address=addr, type=ZenInstanceType.ABSOLUTE_INPUT, number=0)
    btn_inst = ZenInstance(address=addr, type=ZenInstanceType.PUSH_BUTTON, number=1)

    zen._get_addresses_with_instances = AsyncMock(return_value=[addr])  # noqa: SLF001
    zen.commands.query_instances_by_address = AsyncMock(return_value=[abs_inst, btn_inst])
    zen.commands.query_dali_device_label = AsyncMock(return_value="Wall")
    zen.commands.query_dali_serial = AsyncMock(return_value="ABC")
    zen.commands.query_dali_instance_label = AsyncMock(return_value="Slider")

    found = await zen.get_absolute_inputs(controller=ctrl)
    assert len(found) == 1
    item = next(iter(found))
    assert item.instance.type == ZenInstanceType.ABSOLUTE_INPUT
    assert item.label == "Wall"
    assert item.instance_label == "Slider"
    assert item in ctrl.absolute_inputs


@pytest.mark.asyncio
async def test_ecd_getters_share_instance_scan() -> None:
    zen = ZenControl()
    ctrl = zen.add_controller(id=1, name="house", label="House", host="127.0.0.1", port=5108)
    addr = ZenAddress(controller=ctrl, type=ZenAddressType.ECD, number=2)
    abs_inst = ZenInstance(address=addr, type=ZenInstanceType.ABSOLUTE_INPUT, number=0)
    btn_inst = ZenInstance(address=addr, type=ZenInstanceType.PUSH_BUTTON, number=1)
    motion_inst = ZenInstance(address=addr, type=ZenInstanceType.OCCUPANCY_SENSOR, number=2)

    zen._get_addresses_with_instances = AsyncMock(return_value=[addr])  # noqa: SLF001
    query_instances = AsyncMock(return_value=[abs_inst, btn_inst, motion_inst])
    zen.commands.query_instances_by_address = query_instances
    zen.commands.query_dali_device_label = AsyncMock(return_value="Panel")
    zen.commands.query_dali_serial = AsyncMock(return_value="ABC")
    zen.commands.query_dali_instance_label = AsyncMock(return_value="Inst")
    zen.commands.query_occupancy_instance_timers = AsyncMock(
        return_value={"deadtime": 0, "hold": 60, "last_detect": 0}
    )

    buttons = await zen.get_buttons(controller=ctrl)
    absolute_inputs = await zen.get_absolute_inputs(controller=ctrl)
    sensors = await zen.get_motion_sensors(controller=ctrl)

    assert len(buttons) == 1
    assert len(absolute_inputs) == 1
    assert len(sensors) == 1
    assert query_instances.await_count == 1
    assert zen._get_addresses_with_instances.await_count == 1  # noqa: SLF001

    zen.clear_entity_caches()
    await zen.get_buttons(controller=ctrl)
    assert query_instances.await_count == 2


@pytest.mark.asyncio
async def test_absolute_input_singleton_per_protocol() -> None:
    zen = ZenControl()
    _ctrl, instance = _ecd_instance(zen)
    a = ZenAbsoluteInput(ctx=zen.context, instance=instance)
    b = ZenAbsoluteInput(ctx=zen.context, instance=instance)
    assert a is b
    assert "house 0 1" in zen.context.registry.absolute_inputs
