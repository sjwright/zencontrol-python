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

from zencontrol import ZenProtocol, ZenController, ZenAddress, ZenInstance, run_with_keyboard_interrupt

CONFIG_PATH = Path(__file__).parent / "config.yaml"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("test_events")

# Global timing variable for event timing
timevar = None

def ms():
    """Print time since last call in milliseconds"""
    global timevar
    timevar = timevar or time.time()
    msecs = (time.time() - timevar) * 1000
    timevar = time.time()
    print(f"{msecs:.1f} ms")

# Event handlers — must be async; protocol awaits them
async def button_press_event(instance: ZenInstance, payload: bytes) -> None:
    ms()
    print(f"Button Press Event       - ECD {instance.address.number} instance {instance.number}")

async def level_change_event(address: ZenAddress, arc_level: int, payload: bytes) -> None:
    ms()
    print(f"Level Change Event       - {address.type} {address.number} arc_level {arc_level}")

async def group_level_change_event(address: ZenAddress, arc_level: int, payload: bytes) -> None:
    ms()
    print(f"Level Change Event Group - {address.type} {address.number} arc_level {arc_level}")

async def scene_change_event(address: ZenAddress, scene: int, active: bool, payload: bytes) -> None:
    ms()
    print(f"Scene Change Event       - {address.type} {address.number} scene {scene}")

async def colour_change_event(address: ZenAddress, colour: bytes, payload: bytes) -> None:
    ms()
    print(f"Colour Change Event      - {address.type} {address.number} colour {colour}")

def check_event_listener(tpi: ZenProtocol) -> None:
    """Print listener health — call periodically to detect silent task death."""
    task = tpi.event_task
    listener = tpi.event_listener

    if task is None:
        print("LISTENER: event_task not started")
        return
    if task.done():
        exc = task.exception()
        if exc:
            print(f"LISTENER: event_task died with {type(exc).__name__}: {exc}")
        else:
            print("LISTENER: event_task finished unexpectedly")
    elif listener is None or not listener.is_listening():
        print("LISTENER: event_task running but socket is closed")
    else:
        print(f"LISTENER: ok (queue size {listener._event_queue.qsize()})")

async def main():
    """Test async ZenProtocol with event monitoring"""
    config = yaml.safe_load(CONFIG_PATH.read_text())

    async with ZenProtocol(print_traffic=True, unicast=False, logger=logger) as tpi:
        ctrl = ZenController(protocol=tpi, **config.get('zencontrol')[0])
        tpi.set_controllers([ctrl])

        print("Testing ZenProtocol Event Monitoring...")
        print("=" * 60)

        tpi.set_callbacks(
            button_press_callback=button_press_event,
            level_change_callback=level_change_event,
            group_level_change_callback=group_level_change_event,
            scene_change_callback=scene_change_event,
            colour_change_callback=colour_change_event
        )

        try:
            print("Querying TPI event configuration...")
            unicast_config = await tpi.query_tpi_event_unicast_address(ctrl)
            print(f"TPI Event Unicast Address: {unicast_config}")

            emit_state = await tpi.query_tpi_event_emit_state(ctrl)
            print(f"TPI Event Emit State: {emit_state}")

        except Exception as e:
            print(f"Error querying event configuration: {e}")

        print("\nStarting event monitoring...")
        print("Press Ctrl+C to stop")
        print("=" * 60)

        await tpi.start_event_monitoring()
        check_event_listener(tpi)

        try:
            while True:
                await asyncio.sleep(5)
                check_event_listener(tpi)
        except KeyboardInterrupt:
            print("\nStopping event monitoring...")
            await tpi.stop_event_monitoring()
            print("Event monitoring stopped.")

if __name__ == "__main__":
    run_with_keyboard_interrupt(main)
