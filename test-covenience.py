from zen import ZenProtocol, ZenController, ZenLight, ZenGroup, ZenMotionSensor
import yaml

config = yaml.safe_load(open("test-config.yaml"))
ctrl = ZenController(**config.get('zencontrol')[0])
tpi = ZenProtocol(controllers=[ctrl], narration=True)

print("Lights")
lights = tpi.get_lights()
for light in lights:
    print(f"  • {light}")
    for group in light.groups:
        print(f"      • {group}")

print("Groups")
groups = tpi.get_groups()
for group in groups:
    print(f"  • {group}")
    for light in group.lights:
        print(f"      • {light}")

print("Buttons")
buttons = tpi.get_buttons()
for button in buttons:
    print(f"  • {button}")

print("Motion sensors")
motion_sensors = tpi.get_motion_sensors()
for motion_sensor in motion_sensors:
    print(f"  • {motion_sensor}")
    print(f"      = {'occupied' if motion_sensor.occupied else 'not occupied'}")

print("System variables")
system_variables = tpi.get_system_variables()
for zsv in system_variables:
    print(f"  • {zsv}")
    print(f"      = {zsv.value}")
