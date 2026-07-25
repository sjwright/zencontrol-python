"""Protocol-layer integration tests against zencontrol-simulator."""

from __future__ import annotations

import pytest

from zencontrol import ZenColour, ZenColourType, ZenEventMask, ZenEventMode

from helpers import wait_until

pytestmark = pytest.mark.simulator


@pytest.mark.asyncio
async def test_controller_identity_and_readiness(live_sim):
    p, c = live_sim.protocol, live_sim.controller
    version = await p.query_controller_version_number(c)
    assert version is not None
    assert "2" in version
    assert await p.query_controller_label(c) == "Simulator"
    assert await p.query_controller_startup_complete(c) is True
    assert await p.query_is_dali_ready(c) is True


@pytest.mark.asyncio
async def test_discover_gear_groups_and_devices(live_sim):
    p, c = live_sim.protocol, live_sim.controller

    gears = await p.query_control_gear_dali_addresses(c)
    assert sorted(a.number for a in gears) == list(range(12))

    groups = await p.query_group_numbers(c)
    assert sorted(a.number for a in groups) == [0, 1, 2, 3, 4, 5]
    assert await p.query_group_label(live_sim.group(0)) == "Living Areas"

    devices = await p.query_dali_addresses_with_instances(c, start_address=0)
    ecd_nums = {a.number for a in devices}
    assert {0, 1, 2, 10, 11}.issubset(ecd_nums)

    instances = await p.query_instances_by_address(live_sim.ecd(0))
    types = {(i.number, i.type.value) for i in instances}
    assert (0, 1) in types  # push button
    assert (2, 3) in types  # occupancy


@pytest.mark.asyncio
async def test_light_identity_features_and_membership(live_sim):
    p = live_sim.protocol
    ecg0 = live_sim.ecg(0)
    ecg1 = live_sim.ecg(1)

    assert await p.query_dali_device_label(ecg0) == "Living Room Ceiling"
    serial = await p.query_dali_serial(ecg0)
    assert serial is not None and serial > 0

    cg = await p.dali_query_cg_type(ecg0)
    assert cg is not None and 6 in cg and 8 in cg

    features = await p.query_dali_colour_features(ecg0)
    assert features is not None
    assert features.get("supports_tunable") or features.get("colour_temperature")

    limits = await p.query_dali_colour_temp_limits(ecg0)
    assert limits is not None
    assert limits.get("soft_warmest") == 2700 or limits.get("physical_warmest") == 2700

    groups = await p.query_group_membership_by_address(ecg1)
    assert {g.number for g in groups} == {0, 1}


@pytest.mark.asyncio
async def test_arc_level_off_and_on(live_sim):
    p = live_sim.protocol
    addr = live_sim.ecg(1)

    assert await p.dali_arc_level(addr, 77) is True
    assert await p.dali_query_level(addr) == 77
    assert live_sim.world.lights[1].level == 77

    assert await p.dali_off(addr) is True
    assert await p.dali_query_level(addr) == 0
    assert await p.dali_on_step_up(addr) is True
    assert await p.dali_query_level(addr) == live_sim.world.lights[1].min_level


@pytest.mark.asyncio
async def test_group_arc_and_mixed_level_query(live_sim):
    p = live_sim.protocol
    g0 = live_sim.group(0)

    assert await p.dali_arc_level(g0, 40) is True
    assert await p.dali_query_level(g0) == 40
    assert live_sim.world.lights[0].level == 40
    assert live_sim.world.lights[1].level == 40

    await p.dali_arc_level(live_sim.ecg(0), 10)
    await p.dali_arc_level(live_sim.ecg(1), 20)
    assert await p.dali_query_level(g0) is None  # mixed → None


@pytest.mark.asyncio
async def test_scene_recall_and_queries(live_sim):
    p = live_sim.protocol
    addr = live_sim.ecg(0)

    assert await p.dali_scene(addr, 1) is True
    assert await p.dali_query_last_scene(addr) == 1
    assert await p.dali_query_last_scene_is_current(addr) is True
    assert await p.dali_query_level(addr) == 80

    levels = await p.query_scene_levels_by_address(addr)
    assert levels[0] == 180
    assert levels[1] == 80
    assert await p.query_scene_numbers_by_address(addr) == [0, 1, 2, 8, 9]


@pytest.mark.asyncio
async def test_group_scene_recall(live_sim):
    p = live_sim.protocol
    g0 = live_sim.group(0)

    scenes = await p.query_scene_numbers_for_group(g0)
    assert 0 in scenes and 1 in scenes
    assert await p.query_scene_label_for_group(g0, 1) == "Relax"

    assert await p.dali_scene(g0, 1) is True
    assert live_sim.world.groups[0].last_scene == 1
    assert live_sim.world.lights[0].level == 80
    assert live_sim.world.lights[1].level == 100


@pytest.mark.asyncio
async def test_colour_tc_and_rgb(live_sim):
    p = live_sim.protocol

    tc = ZenColour(type=ZenColourType.TC, kelvin=4000)
    assert await p.dali_colour(live_sim.ecg(0), tc) is True
    queried = await p.query_dali_colour(live_sim.ecg(0))
    assert queried is not None
    assert queried.type == ZenColourType.TC
    assert queried.kelvin == 4000

    rgb = ZenColour(type=ZenColourType.RGBWAF, r=10, g=20, b=30, w=0, a=0, f=0)
    assert await p.dali_colour(live_sim.ecg(2), rgb, level=128) is True
    queried_rgb = await p.query_dali_colour(live_sim.ecg(2))
    assert queried_rgb is not None
    assert queried_rgb.r == 10 and queried_rgb.g == 20 and queried_rgb.b == 30
    assert await p.dali_query_level(live_sim.ecg(2)) == 128


@pytest.mark.asyncio
async def test_profiles_and_system_variables(live_sim):
    p, c = live_sim.protocol, live_sim.controller

    numbers = await p.query_profile_numbers(c)
    assert numbers is not None
    assert {1, 2, 3}.issubset(set(numbers))
    assert await p.query_profile_label(c, 2) == "Night"
    assert await p.query_current_profile_number(c) == 1
    assert await p.change_profile_number(c, 2) is True
    assert await p.query_current_profile_number(c) == 2

    assert await p.query_system_variable_name(c, 0) == "Demo Switch"
    assert await p.set_system_variable(c, 0, 9) is True
    assert await p.query_system_variable(c, 0) == 9
    assert live_sim.world.system_variables[0].value == 9


@pytest.mark.asyncio
async def test_tpi_event_mode_and_filters(live_sim):
    p, c = live_sim.protocol, live_sim.controller

    assert await p.tpi_event_emit(
        c, ZenEventMode(enabled=True, filtering=False, unicast=False, multicast=True)
    ) is True
    assert await p.query_tpi_event_emit_state(c) is True

    await p.set_tpi_event_unicast_address(c, ipaddr="127.0.0.1", port=6970)
    info = await p.query_tpi_event_unicast_address(c)
    assert info is not None
    assert info["port"] == 6970

    addr = live_sim.ecg(0)
    mask = ZenEventMask(level_change_v2=True)
    assert await p.dali_add_tpi_event_filter(addr, mask) is True
    assert await p.query_dali_tpi_event_filters(addr)
    assert await p.dali_clear_tpi_event_filter(addr, mask) is True


@pytest.mark.asyncio
async def test_level_change_event_via_protocol(live_sim):
    p = live_sim.protocol
    events: list[tuple[int, int]] = []

    async def on_level(*, address, arc_level, payload):
        events.append((address.number, arc_level))

    p.set_callbacks(level_change_callback=on_level)
    await p.start_event_monitoring()
    assert await p.dali_arc_level(live_sim.ecg(1), 55) is True
    await wait_until(
        lambda: any(n == 1 and level == 55 for n, level in events),
        message="expected level-change event for ECG 1 → 55",
    )


@pytest.mark.asyncio
async def test_injected_button_and_occupancy_events(live_sim):
    p = live_sim.protocol
    buttons: list[tuple[int, int]] = []
    occupied: list[tuple[int, int]] = []

    async def on_button(*, instance, payload):
        buttons.append((instance.address.number, instance.number))

    async def on_occ(*, instance, payload):
        occupied.append((instance.address.number, instance.number))

    p.set_callbacks(
        button_press_callback=on_button,
        is_occupied_callback=on_occ,
    )
    await p.start_event_monitoring()
    live_sim.sim.inject_button_press(0, 0)
    live_sim.sim.inject_occupancy(0, 2, occupied=True)
    await wait_until(
        lambda: any(ecd == 0 and inst == 0 for ecd, inst in buttons)
        and any(ecd == 0 and inst == 2 for ecd, inst in occupied),
        message="expected injected button and occupancy events",
    )


@pytest.mark.asyncio
async def test_injected_absolute_input_event(live_sim):
    p = live_sim.protocol
    events: list[tuple[int, int, bytes]] = []

    async def on_absolute(*, instance, payload):
        events.append((instance.address.number, instance.number, bytes(payload)))

    p.set_callbacks(absolute_input_callback=on_absolute)
    await p.start_event_monitoring()
    live_sim.sim.inject_absolute_input(13, 0, 4660)
    await wait_until(
        lambda: any(
            ecd == 13 and inst == 0 and payload == bytes([0, 0x12, 0x34])
            for ecd, inst, payload in events
        ),
        message="expected injected absolute-input event",
    )


@pytest.mark.asyncio
async def test_query_absolute_input_instance(live_sim):
    """Demo ECD 13 exposes a discoverable absolute_input instance."""
    from zencontrol import ZenAddress, ZenAddressType, ZenInstanceType

    p = live_sim.protocol
    c = live_sim.controller
    addr = ZenAddress(controller=c, type=ZenAddressType.ECD, number=13)
    instances = await p.query_instances_by_address(address=addr)
    assert any(
        inst.number == 0 and inst.type == ZenInstanceType.ABSOLUTE_INPUT
        for inst in instances
    )


@pytest.mark.asyncio
async def test_timeout_when_simulator_stopped(live_sim):
    from zencontrol import ZenTimeoutError

    p, c = live_sim.protocol, live_sim.controller
    # Use a non-cacheable query so the second call must hit the wire.
    assert await p.query_system_variable(c, 0) is not None
    await live_sim.sim.stop()
    with pytest.raises(ZenTimeoutError):
        await p.query_system_variable(c, 0)


# ---------------------------------------------------------------------------
# Step / recall / fade / inhibit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_step_up_down_and_step_down_off(live_sim):
    p = live_sim.protocol
    addr = live_sim.ecg(1)

    await p.dali_arc_level(addr, 10)
    assert await p.dali_up(addr) is True
    assert await p.dali_query_level(addr) == 11
    assert await p.dali_down(addr) is True
    assert await p.dali_query_level(addr) == 10

    await p.dali_arc_level(addr, 1)
    assert await p.dali_down(addr) is True  # stay at min
    assert await p.dali_query_level(addr) == 1
    assert await p.dali_step_down_off(addr) is True
    assert await p.dali_query_level(addr) == 0

    # UP must not ignite from off
    assert await p.dali_up(addr) is True
    assert await p.dali_query_level(addr) == 0


@pytest.mark.asyncio
async def test_recall_max_min_and_last_active(live_sim):
    p = live_sim.protocol
    addr = live_sim.ecg(1)
    live_sim.world.lights[1].max_level = 200
    live_sim.world.lights[1].min_level = 5

    assert await p.dali_recall_max(addr) is True
    assert await p.dali_query_level(addr) == 200
    assert await p.dali_recall_min(addr) is True
    assert await p.dali_query_level(addr) == 5

    await p.dali_arc_level(addr, 88)
    await p.dali_off(addr)
    assert await p.dali_go_to_last_active_level(addr) is True
    assert await p.dali_query_level(addr) == 88


@pytest.mark.asyncio
async def test_min_max_level_queries(live_sim):
    p = live_sim.protocol
    addr = live_sim.ecg(0)
    assert await p.dali_query_min_level(addr) == live_sim.world.lights[0].min_level
    assert await p.dali_query_max_level(addr) == live_sim.world.lights[0].max_level


@pytest.mark.asyncio
async def test_inhibit_and_group_inhibit(live_sim):
    p = live_sim.protocol
    addr = live_sim.ecg(1)
    assert await p.dali_inhibit(addr, 10) is True
    assert live_sim.world.lights[1].is_inhibited() is True
    assert await p.dali_inhibit(addr, 0) is True
    assert live_sim.world.lights[1].is_inhibited() is False

    g0 = live_sim.group(0)
    assert await p.dali_inhibit(g0, 15) is True
    assert live_sim.world.groups[0].is_inhibited() is True
    assert live_sim.world.lights[0].is_inhibited() is True
    assert await p.dali_inhibit(g0, 0) is True
    assert live_sim.world.groups[0].is_inhibited() is False


@pytest.mark.asyncio
async def test_custom_fade_stop_and_auto_complete(live_sim):
    p = live_sim.protocol
    addr = live_sim.ecg(1)
    await p.dali_arc_level(addr, 0)
    assert await p.dali_custom_fade(addr, 100, 5) is True
    p.cache.clear()
    status = await p.dali_query_control_gear_status(addr)
    assert status is not None
    assert status["fade_running"] is True
    level = await p.dali_query_level(addr)
    assert level is not None and 0 <= level <= 100

    assert await p.dali_stop_fade(addr) is True
    p.cache.clear()
    status2 = await p.dali_query_control_gear_status(addr)
    assert status2 is not None
    assert status2["fade_running"] is False

    await p.dali_arc_level(addr, 0)
    assert await p.dali_custom_fade(addr, 50, 1) is True
    await wait_until(
        lambda: live_sim.world.lights[1].level == 50
        and not (live_sim.world.lights[1].status & 0x10),
        timeout=2.0,
        message="expected fade to complete at level 50",
    )
    p.cache.clear()
    assert await p.dali_query_level(addr) == 50


@pytest.mark.asyncio
async def test_dapc_sequence_is_no_answer(live_sim):
    p = live_sim.protocol
    # PDF: DAPC replies NO_ANSWER (legacy); library maps that to None.
    assert await p.dali_enable_dapc_sequence(live_sim.ecg(1)) is None


# ---------------------------------------------------------------------------
# Colour XY / scenes 8–11 / dimmer colour membership
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_colour_xy_set_and_query(live_sim):
    p = live_sim.protocol
    addr = live_sim.ecg(3)
    features = await p.query_dali_colour_features(addr)
    assert features is not None
    assert features.get("supports_xy") is True

    colour = ZenColour(type=ZenColourType.XY, x=12345, y=23456)
    assert await p.dali_colour(addr, colour, level=90) is True
    queried = await p.query_dali_colour(addr)
    assert queried is not None
    assert queried.type == ZenColourType.XY
    assert queried.x == 12345 and queried.y == 23456
    assert await p.dali_query_level(addr) == 90
    assert live_sim.world.lights[3].colour.x == 12345


@pytest.mark.asyncio
async def test_colour_scenes_include_8_11(live_sim):
    p = live_sim.protocol
    addr = live_sim.ecg(0)
    membership = await p.query_colour_scene_membership_by_address(addr)
    assert 0 in membership and 1 in membership
    assert 8 in membership and 9 in membership

    colours = await p.query_scene_colours_by_address(addr)
    assert colours[0] is not None and colours[0].kelvin == 3000
    assert colours[8] is not None and colours[8].kelvin == 4500
    assert colours[9] is not None and colours[9].kelvin == 5500

    levels = await p.query_scene_levels_by_address(addr)
    assert levels[8] == 160
    assert levels[9] == 40

    assert await p.dali_scene(addr, 8) is True
    assert await p.dali_query_level(addr) == 160
    queried = await p.query_dali_colour(addr)
    assert queried is not None and queried.kelvin == 4500


@pytest.mark.asyncio
async def test_empty_colour_membership_for_dimmer(live_sim):
    p = live_sim.protocol
    membership = await p.query_colour_scene_membership_by_address(live_sim.ecg(1))
    assert membership == [] or membership is None


# ---------------------------------------------------------------------------
# Broadcast / group status / labels / fitting numbers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_broadcast_arc_off_scene_and_colour(live_sim):
    p = live_sim.protocol
    bcast = live_sim.broadcast()

    assert await p.dali_arc_level(bcast, 33) is True
    assert all(lt.level == 33 for lt in live_sim.world.lights.values())

    assert await p.dali_off(bcast) is True
    assert all(lt.level == 0 for lt in live_sim.world.lights.values())

    assert await p.dali_scene(bcast, 0) is True
    assert live_sim.world.lights[0].level == 180
    assert live_sim.world.groups[0].last_scene == 0

    tc = ZenColour(type=ZenColourType.TC, kelvin=4200)
    assert await p.dali_colour(bcast, tc) is True
    assert live_sim.world.lights[0].colour.kelvin == 4200
    assert live_sim.world.lights[3].colour.type == "tc"
    assert live_sim.world.lights[3].colour.kelvin == 4200


@pytest.mark.asyncio
async def test_group_last_scene_and_status(live_sim):
    p = live_sim.protocol
    g0 = live_sim.group(0)
    assert await p.dali_scene(g0, 1) is True
    assert await p.dali_query_last_scene(g0) == 1
    assert await p.dali_query_last_scene_is_current(g0) is True

    await p.dali_arc_level(live_sim.ecg(0), 10)
    assert await p.dali_query_last_scene_is_current(g0) is False

    await p.dali_arc_level(live_sim.ecg(1), 0)
    assert await p.dali_custom_fade(live_sim.ecg(1), 80, 5) is True
    p.cache.clear()
    status = await p.dali_query_control_gear_status(g0)
    assert status is not None
    assert status["fade_running"] is True


@pytest.mark.asyncio
async def test_group_by_number_and_scenes_list(live_sim):
    p = live_sim.protocol
    live_sim.world.lights[0].set_level(10)
    live_sim.world.lights[1].set_level(77)
    info = await p.query_group_by_number(live_sim.group(0))
    assert info == (0, True, 77)
    assert await p.query_group_by_number(live_sim.group(15)) is None

    scenes = await p.query_scenes_for_group(live_sim.group(0), generic_if_none=False)
    assert scenes[1] == "Relax"


@pytest.mark.asyncio
async def test_instance_ecd_labels_and_fitting_numbers(live_sim):
    p = live_sim.protocol
    assert await p.query_dali_device_label(live_sim.ecd(0)) == "Living Room Switch"
    assert await p.query_dali_device_label(live_sim.ecd(1)) == "Kitchen Switch"
    assert await p.query_dali_instance_label(live_sim.instance(0, 0)) == "On/Off"
    assert await p.query_dali_instance_label(live_sim.instance(0, 2, type_code=3)) == "Motion"
    assert await p.query_dali_instance_label(live_sim.instance(1, 0)) == "Kitchen Toggle"

    addr = live_sim.ecg(3)
    assert await p.query_dali_device_label(addr) == "XY Spotlight"
    assert await p.query_dali_serial(addr) == 0x0100000000000004
    ean = await p.query_dali_ean(addr)
    assert ean == 10_000_000_000 + 3
    assert await p.query_dali_fitting_number(addr) == "1.3"
    assert await p.query_dali_fitting_number(live_sim.ecd(0)) == "1.100"
    assert await p.query_controller_fitting_number(live_sim.controller) == "1"
    assert (
        await p.query_dali_instance_fitting_number(live_sim.instance(4, 2))
        == "1.104.2"
    )
    groups = await p.query_group_membership_by_address(addr)
    assert groups == [] or groups is None or list(groups) == []


@pytest.mark.asyncio
async def test_occupancy_timers(live_sim):
    p = live_sim.protocol
    inst = live_sim.instance(0, 2, type_code=3)
    timers = await p.query_occupancy_instance_timers(inst)
    assert timers is not None
    assert timers["hold"] == 60
    assert timers["deadtime"] == 1
    assert "last_detect" in timers


@pytest.mark.asyncio
async def test_readiness_flags_and_unknown_sysvars(live_sim):
    p, c = live_sim.protocol, live_sim.controller

    live_sim.world.startup_complete = False
    assert await p.query_controller_startup_complete(c) is not True
    live_sim.world.startup_complete = True
    live_sim.world.dali_ready = False  # simulator ignores — DALI always ready
    assert await p.query_is_dali_ready(c) is True

    assert await p.query_system_variable_name(c, 99) is None
    assert await p.query_system_variable(c, 99) is None
    assert await p.set_system_variable(c, 99, 1) is None


@pytest.mark.asyncio
async def test_return_to_scheduled_profile(live_sim):
    p, c = live_sim.protocol, live_sim.controller
    live_sim.world.last_scheduled_profile = 1
    assert await p.change_profile_number(c, 3) is True
    assert await p.return_to_scheduled_profile(c) is True
    assert await p.query_current_profile_number(c) == 1


@pytest.mark.asyncio
async def test_dali_query_fade_running(live_sim):
    p = live_sim.protocol
    addr = live_sim.ecg(1)
    await p.dali_arc_level(addr, 0)
    assert await p.dali_query_fade_running(addr) is False
    assert await p.dali_custom_fade(addr, 100, 5) is True
    p.cache.clear()
    assert await p.dali_query_fade_running(addr) is True
    assert await p.dali_stop_fade(addr) is True
    p.cache.clear()
    assert await p.dali_query_fade_running(addr) is False


@pytest.mark.asyncio
async def test_operating_mode_and_button_led_stubs(live_sim):
    p = live_sim.protocol
    assert await p.query_operating_mode_by_address(live_sim.ecg(0)) == 0
    assert await p.query_operating_mode_by_address(live_sim.ecd(0)) == 0

    button = live_sim.instance(0, 0)
    assert await p.override_dali_button_led_state(button, True) is True
    # Sim stub always reports last-known LED off.
    assert await p.query_last_known_dali_button_led_state(button) is False


@pytest.mark.asyncio
async def test_query_instance_groups(live_sim):
    p = live_sim.protocol
    groups = await p.query_instance_groups(live_sim.instance(0, 0))
    assert groups == (0, 1, None)
    motion = await p.query_instance_groups(live_sim.instance(0, 2, type_code=3))
    assert motion == (0, None, None)
    # Unconfigured instance → all None
    unset = await p.query_instance_groups(live_sim.instance(1, 0))
    assert unset == (None, None, None)


@pytest.mark.asyncio
async def test_query_profile_information(live_sim):
    """PDF: header + records; bit0=disabled, bits1–2=priority (0 scheduled, 1+, …)."""
    p, c = live_sim.protocol, live_sim.controller
    result = await p.query_profile_information(c)
    assert result is not None
    state, profiles = result
    assert state["current_active_profile"] == 1
    assert state["last_scheduled_profile"] == 1
    assert state["last_overridden_profile_utc"].timestamp() == 0x22334455
    assert state["last_scheduled_profile_utc"].timestamp() == 0x44556677
    assert set(profiles) == {1, 2, 3}
    # Assert protocol fields (enabled/priority); labels are library niceties.
    assert profiles[1]["enabled"] is True and profiles[1]["priority"] == 0
    assert profiles[2]["enabled"] is True and profiles[2]["priority"] == 1
    assert profiles[3]["enabled"] is False and profiles[3]["priority"] == 0


@pytest.mark.asyncio
async def test_colour_only_preserves_level(live_sim):
    p = live_sim.protocol
    addr = live_sim.ecg(0)
    assert await p.dali_arc_level(addr, 77) is True
    colour = ZenColour(type=ZenColourType.TC, kelvin=4200)
    assert await p.dali_colour(addr, colour, level=255) is True
    assert await p.dali_query_level(addr) == 77
    assert live_sim.world.lights[0].level == 77
    queried = await p.query_dali_colour(addr)
    assert queried is not None and queried.kelvin == 4200


@pytest.mark.asyncio
async def test_clear_tpi_event_unicast_address(live_sim):
    """Sim extension: omit IP/port (zeros) clears unicast targeting."""
    p, c = live_sim.protocol, live_sim.controller
    await p.set_tpi_event_unicast_address(c, ipaddr="127.0.0.1", port=6970)
    await p.set_tpi_event_unicast_address(c)
    info = await p.query_tpi_event_unicast_address(c)
    assert info is not None
    assert info["port"] == 0
    assert info["ip"] == "0.0.0.0"
