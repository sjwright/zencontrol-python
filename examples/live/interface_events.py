import sys
from pathlib import Path

_EXAMPLES = Path(__file__).resolve().parent.parent
if str(_EXAMPLES) not in sys.path:
    sys.path.insert(0, str(_EXAMPLES))
from run_main import run_with_keyboard_interrupt

import asyncio
from zencontrol import ZenControl, ZenProfile, ZenGroup, ZenLight, ZenButton, ZenMotionSensor, ZenSystemVariable
import yaml
from pathlib import Path
import time

async def main():
    config = yaml.safe_load(open(Path(__file__).resolve().parents[2] / "tests" / "config.yaml"))
    zen = ZenControl(print_traffic=False)
    zen.add_controller(**config.get('zencontrol')[0])

    # Handlers
    async def _zen_on_connect() -> None:
        print(f"Connected to Zen")

    async def _zen_on_disconnect() -> None:
        print(f"Disconnected from Zen")

    async def _zen_profile_change(profile: ZenProfile) -> None:
        ms()
        print(f"Profile Change Event     - {profile}")

    async def _zen_group_change(*, group: ZenGroup, discoordinated: bool = False) -> None:
        ms()
        print(f"Group Change Event       - {group} level {group.level} colour {group.colour} scene {group.scene} {'discoordinated' if discoordinated else ''}")

    async def _zen_light_change(*, light: ZenLight) -> None:
        ms()
        print(f"Light Change Event       - {light} level {light.level} colour {light.colour} scene {light.scene}")

    async def _zen_button_press(button: ZenButton) -> None:
        ms()
        print(f"Button Press Event       - {button}")

    async def _zen_button_long_press(button: ZenButton) -> None:
        ms()
        print(f"Button Long Press Event  - {button}")

    async def _zen_motion_event(*, sensor: ZenMotionSensor) -> None:
        ms()
        print(f"Motion Event             - {sensor} {'occupied' if sensor.occupied else 'not occupied'}")

    async def _zen_system_variable_change(*, system_variable: ZenSystemVariable, by_me: bool = False) -> None:
        ms()
        print(f"System Variable Change   - {system_variable} value {system_variable.value} {'by me' if by_me else 'by someone else'}")

    timevar = None
    def ms():
        nonlocal timevar
        timevar = timevar or time.time()
        msecs = (time.time() - timevar) * 1000
        timevar = time.time()
        print(f"{msecs:.1f} ms")

    # Set up event callbacks
    # zen.callbacks.on_connect = _zen_on_connect
    # zen.callbacks.on_disconnect = _zen_on_disconnect
    # zen.callbacks.profile_change = _zen_profile_change
    zen.callbacks.group_change = _zen_group_change
    zen.callbacks.light_change = _zen_light_change
    zen.callbacks.button_press = _zen_button_press
    # zen.callbacks.button_long_press = _zen_button_long_press
    # zen.callbacks.motion_event = _zen_motion_event
    # zen.callbacks.system_variable_change = _zen_system_variable_change

    # Start event monitoring
    await zen.start()

    print("Event monitoring started. Press Ctrl+C to stop.")
    
    # Loop forever
    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping event monitoring...")
        await zen.stop()

if __name__ == "__main__":
    run_with_keyboard_interrupt(main)