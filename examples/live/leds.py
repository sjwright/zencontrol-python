import sys
from pathlib import Path

_EXAMPLES = Path(__file__).resolve().parent.parent
if str(_EXAMPLES) not in sys.path:
    sys.path.insert(0, str(_EXAMPLES))
from run_main import run_with_keyboard_interrupt

import asyncio
import yaml
from pathlib import Path
from zencontrol import ZenCommandClient, ZenAddress, ZenAddressType
from zencontrol.interface import EntityContext

async def main():
    """Test LED control queries"""
    # Load configuration
    config = yaml.safe_load(open(Path(__file__).resolve().parents[2] / "tests" / "config.yaml"))
    
    # Create protocol and controller
    async with ZenCommandClient(print_traffic=False) as tpi:
        ctx = EntityContext(commands=tpi)
        ctrl = ctx.controller(**config.get('zencontrol')[0])
        
        print("Testing LED control queries...")
        print("=" * 50)
        
        try:
            address = ZenAddress(ctrl, ZenAddressType.ECD, 4) # Office

            instances = await tpi.query_instances_by_address(address)
            print(f"    instances: {instances}")

            for instance in instances:
                
                instance_label = await tpi.query_dali_instance_label(instance)
                if instance_label is None:
                    instance_label = (
                        instance.type.name.title().replace("_", " ")
                        + " "
                        + str(instance.number)
                    )
                print(f"      {instance.number} - {instance_label}")

                last_known_led_state = await tpi.query_last_known_dali_button_led_state(instance)
                print(f"      last known led state: {last_known_led_state}")

                set_led_state = await tpi.override_dali_button_led_state(instance, False)
                print(f"      set led state: {set_led_state}")
                
        except Exception as e:
            print(f"Error during testing: {e}")
        
        print("=" * 50)
        print("Test completed!")

if __name__ == "__main__":
    run_with_keyboard_interrupt(main)

