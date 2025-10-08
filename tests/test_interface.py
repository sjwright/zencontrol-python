import asyncio
from zencontrol import ZenControl, run_with_keyboard_interrupt
import yaml
import time

async def main():
    config = yaml.safe_load(open("tests/config.yaml"))
    zi = ZenControl(print_traffic=False)
    zi.add_controller(**config.get('zencontrol')[0])
    await zi.start()

    timer_start = time.time()

    print("Profiles")
    profiles = await zi.get_profiles()
    for profile in profiles:
        print(f"  • {profile}")

    print("Lights")
    lights = await zi.get_lights()
    for light in lights:
        print(f"  • {light}")
        for group in light.groups:
            print(f"      • {group}")

    print("Groups")
    groups = await zi.get_groups()
    for group in groups:
        print(f"  • {group}")
        for light in group.lights:
            print(f"      • {light}")

    print("Buttons")
    buttons = await zi.get_buttons()
    for button in buttons:
        print(f"  • {button}")

    print("Motion sensors")
    motion_sensors = await zi.get_motion_sensors()
    for motion_sensor in motion_sensors:
        print(f"  • {motion_sensor}")
        print(f"      = {'occupied' if motion_sensor.occupied else 'not occupied'}")

    print("System variables")
    system_variables = await zi.get_system_variables()
    for zsv in system_variables:
        print(f"  • {zsv}")
        value = await zsv.get_value()
        print(f"      = {value}")

    timer_end = time.time()
    print(f"Time taken: {timer_end - timer_start} seconds")

if __name__ == "__main__":
    run_with_keyboard_interrupt(main)
