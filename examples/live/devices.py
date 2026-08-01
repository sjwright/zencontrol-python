import sys
from pathlib import Path
_EXAMPLES = Path(__file__).resolve().parent.parent
if str(_EXAMPLES) not in sys.path:
    sys.path.insert(0, str(_EXAMPLES))
from run_main import run_with_keyboard_interrupt

import asyncio
import yaml
from pathlib import Path
from zencontrol import ZenCommandClient
from zencontrol.interface import EntityContext

async def main():
    """Test DALI device queries"""
    # Load configuration
    config = yaml.safe_load(open(Path(__file__).resolve().parents[2] / "tests" / "config.yaml"))
    
    # Create protocol and controller
    async with ZenCommandClient(print_traffic=False) as tpi:
        ctx = EntityContext(commands=tpi)
        ctrl = ctx.ctrl(**config.get('zencontrol')[0])
        
        print("Testing DALI device queries...")
        print("=" * 50)
        
        try:
            addresses = await tpi.query_dali_addresses_with_instances(ctrl)
            print(f"Addresses with instances:")
            
            for address in addresses:
                print(f"  {address.number}")

                label = await tpi.query_dali_device_label(address)
                if label is None:
                    label = f"{address.ctrl.label} ECD {address.number}"
                print(f"    label: {label}")

                operating_mode = await tpi.query_operating_mode_by_address(address)
                print(f"    operating mode: {operating_mode}")

                serial = await tpi.query_dali_serial(address)
                print(f"    serial: {serial}")

                ean = await tpi.query_dali_ean(address)
                print(f"    ean: {ean}")

                fitting = await tpi.query_dali_fitting_number(address)
                print(f"    fitting: {fitting}")
                
        except Exception as e:
            print(f"Error during testing: {e}")
        
        print("=" * 50)
        print("Test completed!")

if __name__ == "__main__":
    run_with_keyboard_interrupt(main)
