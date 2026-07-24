"""Unit tests for ZenAbsoluteInput discovery and events."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from zencontrol.api.models import ZenAddress, ZenInstance
from zencontrol.api.types import ZenAddressType, ZenInstanceType
from zencontrol.interface.interface import ZenAbsoluteInput, ZenControl


def _ecd_instance(zen: ZenControl, *, number: int = 0, inst: int = 1) -> ZenInstance:
    ctrl = zen.add_controller(
        id=1, name="house", label="House", host="127.0.0.1", port=5108
    )
    addr = ZenAddress(controller=ctrl, type=ZenAddressType.ECD, number=number)
    return ZenInstance(address=addr, type=ZenInstanceType.ABSOLUTE_INPUT, number=inst)


@pytest.mark.asyncio
async def test_absolute_input_event_parses_16bit_value() -> None:
    zen = ZenControl()
    instance = _ecd_instance(zen)
    changes: list[tuple[ZenAbsoluteInput, int]] = []

    async def on_change(absolute_input: ZenAbsoluteInput, value: int) -> None:
        changes.append((absolute_input, value))

    zen.absolute_input_change = on_change
    absolute = ZenAbsoluteInput(protocol=zen.protocol, instance=instance)
    assert absolute.interview_hydrate(
        {"serial": "1", "label": "Panel", "instance_label": "Dial"}
    )

    await zen.absolute_input_event(instance, bytes([1, 0x12, 0x34]))
    assert absolute.value == 0x1234
    assert changes == [(absolute, 0x1234)]

    await zen.absolute_input_event(instance, bytes([1, 0x12, 0x34]))
    assert changes == [(absolute, 0x1234)]

    await zen.absolute_input_event(instance, bytes([1, 0x00, 0x01]))
    assert absolute.value == 1
    assert changes[-1] == (absolute, 1)


@pytest.mark.asyncio
async def test_absolute_input_event_ignores_short_payload() -> None:
    zen = ZenControl()
    instance = _ecd_instance(zen)
    absolute = ZenAbsoluteInput(protocol=zen.protocol, instance=instance)
    await zen.absolute_input_event(instance, bytes([1]))
    assert absolute.value is None


@pytest.mark.asyncio
async def test_get_absolute_inputs_filters_instance_type() -> None:
    zen = ZenControl()
    ctrl = zen.add_controller(
        id=1, name="house", label="House", host="127.0.0.1", port=5108
    )
    addr = ZenAddress(controller=ctrl, type=ZenAddressType.ECD, number=2)
    abs_inst = ZenInstance(
        address=addr, type=ZenInstanceType.ABSOLUTE_INPUT, number=0
    )
    btn_inst = ZenInstance(
        address=addr, type=ZenInstanceType.PUSH_BUTTON, number=1
    )

    zen._get_addresses_with_instances = AsyncMock(return_value=[addr])  # noqa: SLF001
    zen.protocol.query_instances_by_address = AsyncMock(
        return_value=[abs_inst, btn_inst]
    )
    zen.protocol.query_dali_device_label = AsyncMock(return_value="Wall")
    zen.protocol.query_dali_serial = AsyncMock(return_value="ABC")
    zen.protocol.query_dali_instance_label = AsyncMock(return_value="Slider")

    found = await zen.get_absolute_inputs(controller=ctrl)
    assert len(found) == 1
    item = next(iter(found))
    assert item.instance.type == ZenInstanceType.ABSOLUTE_INPUT
    assert item.label == "Wall"
    assert item.instance_label == "Slider"
    assert item in ctrl.absolute_inputs


@pytest.mark.asyncio
async def test_absolute_input_singleton_per_protocol() -> None:
    zen = ZenControl()
    instance = _ecd_instance(zen)
    a = ZenAbsoluteInput(protocol=zen.protocol, instance=instance)
    b = ZenAbsoluteInput(protocol=zen.protocol, instance=instance)
    assert a is b
    assert "house 0 1" in zen.protocol.entity_registry.absolute_inputs
