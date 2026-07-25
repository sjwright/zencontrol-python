"""Event-path integration tests against zencontrol-simulator."""

from __future__ import annotations

import pytest

from zencontrol import ZenColour, ZenColourType

from helpers import LEGACY_ACK, wait_until

pytestmark = pytest.mark.simulator


@pytest.mark.asyncio
async def test_scene_and_colour_events_via_protocol(live_sim):
    p = live_sim.protocol
    scenes: list[tuple[str, int, int]] = []
    colours: list[tuple[int, object]] = []

    async def on_scene(*, address, scene, active, payload):
        scenes.append((address.type.name, address.number, scene))

    async def on_colour(*, address, colour, payload):
        colours.append((address.number, colour))

    p.set_callbacks(scene_change_callback=on_scene, colour_change_callback=on_colour)
    await p.start_event_monitoring()

    assert await p.dali_scene(live_sim.ecg(0), 1) is LEGACY_ACK
    await wait_until(
        lambda: any(t == "ECG" and n == 0 and s == 1 for t, n, s in scenes),
        message="expected scene-change event for ECG 0 → scene 1",
    )

    tc = ZenColour(type=ZenColourType.TC, kelvin=3500)
    assert await p.dali_colour(live_sim.ecg(0), tc) is True
    await wait_until(
        lambda: any(
            n == 0 and c is not None and getattr(c, "kelvin", None) == 3500
            for n, c in colours
        ),
        message="expected colour-change event for ECG 0 → 3500K",
    )


@pytest.mark.asyncio
async def test_group_level_event_via_protocol(live_sim):
    p = live_sim.protocol
    group_events: list[tuple[int, int]] = []

    async def on_level(*, address, arc_level, payload):
        # Required gate in zencontrol-python for group LEVEL_CHANGE_V2 dispatch.
        pass

    async def on_group_level(*, address, arc_level, payload):
        group_events.append((address.number, arc_level))

    p.set_callbacks(
        level_change_callback=on_level,
        group_level_change_callback=on_group_level,
    )
    await p.start_event_monitoring()

    assert await p.dali_arc_level(live_sim.group(0), 44) is LEGACY_ACK
    await wait_until(
        lambda: any(n == 0 and level == 44 for n, level in group_events),
        message="expected group level-change event for group 0 → 44",
    )


@pytest.mark.asyncio
async def test_member_events_on_group_scene(live_sim):
    p = live_sim.protocol
    scenes: list[tuple[str, int, int]] = []
    levels: list[tuple[str, int, int]] = []

    async def on_scene(*, address, scene, active, payload):
        scenes.append((address.type.name, address.number, scene))

    async def on_level(*, address, arc_level, payload):
        levels.append((address.type.name, address.number, arc_level))

    p.set_callbacks(scene_change_callback=on_scene, level_change_callback=on_level)
    await p.start_event_monitoring()

    assert await p.dali_scene(live_sim.group(0), 1) is LEGACY_ACK
    await wait_until(
        lambda: (
            any(t == "GROUP" and n == 0 and s == 1 for t, n, s in scenes)
            and any(t == "ECG" and n == 0 and s == 1 for t, n, s in scenes)
            and any(t == "ECG" and n == 1 and s == 1 for t, n, s in scenes)
            and any(t == "ECG" and n == 0 and lv == 80 for t, n, lv in levels)
            and any(t == "ECG" and n == 1 and lv == 100 for t, n, lv in levels)
        ),
        message="expected group + member scene/level events for group 0 scene 1",
    )


@pytest.mark.asyncio
async def test_profile_and_sysvar_events_via_protocol(live_sim):
    p, c = live_sim.protocol, live_sim.controller
    profiles: list[int] = []
    sysvars: list[tuple[int, int]] = []

    async def on_profile(*, controller, profile, payload):
        profiles.append(profile)

    async def on_sysvar(*, controller, target, value, payload):
        sysvars.append((target, value))

    p.set_callbacks(
        profile_change_callback=on_profile,
        system_variable_change_callback=on_sysvar,
    )
    await p.start_event_monitoring()

    assert await p.change_profile_number(c, 3) is True
    assert await p.set_system_variable(c, 1, 111) is True
    await wait_until(
        lambda: 3 in profiles and any(vid == 1 and val == 111 for vid, val in sysvars),
        message="expected profile and sysvar change events",
    )


@pytest.mark.asyncio
async def test_button_hold_and_occupancy_inject(live_sim):
    p = live_sim.protocol
    presses: list[tuple[int, int]] = []
    holds: list[tuple[int, int]] = []
    occupied: list[tuple[int, int, bytes]] = []

    async def on_button(*, instance, payload):
        presses.append((instance.address.number, instance.number))

    async def on_hold(*, instance, payload):
        holds.append((instance.address.number, instance.number))

    async def on_occ(*, instance, payload):
        occupied.append((instance.address.number, instance.number, bytes(payload)))

    p.set_callbacks(
        button_press_callback=on_button,
        button_hold_callback=on_hold,
        is_occupied_callback=on_occ,
    )
    await p.start_event_monitoring()

    live_sim.sim.inject_button_press(0, 0)
    live_sim.sim.inject_button_hold(0, 1)
    live_sim.sim.inject_occupancy(0, 2, occupied=True)
    await wait_until(
        lambda: (
            any(ecd == 0 and inst == 0 for ecd, inst in presses)
            and any(ecd == 0 and inst == 1 for ecd, inst in holds)
            and any(ecd == 0 and inst == 2 for ecd, inst, _ in occupied)
        ),
        message="expected button press/hold and occupancy inject events",
    )


@pytest.mark.asyncio
async def test_absolute_input_inject(live_sim):
    p = live_sim.protocol
    events: list[tuple[int, int, bytes]] = []

    async def on_absolute(*, instance, payload):
        events.append((instance.address.number, instance.number, bytes(payload)))

    p.set_callbacks(absolute_input_callback=on_absolute)
    await p.start_event_monitoring()

    live_sim.sim.inject_absolute_input(13, 0, 0xABCD)
    await wait_until(
        lambda: any(
            ecd == 13 and inst == 0 and payload == bytes([0, 0xAB, 0xCD])
            for ecd, inst, payload in events
        ),
        message="expected absolute-input inject event for ECD 13",
    )
    assert live_sim.world.instance(13, 0).value == 0xABCD


@pytest.mark.asyncio
async def test_inject_level_scene_colour_profile_events(live_sim):
    from zencontrol_simulator.world import Colour

    p = live_sim.protocol
    levels: list[tuple[int, int]] = []
    scenes: list[tuple[int, int]] = []
    colours: list[tuple[int, object]] = []
    profiles: list[int] = []

    async def on_level(*, address, arc_level, payload):
        levels.append((address.number, arc_level))

    async def on_scene(*, address, scene, active, payload):
        scenes.append((address.number, scene))

    async def on_colour(*, address, colour, payload):
        colours.append((address.number, colour))

    async def on_profile(*, controller, profile, payload):
        profiles.append(profile)

    p.set_callbacks(
        level_change_callback=on_level,
        scene_change_callback=on_scene,
        colour_change_callback=on_colour,
        profile_change_callback=on_profile,
    )
    await p.start_event_monitoring()

    live_sim.sim.inject_level(1, 77)
    live_sim.sim.inject_scene(0, 1)
    live_sim.sim.inject_colour(3, Colour(type="xy", x=1111, y=2222))
    live_sim.sim.inject_profile(3)
    await wait_until(
        lambda: (
            any(n == 1 and lv == 77 for n, lv in levels)
            and any(n == 0 and s == 1 for n, s in scenes)
            and any(
                n == 3 and c is not None and getattr(c, "x", None) == 1111
                for n, c in colours
            )
            and 3 in profiles
        ),
        message="expected injected level/scene/colour/profile events",
    )
