"""High-level ZenControl integration tests against zencontrol-simulator."""

from __future__ import annotations

import pytest
from helpers import LEGACY_ACK, wait_until

from zencontrol import ZenColour, ZenColourType

pytestmark = pytest.mark.simulator


@pytest.mark.asyncio
async def test_interview_discovers_entities(live_zen):
    zen, live_sim = live_zen
    ctrl = zen.controllers[0]

    assert await ctrl.is_controller_ready()
    await ctrl.interview()

    lights = await zen.get_lights()
    groups = await zen.get_groups()
    buttons = await zen.get_buttons()
    absolute_inputs = await zen.get_absolute_inputs()
    sensors = await zen.get_motion_sensors()
    profiles = await zen.get_profiles()
    sysvars = await zen.get_system_variables(give_up_after=5)

    assert len(lights) == 12
    assert len(groups) == 6
    assert len(buttons) >= 9
    assert len(absolute_inputs) >= 1
    assert len(sensors) >= 2
    assert len(profiles) == 3
    assert len(sysvars) >= 2

    by_addr = {lt.address.number: lt for lt in lights}
    assert by_addr[0].features.get("temperature") is True
    assert by_addr[1].features.get("brightness") is True
    assert by_addr[2].features.get("RGB") is True

    assert any(getattr(b, "instance_label", None) == "On/Off" for b in buttons)
    assert any(getattr(s, "instance_label", None) == "Motion" for s in sensors)
    assert any(getattr(b, "label", None) == "Living Room Switch" for b in buttons)
    assert any(getattr(s, "label", None) == "Porch Sensor" for s in sensors)
    slider = next(
        a
        for a in absolute_inputs
        if a.instance.address.number == 13 and a.instance.number == 0
    )
    assert slider.instance_label == "Slider"
    assert slider.value is None

    # World state still matches what we interviewed
    assert live_sim.world.lights[0].label == "Living Room Ceiling"


@pytest.mark.asyncio
async def test_light_set_and_query(live_zen):
    zen, live_sim = live_zen
    ctrl = zen.controllers[0]
    await ctrl.interview()

    lights = {lt.address.number: lt for lt in await zen.get_lights()}
    light = lights[1]

    assert await light.set(level=50, fade=True) is LEGACY_ACK
    assert await zen.commands.dali_query_level(light.address) == 50
    assert live_sim.world.lights[1].level == 50

    assert await light.off(fade=False) is LEGACY_ACK
    assert await zen.commands.dali_query_level(light.address) == 0


@pytest.mark.asyncio
async def test_tunable_colour_via_interface(live_zen):
    zen, live_sim = live_zen
    ctrl = zen.controllers[0]
    await ctrl.interview()

    lights = {lt.address.number: lt for lt in await zen.get_lights()}
    tc = ZenColour(type=ZenColourType.TC, kelvin=4000)
    assert await lights[0].set(colour=tc) is True
    assert live_sim.world.lights[0].colour is not None
    assert live_sim.world.lights[0].colour.kelvin == 4000

    queried = await zen.commands.query_dali_colour(lights[0].address)
    assert queried is not None
    assert queried.kelvin == 4000


@pytest.mark.asyncio
async def test_group_scene_and_profile_switch(live_zen):
    zen, live_sim = live_zen
    ctrl = zen.controllers[0]
    await ctrl.interview()
    await zen.get_profiles()

    groups = await zen.get_groups()
    group = next(g for g in groups if g.address.number == 0)
    assert await group.set_scene(1) is LEGACY_ACK
    assert live_sim.world.groups[0].last_scene == 1
    assert live_sim.world.lights[0].level == 80
    assert live_sim.world.lights[1].level == 100

    assert await ctrl.switch_to_profile(2) is True
    assert live_sim.world.current_profile == 2


@pytest.mark.asyncio
async def test_system_variable_set_via_interface(live_zen):
    zen, live_sim = live_zen
    ctrl = zen.controllers[0]
    await ctrl.interview()

    sysvars = await zen.get_system_variables(give_up_after=5)
    svar = next(v for v in sysvars if v.id == 0)
    await svar.set_value(42)
    assert live_sim.world.system_variables[0].value == 42
    assert await zen.commands.query_system_variable(ctrl, 0) == 42


@pytest.mark.asyncio
async def test_start_receives_injected_and_control_events(live_zen):
    zen, live_sim = live_zen
    ctrl = zen.controllers[0]
    await ctrl.interview()

    button_events: list = []
    hold_events: list = []
    motion_events: list = []
    absolute_events: list = []
    profile_events: list = []
    sysvar_events: list = []
    colour_events: list = []
    light_events: list = []
    group_events: list = []

    async def on_button(button):
        button_events.append(button)

    async def on_hold(button):
        hold_events.append(button)

    async def on_motion(sensor, occupied):
        motion_events.append((sensor, occupied))

    async def on_absolute(absolute_input, value):
        absolute_events.append((absolute_input, value))

    async def on_profile(profile):
        profile_events.append(profile)

    async def on_sysvar(system_variable, value, changed, by_me):
        sysvar_events.append((system_variable.id, value))

    async def on_light(*, light, level=None, colour=None, scene=None, **kwargs):
        light_events.append((light.address.number, level, colour, scene))
        if colour is not None:
            colour_events.append((light.address.number, colour))

    async def on_group(*, group, level=None, colour=None, scene=None, **kwargs):
        group_events.append((group.address.number, level, colour, scene))

    zen.button_press = on_button
    zen.button_long_press = on_hold
    zen.motion_event = on_motion
    zen.absolute_input_change = on_absolute
    zen.profile_change = on_profile
    zen.system_variable_change = on_sysvar
    zen.light_change = on_light
    zen.group_change = on_group

    await zen.start()
    await zen.get_profiles()

    lights = {lt.address.number: lt for lt in await zen.get_lights()}
    assert await lights[1].set(level=66) is LEGACY_ACK
    await wait_until(
        lambda: any(n == 1 and level == 66 for n, level, _, _ in light_events),
        message="expected light_change for ECG 1 → 66",
    )

    tc = ZenColour(type=ZenColourType.TC, kelvin=3500)
    assert await lights[0].set(colour=tc) is True
    await wait_until(
        lambda: any(n == 0 for n, _ in colour_events),
        message="expected colour_change for ECG 0",
    )

    groups = {g.address.number: g for g in await zen.get_groups()}
    assert await groups[0].set(level=55) is LEGACY_ACK
    await wait_until(
        lambda: any(n == 0 and level == 55 for n, level, _, _ in group_events),
        message="expected group_change for group 0 → 55",
    )

    assert await ctrl.switch_to_profile(3) is True
    await wait_until(
        lambda: len(profile_events) >= 1,
        message="expected profile_change callback",
    )

    sysvars = await zen.get_system_variables(give_up_after=5)
    svar = next(v for v in sysvars if v.id == 1)
    await svar.set_value(111)
    await wait_until(
        lambda: any(vid == 1 and val == 111 for vid, val in sysvar_events),
        message="expected system_variable_change for id 1",
    )

    live_sim.sim.inject_button_press(0, 0)
    # Interface long-press fires after Const.LONG_PRESS_COUNT (2) holds.
    live_sim.sim.inject_button_hold(0, 1)
    live_sim.sim.inject_button_hold(0, 1)
    live_sim.sim.inject_occupancy(0, 2, occupied=True)
    live_sim.sim.inject_absolute_input(13, 0, 0x1234)
    await wait_until(
        lambda: (
            len(button_events) >= 1
            and len(hold_events) >= 1
            and len(motion_events) >= 1
            and any(value == 0x1234 for _, value in absolute_events)
        ),
        message="expected injected button/hold/motion/absolute-input callbacks",
    )
    assert absolute_events[0][0].value == 0x1234
    assert absolute_events[0][0].instance.address.number == 13

    await zen.stop()


@pytest.mark.asyncio
async def test_rgb_and_xy_via_interface(live_zen):
    zen, live_sim = live_zen
    ctrl = zen.controllers[0]
    await ctrl.interview()
    lights = {lt.address.number: lt for lt in await zen.get_lights()}

    assert lights[2].features.get("RGB") is True
    rgb = ZenColour(type=ZenColourType.RGBWAF, r=1, g=2, b=3, w=0, a=0, f=0)
    assert await lights[2].set(colour=rgb, level=100) is True
    assert live_sim.world.lights[2].colour.r == 1
    assert live_sim.world.lights[2].level == 100

    assert lights[3].features.get("XY") is True
    xy = ZenColour(type=ZenColourType.XY, x=15000, y=16000)
    assert lights[3].supports_colour(xy) is True
    assert await lights[3].set(colour=xy, level=90) is True
    assert live_sim.world.lights[3].colour.x == 15000
    assert live_sim.world.lights[3].level == 90


@pytest.mark.asyncio
async def test_light_scene_and_on_off_via_interface(live_zen):
    zen, live_sim = live_zen
    ctrl = zen.controllers[0]
    await ctrl.interview()
    lights = {lt.address.number: lt for lt in await zen.get_lights()}
    light = lights[0]

    assert await light.set_scene(1) is LEGACY_ACK
    assert live_sim.world.lights[0].level == 80
    assert await zen.commands.dali_query_last_scene(light.address) == 1

    assert await light.set(level=120) is LEGACY_ACK
    assert await light.off(fade=False) is LEGACY_ACK
    assert live_sim.world.lights[0].level == 0
    assert await light.on(fade=False) is LEGACY_ACK
    assert live_sim.world.lights[0].level == 120


@pytest.mark.asyncio
async def test_group_scene_by_label_and_level(live_zen):
    zen, live_sim = live_zen
    ctrl = zen.controllers[0]
    await ctrl.interview()
    groups = {g.address.number: g for g in await zen.get_groups()}
    group = groups[0]

    assert group.get_scene_number_from_label("Relax") == 1
    assert await group.set_scene("Relax") is LEGACY_ACK
    assert live_sim.world.groups[0].last_scene == 1
    assert live_sim.world.lights[0].level == 80

    assert await group.set(level=40) is LEGACY_ACK
    assert live_sim.world.lights[0].level == 40
    assert live_sim.world.lights[1].level == 40


@pytest.mark.asyncio
async def test_profile_select_and_return_scheduled(live_zen):
    zen, live_sim = live_zen
    ctrl = zen.controllers[0]
    await ctrl.interview()
    profiles = {p.number: p for p in await zen.get_profiles()}
    live_sim.world.last_scheduled_profile = 1

    assert await profiles[2].select() is True
    assert live_sim.world.current_profile == 2
    assert await ctrl.return_to_scheduled_profile() is True
    assert live_sim.world.current_profile == 1


@pytest.mark.asyncio
async def test_light_steps_recall_and_inhibit_via_interface(live_zen):
    zen, live_sim = live_zen
    ctrl = zen.controllers[0]
    await ctrl.interview()
    lights = {lt.address.number: lt for lt in await zen.get_lights()}
    light = lights[1]
    live_sim.world.lights[1].max_level = 200
    live_sim.world.lights[1].min_level = 5

    assert await light.set(level=10) is LEGACY_ACK
    assert await light.dali_up() is LEGACY_ACK
    assert live_sim.world.lights[1].level == 11
    assert await light.dali_down() is LEGACY_ACK
    assert live_sim.world.lights[1].level == 10

    assert await light.dali_recall_max() is LEGACY_ACK
    assert live_sim.world.lights[1].level == 200
    assert await light.dali_recall_min() is LEGACY_ACK
    assert live_sim.world.lights[1].level == 5

    assert await light.dali_inhibit(True) is True
    assert live_sim.world.lights[1].is_inhibited() is True
    assert await light.dali_inhibit(False) is True
    assert live_sim.world.lights[1].is_inhibited() is False


@pytest.mark.asyncio
async def test_button_and_sensor_interview_fields(live_zen):
    zen, _live_sim = live_zen
    ctrl = zen.controllers[0]
    await ctrl.interview()

    buttons = await zen.get_buttons()
    sensors = await zen.get_motion_sensors()
    living = next(
        b
        for b in buttons
        if b.label == "Living Room Switch" and b.instance_label == "On/Off"
    )
    assert living.instance.number == 0
    porch = next(s for s in sensors if s.label == "Porch Sensor")
    assert porch.instance_label == "Porch Motion"

    # Entrance 6-button pad should contribute multiple button instances
    entrance = [b for b in buttons if b.label == "Entrance 6-Button"]
    assert len(entrance) == 6


@pytest.mark.asyncio
async def test_light_fade_step_and_refresh_via_interface(live_zen):
    zen, live_sim = live_zen
    ctrl = zen.controllers[0]
    await ctrl.interview()
    lights = {lt.address.number: lt for lt in await zen.get_lights()}
    light = lights[1]

    assert await light.set(level=0, fade=False) is True
    assert await light.dali_on_step_up() is LEGACY_ACK
    assert live_sim.world.lights[1].level >= 1

    assert await light.set(level=50, fade=False) is True
    assert await light.dali_custom_fade(100, 5) is True
    assert live_sim.world.lights[1].status & 0x10
    assert await light.dali_stop_fade() is True
    assert not (live_sim.world.lights[1].status & 0x10)

    live_sim.world.lights[1].last_active_level = 88
    assert await light.dali_go_to_last_active_level() is LEGACY_ACK
    assert live_sim.world.lights[1].level == 88

    assert await light.set(level=40, fade=False) is True
    assert await light.dali_step_down_off() is LEGACY_ACK
    assert live_sim.world.lights[1].level == 39

    assert await light.dali_enable_dapc_sequence() is None
    assert await light.dali_off() is LEGACY_ACK
    assert live_sim.world.lights[1].level == 0

    # Mutate controller under the entity, then refresh entity state.
    assert await zen.commands.dali_arc_level(light.address, 123) is LEGACY_ACK
    light.level = None
    await light.refresh_state_from_controller()
    assert light.level == 123


@pytest.mark.asyncio
async def test_sysvar_get_value_and_refresh(live_zen):
    zen, live_sim = live_zen
    ctrl = zen.controllers[0]
    await ctrl.interview()
    assert await ctrl.is_dali_ready() is True

    sysvars = await zen.get_system_variables(give_up_after=5)
    svar = next(v for v in sysvars if v.id == 0)
    svar._value = None
    live_sim.world.system_variables[0].value = 55
    assert await svar.get_value() == 55

    live_sim.world.system_variables[0].value = 66
    await svar.refresh_state_from_controller()
    assert svar.value == 66


@pytest.mark.asyncio
async def test_motion_refresh_and_group_discoordination(live_zen):
    zen, live_sim = live_zen
    ctrl = zen.controllers[0]
    await ctrl.interview()

    sensors = await zen.get_motion_sensors()
    porch = next(s for s in sensors if s.label == "Porch Sensor")
    world_inst = live_sim.world.instance(10, 0)
    assert world_inst is not None and world_inst.timers is not None
    world_inst.timers.last_motion_at = __import__("time").time() - 5
    assert await porch.refresh_state_from_controller() is True
    assert porch.last_detect is not None
    assert porch.hold_time == 60

    groups = {g.address.number: g for g in await zen.get_groups()}
    group = groups[0]
    group.level = 40
    group.scene = 1
    disco: list = []

    async def on_group(*, group, discoordinated=False, **kwargs):
        if discoordinated:
            disco.append(group.address.number)

    zen.group_change = on_group
    await group.declare_discoordination()
    assert group.level is None and group.scene is None
    assert 0 in disco
