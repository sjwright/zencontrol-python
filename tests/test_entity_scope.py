"""Unit tests for Cluster D per-protocol entity identity scope."""

from __future__ import annotations

import pytest

from zencontrol.api.models import ZenAddress, ZenInstance
from zencontrol.api.types import ZenAddressType, ZenInstanceType
from zencontrol.interface.interface import ZenControl


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
    light_a = zen_a.context.light(addr_a)
    light_b = zen_b.context.light(addr_b)

    assert light_a is not light_b
    light_a.client_data["mqtt"] = "topic-a"
    assert "mqtt" not in light_b.client_data

    await zen_a.aclose()
    assert zen_a.context.registry.lights == {}
    assert ("shared", 5) in zen_b.context.registry.lights
    assert zen_b.context.registry.lights[("shared", 5)] is light_b

    await zen_b.aclose()
    assert zen_b.context.registry.lights == {}


@pytest.mark.asyncio
async def test_same_protocol_reuses_entity_identity() -> None:
    zen = ZenControl()
    ctrl = zen.add_controller(id=1, name="ctrl", label="Ctrl", host="127.0.0.1", port=5108)
    addr = ZenAddress(controller=ctrl, type=ZenAddressType.ECG, number=3)
    light1 = zen.context.light(addr)
    light2 = zen.context.light(addr)
    assert light1 is light2

    group_addr = ZenAddress(controller=ctrl, type=ZenAddressType.GROUP, number=1)
    group1 = zen.context.group(group_addr)
    group2 = zen.context.group(group_addr)
    assert group1 is group2
    assert group1 is not light1

    await zen.aclose()


@pytest.mark.parametrize(
    ("factory_name", "address_type"),
    [("light", ZenAddressType.ECG), ("group", ZenAddressType.GROUP)],
)
def test_cached_addressed_entity_keeps_interviewed_address_metadata(
    factory_name: str,
    address_type: ZenAddressType,
) -> None:
    zen = ZenControl()
    ctrl = zen.add_controller(id=1, name="ctrl", label="Ctrl", host="127.0.0.1")
    interviewed = ZenAddress(controller=ctrl, type=address_type, number=3)
    interviewed.label = "Interviewed entity"
    interviewed.serial = "serial-3"
    factory = getattr(zen.context, factory_name)
    entity = factory(interviewed)

    event_address = ZenAddress(controller=ctrl, type=address_type, number=3)
    same_entity = factory(event_address)

    assert same_entity is entity
    assert same_entity.address is interviewed
    assert same_entity.address.label == "Interviewed entity"
    assert same_entity.address.serial == "serial-3"
    zen.clear_entity_caches()


@pytest.mark.parametrize("factory_name", ["button", "absolute_input", "motion_sensor"])
def test_cached_instance_entity_keeps_interviewed_instance(factory_name: str) -> None:
    zen = ZenControl()
    ctrl = zen.add_controller(id=1, name="ctrl", label="Ctrl", host="127.0.0.1")
    interviewed_address = ZenAddress(controller=ctrl, type=ZenAddressType.ECD, number=4)
    interviewed_address.label = "Interviewed device"
    interviewed_address.serial = "serial-4"
    interviewed = ZenInstance(address=interviewed_address, type=ZenInstanceType.PUSH_BUTTON, number=2)
    factory = getattr(zen.context, factory_name)
    entity = factory(interviewed)

    event_address = ZenAddress(controller=ctrl, type=ZenAddressType.ECD, number=4)
    event_instance = ZenInstance(address=event_address, type=ZenInstanceType.PUSH_BUTTON, number=2)
    same_entity = factory(event_instance)

    assert same_entity is entity
    assert same_entity.instance is interviewed
    assert same_entity.instance.address.label == "Interviewed device"
    assert same_entity.instance.address.serial == "serial-4"
    zen.clear_entity_caches()


def test_cached_system_variable_accepts_explicit_value_and_label_updates() -> None:
    zen = ZenControl()
    ctrl = zen.add_controller(id=1, name="ctrl", label="Ctrl", host="127.0.0.1")
    variable = zen.context.system_variable(ctrl, 2, value=10, label="Original")

    same_variable = zen.context.system_variable(ctrl, 2, value=20, label="Updated")

    assert same_variable is variable
    assert same_variable.value == 20
    assert same_variable.label == "Updated"
    zen.clear_entity_caches()


def test_controller_same_name_same_protocol_is_singleton() -> None:
    zen = ZenControl()
    a = zen.context.controller(
        id=1,
        name="one",
        label="First",
        host="127.0.0.1",
        port=5108,
    )
    zen.commands.set_client(a, object())  # type: ignore[arg-type]
    a.version = "1.2.3"
    a.startup_complete = True
    b = zen.context.controller(
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
