import sys
from pathlib import Path

_EXAMPLES = Path(__file__).resolve().parent.parent
if str(_EXAMPLES) not in sys.path:
    sys.path.insert(0, str(_EXAMPLES))
from run_main import run_with_keyboard_interrupt

import asyncio
from zencontrol import ZenControl
import yaml
from pathlib import Path
import time

async def main():
    config = yaml.safe_load(open(Path(__file__).resolve().parents[2] / "tests" / "config.yaml"))
    zen = ZenControl(print_traffic=False)
    zen.add_controller(**config.get('zencontrol')[0])
    await zen.start()

    timer_start = time.time()

    print("Profiles")
    profiles = await zen.get_profiles()
    for profile in profiles:
        print(f"  • {profile}")

    print("Lights")
    lights = await zen.get_lights()
    for light in lights:
        print(f"  • {light}")
        for group in light.groups:
            print(f"      • {group}")

    print("Groups")
    groups = await zen.get_groups()
    for group in groups:
        print(f"  • {group}")
        for light in group.lights:
            print(f"      • {light}")

    print("Buttons")
    buttons = await zen.get_buttons()
    for button in buttons:
        print(f"  • {button}")

    print("Motion sensors")
    motion_sensors = await zen.get_motion_sensors()
    for motion_sensor in motion_sensors:
        print(f"  • {motion_sensor}")
        print(f"      = {'occupied' if motion_sensor.occupied else 'not occupied'}")

    print("System variables")
    system_variables = await zen.get_system_variables()
    for zsv in system_variables:
        print(f"  • {zsv}")
        value = await zsv.get_value()
        print(f"      = {value}")

    timer_end = time.time()
    print(f"Time taken: {timer_end - timer_start} seconds")

if __name__ == "__main__":
    run_with_keyboard_interrupt(main)
