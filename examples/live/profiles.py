import sys
from pathlib import Path

_EXAMPLES = Path(__file__).resolve().parent.parent
if str(_EXAMPLES) not in sys.path:
    sys.path.insert(0, str(_EXAMPLES))
from run_main import run_with_keyboard_interrupt

import asyncio
import yaml
from pathlib import Path
from zencontrol import ZenCommandClient, ZenController
from zencontrol.interface import EntityContext

async def main():
    """Test profile queries"""
    # Load configuration
    config = yaml.safe_load(open(Path(__file__).resolve().parents[2] / "tests" / "config.yaml"))
    
    # Create protocol and controller
    async with ZenCommandClient(print_traffic=False) as tpi:
        ctx = EntityContext(commands=tpi)
        ctrl = ZenController(ctx=ctx, **config.get('zencontrol')[0])
        
        print("Testing profile queries...")
        print("=" * 50)
        
        try:
            current_profile = await tpi.query_current_profile_number(ctrl)
            print(f"Current profile: {current_profile}")

            profile_info, profiles = await tpi.query_profile_information(ctrl)
            for info in profile_info:
                print(f"  {info} = {profile_info[info]}")

            for profile in profiles:
                label = await tpi.query_profile_label(ctrl, profile)
                print(f"  {profile} = {label} {profiles[profile]}")
                
        except Exception as e:
            print(f"Error during testing: {e}")
        
        print("=" * 50)
        print("Test completed!")

if __name__ == "__main__":
    run_with_keyboard_interrupt(main)


