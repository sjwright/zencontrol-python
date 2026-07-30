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
    """Test system variable queries and sets"""
    # Load configuration
    config = yaml.safe_load(open(Path(__file__).resolve().parents[2] / "tests" / "config.yaml"))
    
    # Create protocol and controller
    async with ZenCommandClient(print_traffic=True) as tpi:
        ctx = EntityContext(commands=tpi)
        ctrl = ctx.controller(**config.get('zencontrol')[0])
        
        print("Testing system variable queries and sets...")
        print("=" * 50)
        
        try:
            # Direct access
            print("Direct access")
            var1 = await tpi.query_system_variable(ctrl, 1)
            print(f"  sys var 1: {var1}")
            var2 = await tpi.query_system_variable(ctrl, 2)
            print(f"  sys var 2: {var2}")
            var3 = await tpi.query_system_variable(ctrl, 3)
            print(f"  sys var 3: {var3}")
            var4 = await tpi.query_system_variable(ctrl, 4)
            print(f"  sys var 4: {var4}")

            # Direct set
            print("Direct set")
            await tpi.set_system_variable(ctrl, 4, 420)
            var4 = await tpi.query_system_variable(ctrl, 4)
            print(f"  sys var 4: {var4}")
            
        except Exception as e:
            print(f"Error during testing: {e}")
        
        print("=" * 50)
        print("Test completed!")

if __name__ == "__main__":
    run_with_keyboard_interrupt(main)
