import sys
from pathlib import Path

_EXAMPLES = Path(__file__).resolve().parent.parent
if str(_EXAMPLES) not in sys.path:
    sys.path.insert(0, str(_EXAMPLES))
from run_main import run_with_keyboard_interrupt

import asyncio
import yaml
from pathlib import Path
from zencontrol import ZenCommandClient, ZenAddress, ZenInstance, ZenEventMode
from zencontrol.interface import EntityContext

async def main():
    """Test multicast event monitoring"""
    # Load configuration
    config = yaml.safe_load(open(Path(__file__).resolve().parents[2] / "tests" / "config.yaml"))
    
    # Create protocol and controller
    async with ZenCommandClient(print_traffic=True) as tpi:
        ctx = EntityContext(commands=tpi)
        ctrl = ctx.controller(**config.get('zencontrol')[0])
        
        print("Testing multicast event monitoring...")
        print("=" * 50)
        
        try:
            y = await tpi.query_tpi_event_emit_state(ctrl)
            print(f"Initial event emit state: {y}")

            x = await tpi.tpi_event_emit(ctrl, ZenEventMode(enabled=False, filtering=ctrl.filtering, unicast=False, multicast=True))
            print(f"Set event emit state: {x}")

            y = await tpi.query_tpi_event_emit_state(ctrl)
            print(f"Event emit state after set: {y}")

            await tpi.start_event_monitoring()

            y = await tpi.query_tpi_event_emit_state(ctrl)
            print(f"Event emit state after start monitoring: {y}")

            print("\nEvent monitoring started. Press Ctrl+C to stop...")
            
            # Keep the event loop running
            try:
                while True:
                    await asyncio.sleep(1)
            except KeyboardInterrupt:
                print("\nStopping event monitoring...")
                await tpi.stop_event_monitoring()
                print("Event monitoring stopped.")
                
        except Exception as e:
            print(f"Error during testing: {e}")
        
        print("=" * 50)
        print("Test completed!")

if __name__ == "__main__":
    run_with_keyboard_interrupt(main)
