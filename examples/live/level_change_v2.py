import asyncio
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import yaml

from zencontrol import ZenAddress
from zencontrol.interface import EntityContext
from zencontrol.api.commands import ZenCommandClient
from zencontrol.api.event_decode import ZenEventCode
from zencontrol.api.types import ZenAddressType
from run_main import run_with_keyboard_interrupt
CONFIG_PATH = Path(__file__).resolve().parents[2] / "tests" / "config.yaml"
ECG_ADDRESS = 33
TIMEOUT_SECONDS = 5.0


async def test_level_change_v2():
    config = yaml.safe_load(CONFIG_PATH.read_text())
    level = random.randint(100, 250)
    event_received = asyncio.Event()
    received: dict = {}

    async with ZenCommandClient(print_traffic=True) as tpi:
        ctx = EntityContext(commands=tpi)
        ctrl = ctx.ctrl(**config["zencontrol"][0])

        original_process = tpi._process_zen_event

        async def capture_level_change_v2(event):
            if (
                event.code == ZenEventCode.LEVEL_CHANGE_V2
                and event.target == ECG_ADDRESS
            ):
                received["event"] = event
                event_received.set()
            await original_process(event)

        tpi._process_zen_event = capture_level_change_v2

        await tpi.start_event_monitoring()

        try:
            address = ZenAddress(ctrl, ZenAddressType.ECG, ECG_ADDRESS)
            await tpi.dali_arc_level(address, level)
            print(f"Set address {ECG_ADDRESS} to level {level}, waiting for LEVEL_CHANGE_V2...")
            await asyncio.wait_for(event_received.wait(), timeout=TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            raise RuntimeError(
                f"Timed out after {TIMEOUT_SECONDS}s waiting for LEVEL_CHANGE_V2 "
                f"on address {ECG_ADDRESS} level {level}"
            ) from None
        finally:
            await tpi.stop_event_monitoring()

    event = received["event"]
    if event.code != ZenEventCode.LEVEL_CHANGE_V2:
        raise RuntimeError(f"Expected LEVEL_CHANGE_V2, got event code {event.code}")
    if event.target != ECG_ADDRESS:
        raise RuntimeError(f"Expected target {ECG_ADDRESS}, got {event.target}")

    current = event.payload[0] if len(event.payload) >= 1 else None
    target = event.payload[1] if len(event.payload) >= 2 else None
    print(f"LEVEL_CHANGE_V2 received: address {event.target}, current {current}, target {target}")


async def main():
    await test_level_change_v2()
    print("Test passed.")


if __name__ == "__main__":
    run_with_keyboard_interrupt(main)
