from zen import ZenProtocol, ZenController, ZenLight, ZenGroup, ZenMotionSensor
import yaml

config = yaml.safe_load(open("test-config.yaml"))
ctrl = ZenController(**config.get('zencontrol')[0])
tpi = ZenProtocol(controllers=[ctrl], narration=False)


lights = tpi.get_lights()
for light in lights:
    print(f"  • {light}")
    for group in light.groups:
        print(f"      • {group}")


groups = tpi.get_groups()
for group in groups:
    print(f"  • {group}")
    for light in group.lights:
        print(f"      • {light}")


motion_sensors = tpi.get_motion_sensors()
for motion_sensor in motion_sensors:
    print(f"  • {motion_sensor}")