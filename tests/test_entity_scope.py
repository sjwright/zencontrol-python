"""Unit tests for Cluster D per-protocol entity identity scope."""

from __future__ import annotations

import pytest

from zencontrol.api.models import ZenAddress
from zencontrol.api.types import ZenAddressType
from zencontrol.interface.interface import ZenControl, ZenController, ZenGroup, ZenLight


@pytest.mark.asyncio
async def test_two_zencontrol_instances_isolate_entities() -> None:
    zen_a = ZenControl()
    zen_b = ZenControl()

    ctrl_a = zen_a.add_controller(id=1, name="shared", label="A", host="127.0.0.1", port=5108)
    ctrl_b = zen_b.add_controller(id=1, name="shared", label="B", host="127.0.0.1", port=5108)

    assert ctrl_a is not ctrl_b
    assert ctrl_a.label == "A"
    assert ctrl_b.label == "B"
    assert "shared" in zen_a.context.registry.controllers
    assert "shared" in zen_b.context.registry.controllers
    assert zen_a.context.registry.controllers["shared"] is ctrl_a
    assert zen_b.context.registry.controllers["shared"] is ctrl_b

    addr_a = ZenAddress(controller=ctrl_a, type=ZenAddressType.ECG, number=5)
    addr_b = ZenAddress(controller=ctrl_b, type=ZenAddressType.ECG, number=5)
    light_a = ZenLight(ctx=zen_a.context, address=addr_a)
    light_b = ZenLight(ctx=zen_b.context, address=addr_b)

    assert light_a is not light_b
    light_a.client_data["mqtt"] = "topic-a"
    assert "mqtt" not in light_b.client_data

    await zen_a.aclose()
    assert zen_a.context.registry.lights == {}
    assert "shared 5" in zen_b.context.registry.lights
    assert zen_b.context.registry.lights["shared 5"] is light_b

    await zen_b.aclose()
    assert zen_b.context.registry.lights == {}


@pytest.mark.asyncio
async def test_same_protocol_reuses_entity_identity() -> None:
    zen = ZenControl()
    ctrl = zen.add_controller(id=1, name="ctrl", label="Ctrl", host="127.0.0.1", port=5108)
    addr = ZenAddress(controller=ctrl, type=ZenAddressType.ECG, number=3)
    light1 = ZenLight(ctx=zen.context, address=addr)
    light2 = ZenLight(ctx=zen.context, address=addr)
    assert light1 is light2

    group_addr = ZenAddress(controller=ctrl, type=ZenAddressType.GROUP, number=1)
    group1 = ZenGroup(ctx=zen.context, address=group_addr)
    group2 = ZenGroup(ctx=zen.context, address=group_addr)
    assert group1 is group2
    assert group1 is not light1

    await zen.aclose()


def test_controller_same_name_same_protocol_is_singleton() -> None:
    zen = ZenControl()
    a = ZenController(
        ctx=zen.context,
        id=1,
        name="one",
        label="First",
        host="127.0.0.1",
        port=5108,
    )
    zen.commands.set_client(a, object())  # type: ignore[arg-type]
    a.version = "1.2.3"
    a.startup_complete = True
    b = ZenController(
        ctx=zen.context,
        id=2,
        name="one",
        label="Second",
        host="10.0.0.1",
        port=5108,
    )
    assert a is b
    assert a.label == "Second"
    assert a.host == "10.0.0.1"
    # Re-construct must not wipe live transport / interview state
    assert zen.commands.client_for(a) is not None
    assert a.version == "1.2.3"
    assert a.startup_complete is True
    zen.clear_entity_caches()
