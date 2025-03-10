from zen import ZenProtocol, ZenController
import yaml

config = yaml.safe_load(open("test-config.yaml"))
tpi = ZenProtocol(narration=True)
ctrl = ZenController(protocol=tpi, **config.get('zencontrol')[0])
tpi.set_controllers([ctrl])


addresses = tpi.query_control_gear_dali_addresses(ctrl)
print(f"Control gears")    

for address in addresses:
    print(f"  {address.number}")

    level = tpi.dali_query_level(address)
    print(f"    current level: {level}")

    label = tpi.query_dali_device_label(address)
    print(f"    label: {label}")

    groups = tpi.query_group_membership_by_address(address)
    print(f"    groups: {[group.number for group in groups]}")
    
    type = tpi.dali_query_cg_type(address)
    print(f"    type: {type}")

    scenes = tpi.query_scene_numbers_by_address(address)
    print(f"    scenes: {scenes}")

    levels = tpi.query_scene_levels_by_address(address)
    print(f"    scene levels: {levels}")

    colour = tpi.query_dali_colour(address)
    print(f"    colour: {colour}")
    
    cgtype = tpi.query_dali_colour_features(address)
    print(f"    colour features: {cgtype}")

    colour_temp_limits = tpi.query_dali_colour_temp_limits(address)
    print(f"    colour temp limits: {colour_temp_limits}")

    fitting = tpi.query_dali_fitting_number(address)
    print(f"    fitting: {fitting}")

    ean = tpi.query_dali_ean(address)
    print(f"    ean: {ean}")

    serial = tpi.query_dali_serial(address)
    print(f"    serial: {serial}")

    last_scene = tpi.dali_query_last_scene(address)
    print(f"    last scene: {last_scene}")

    last_scene_is_current = tpi.dali_query_last_scene_is_current(address)
    print(f"    last scene is current: {last_scene_is_current}")

    status = tpi.dali_query_control_gear_status(address)
    print(f"    status: {status}")

    scenes = tpi.query_colour_scene_membership_by_address(address)
    print(f"    scenes: {scenes}")

    scene_data = tpi.query_scene_colours_by_address(address)
    print(f"    scene data: {scene_data}")

