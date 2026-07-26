import asyncio
import logging
import yaml
import time
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

sys.stdout.reconfigure(line_buffering=True)

from zencontrol import ZenControl, run_with_keyboard_interrupt
from zencontrol.interface.interface import ZenButton, ZenGroup, ZenLight

CONFIG_PATH = Path(__file__).resolve().parents[2] / "tests" / "config.yaml"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("test_events")

timevar = None


def ms():
    """Print time since last call in milliseconds."""
    global timevar
    timevar = timevar or time.time()
    msecs = (time.time() - timevar) * 1000
    timevar = time.time()
    print(f"{msecs:.1f} ms")


async def on_button_press(button: ZenButton) -> None:
    ms()
    inst = button.instance
    print(f"Button Press Event       - ECD {inst.address.number} instance {inst.number}")


async def on_light_change(light: ZenLight, level, colour, scene) -> None:
    ms()
    addr = light.address
    print(f"Level Change Event       - {addr.type} {addr.number} level {level}")


async def on_group_change(group: ZenGroup, level, colour, scene, discoordinated=False) -> None:
    ms()
    addr = group.address
    print(f"Level Change Event Group - {addr.type} {addr.number} level {level}")


async def on_scene_change_light(light: ZenLight, level, colour, scene) -> None:
    if scene is not None:
        ms()
        addr = light.address
        print(f"Scene Change Event       - {addr.type} {addr.number} scene {scene}")


def check_event_listener(zen: ZenControl) -> None:
    """Print listener health — call periodically to detect silent task death."""
    task = zen.event_task

    if task is None:
        print("LISTENER: event_task not started")
        return
    if task.done():
        exc = task.exception()
        if exc:
            print(f"LISTENER: event_task died with {type(exc).__name__}: {exc}")
        else:
            print("LISTENER: event_task finished unexpectedly")
    elif not zen.is_event_monitoring_active():
        print("LISTENER: event_task present but monitoring inactive")
    else:
        print("LISTENER: ok")


async def main():
    """Test ZenControl event monitoring."""
    config = yaml.safe_load(CONFIG_PATH.read_text())

    async with ZenControl(print_traffic=True, unicast=False, logger=logger) as zen:
        zen.add_controller(**config.get("zencontrol")[0])
        ctrl = zen.controllers[0]

        zen.button_press = on_button_press
        zen.light_change = on_light_change
        zen.group_change = on_group_change

        print("Testing ZenControl Event Monitoring...")
        print("=" * 60)

        try:
            print("Querying TPI event configuration...")
            unicast_config = await zen.commands.query_tpi_event_unicast_address(ctrl)
            print(f"TPI Event Unicast Address: {unicast_config}")

            emit_state = await zen.commands.query_tpi_event_emit_state(ctrl)
            print(f"TPI Event Emit State: {emit_state}")

        except Exception as e:
            print(f"Error querying event configuration: {e}")

        print("\nStarting event monitoring...")
        print("Press Ctrl+C to stop")
        print("=" * 60)

        await zen.start()
        check_event_listener(zen)

        try:
            while True:
                await asyncio.sleep(5)
                check_event_listener(zen)
        except KeyboardInterrupt:
            print("\nStopping event monitoring...")
            await zen.stop()
            print("Event monitoring stopped.")


if __name__ == "__main__":
    run_with_keyboard_interrupt(main())
