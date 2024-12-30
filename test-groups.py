from zen import ZenProtocol, ZenController
import yaml

config = yaml.safe_load(open("config.yaml"))
ctrl = ZenController(**config.get('zencontrol')[0])
tpi = ZenProtocol(controllers=[ctrl])


groups = tpi.query_group_numbers(ctrl)
print(f"Group")

for group in groups:
    print(f"  {group}")
    name = tpi.query_group_label(ctrl, group)
    print(f"    name: {name}")

    information = tpi.query_group_by_number(ctrl, group)
    print(f"    information: {information}")

    scenes = tpi.query_scene_numbers_for_group(ctrl, group)
    print(f"    scenes: {scenes}")

    for scene in scenes:
        label = tpi.query_scene_label_for_group(ctrl, scene, group)
        print(f"    Group {group} scene {scene} label: {label}")
