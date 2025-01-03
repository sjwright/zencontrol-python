from zen import ZenProtocol, ZenController, ZenAddress, ZenInstance
import yaml

config = yaml.safe_load(open("config.yaml"))
ctrl = ZenController(**config.get('zencontrol')[0])
tpi = ZenProtocol(controllers=[ctrl])


addresses = tpi.query_group_numbers(ctrl)
print(f"Group")

for address in addresses:
    print(f"  {address.number}")
    name = tpi.query_group_label(address)
    print(f"    name: {name}")

    information = tpi.query_group_by_number(address)
    print(f"    information: {information}")

    scenes = tpi.query_scene_numbers_for_group(address)
    print(f"    scenes: {scenes}")

    for scene in scenes:
        label = tpi.query_scene_label_for_group(address, scene)
        print(f"    Group {address.number} scene {scene} label: {label}")
