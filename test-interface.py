from zen_interface import ZenInterface
import yaml
import time

config = yaml.safe_load(open("test-config.yaml"))
zi = ZenInterface(narration=False)
zi.add_controller(**config.get('zencontrol')[0])
zi.start()


timer_start = time.time()

print("Profiles")
profiles = zi.get_profiles()
for profile in profiles:
    print(f"  • {profile}")

print("Lights")
lights = zi.get_lights()
for light in lights:
    print(f"  • {light}")
    for group in light.groups:
        print(f"      • {group}")

print("Groups")
groups = zi.get_groups()
for group in groups:
    print(f"  • {group}")
    for light in group.lights:
        print(f"      • {light}")

print("Buttons")
buttons = zi.get_buttons()
for button in buttons:
    print(f"  • {button}")

print("Motion sensors")
motion_sensors = zi.get_motion_sensors()
for motion_sensor in motion_sensors:
    print(f"  • {motion_sensor}")
    print(f"      = {'occupied' if motion_sensor.occupied else 'not occupied'}")

print("System variables")
system_variables = zi.get_system_variables()
for zsv in system_variables:
    print(f"  • {zsv}")
    print(f"      = {zsv.value}")

timer_end = time.time()
print(f"Time taken: {timer_end - timer_start} seconds")
