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
    """Test the async ZenCommandClient with ctrl queries"""
    # Load configuration
    config = yaml.safe_load(open(Path(__file__).resolve().parents[2] / "tests" / "config.yaml"))
    
    # Create protocol and controller
    async with ZenCommandClient(print_traffic=True) as tpi:
        ctx = EntityContext(commands=tpi)
        ctrl = ctx.ctrl(**config.get('zencontrol')[0])
        
        print("Testing ZenController queries...")
        print("=" * 50)
        
        try:
            # Query controller version
            version = await tpi.query_controller_version_number(ctrl)
            print(f"ZenController version: {version}")
            
            # Query controller label
            controller_label = await tpi.query_controller_label(ctrl)
            print(f"ZenController label: {controller_label}")
            
            # Query controller fitting number
            controller_fitting_number = await tpi.query_controller_fitting_number(ctrl)
            print(f"ZenController fitting number: {controller_fitting_number}")
            
            # Query startup status
            startup_complete = await tpi.query_controller_startup_complete(ctrl)
            print(f"ZenController startup complete: {startup_complete}")
            
            # Query DALI bus status
            dali_ready = await tpi.query_is_dali_ready(ctrl)
            print(f"DALI bus is ready: {dali_ready}")
            
        except Exception as e:
            print(f"Error during testing: {e}")
        
        print("=" * 50)
        print("Test completed!")

if __name__ == "__main__":
    run_with_keyboard_interrupt(main)

