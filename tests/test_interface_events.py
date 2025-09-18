import asyncio
from zencontrol import ZenControl, ZenProfile, ZenGroup, ZenLight, ZenButton, ZenMotionSensor, ZenSystemVariable, ZenColour, run_with_keyboard_interrupt
import yaml
import time
from typing import Optional

async def main():
    config = yaml.safe_load(open("tests/config.yaml"))
    zi = ZenControl(print_spam=False)
    zi.add_controller(**config.get('zencontrol')[0])

    # Handlers
    async def _zen_on_connect() -> None:
        print(f"Connected to Zen")

    async def _zen_on_disconnect() -> None:
        print(f"Disconnected from Zen")

    async def _zen_profile_change(profile: ZenProfile) -> None:
        ms()
        print(f"Profile Change Event     - {profile}")

    async def _zen_group_change(group: ZenGroup, level: Optional[int] = None, colour: Optional[ZenColour] = None, scene: Optional[int] = None, discoordinated: bool = False) -> None:
        ms()
        print(f"Group Change Event       - {group} level {level} colour {colour} scene {scene} {'discoordinated' if discoordinated else ''}")

    async def _zen_light_change(light: ZenLight, level: Optional[int] = None, colour: Optional[ZenColour] = None, scene: Optional[int] = None) -> None:
        ms()
        print(f"Light Change Event       - {light} level {level} colour {colour} scene {scene}")

    async def _zen_button_press(button: ZenButton) -> None:
        ms()
        print(f"Button Press Event       - {button}")

    async def _zen_button_long_press(button: ZenButton) -> None:
        ms()
        print(f"Button Long Press Event  - {button}")

    async def _zen_motion_event(sensor: ZenMotionSensor, occupied: bool) -> None:
        ms()
        print(f"Motion Event             - {sensor} {'occupied' if occupied else 'not occupied'}")

    async def _zen_system_variable_change(system_variable: ZenSystemVariable, value: int, changed: bool, by_me: bool) -> None:
        ms()
        print(f"System Variable Change   - {system_variable} value {value} {'changed' if changed else 'not changed'} {'by me' if by_me else 'by someone else'}")

    timevar = None
    def ms():
        nonlocal timevar
        timevar = timevar or time.time()
        msecs = (time.time() - timevar) * 1000
        timevar = time.time()
        print(f"{msecs:.1f} ms")

    # Set up event callbacks
    # zi.on_connect = _zen_on_connect
    # zi.on_disconnect = _zen_on_disconnect
    # zi.profile_change = _zen_profile_change
    zi.group_change = _zen_group_change
    zi.light_change = _zen_light_change
    zi.button_press = _zen_button_press
    # zi.button_long_press = _zen_button_long_press
    # zi.motion_event = _zen_motion_event
    # zi.system_variable_change = _zen_system_variable_change

    # Start event monitoring
    await zi.start()

    print("Event monitoring started. Press Ctrl+C to stop.")
    
    # Loop forever
    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping event monitoring...")
        await zi.stop()

if __name__ == "__main__":
    run_with_keyboard_interrupt(main)