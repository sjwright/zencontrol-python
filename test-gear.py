from zen import ZenProtocol, ZenController
import yaml

config = yaml.safe_load(open("config.yaml"))
ctrl = ZenController(**config.get('zencontrol')[0])
tpi = ZenProtocol(controllers=[ctrl])


gears = tpi.query_control_gear_dali_addresses(ctrl)
print(f"Control gears")    

for gear in gears:
    print(f"  {gear}")

    level = tpi.dali_query_level(ctrl, gear=gear)
    print(f"    current level: {level}")

    label = tpi.query_dali_device_label(ctrl, gear=gear)
    print(f"    label: {label}")
    
    type = tpi.dali_query_cg_type(ctrl, gear=gear)
    print(f"    type: {type}")

    groups = tpi.query_group_membership_by_address(ctrl, gear=gear)
    print(f"    groups: {groups}")

    scenes = tpi.query_scene_numbers_by_address(ctrl, gear=gear)
    print(f"    scenes: {scenes}")

    levels = tpi.query_scene_levels_by_address(ctrl, gear=gear)
    print(f"    scene levels: {levels}")

    colour = tpi.query_dali_colour(ctrl, gear=gear)
    print(f"    colour: {colour}")
    
    cgtype = tpi.query_dali_colour_features(ctrl, gear=gear)
    print(f"    colour features: {cgtype}")

    colour_temp_limits = tpi.query_dali_colour_temp_limits(ctrl, gear=gear)
    print(f"    colour temp limits: {colour_temp_limits}")

    fitting = tpi.query_dali_fitting_number(ctrl, gear=gear)
    print(f"    fitting: {fitting}")

    ean = tpi.query_dali_ean(ctrl, gear=gear)
    print(f"    ean: {ean}")

    serial = tpi.query_dali_serial(ctrl, gear=gear)
    print(f"    serial: {serial}")

    last_scene = tpi.dali_query_last_scene(ctrl, gear=gear)
    print(f"    last scene: {last_scene}")

    last_scene_is_current = tpi.dali_query_last_scene_is_current(ctrl, gear=gear)
    print(f"    last scene is current: {last_scene_is_current}")

    status = tpi.dali_query_control_gear_status(ctrl, gear=gear)
    print(f"    status: {status}")

