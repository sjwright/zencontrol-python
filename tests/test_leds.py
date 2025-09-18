import asyncio
import yaml
from zencontrol import ZenProtocol, ZenController, ZenAddress, ZenAddressType, run_with_keyboard_interrupt

async def main():
    """Test LED control queries"""
    # Load configuration
    config = yaml.safe_load(open("tests/config.yaml"))
    
    # Create protocol and controller
    async with ZenProtocol(print_spam=False) as tpi:
        ctrl = ZenController(protocol=tpi, **config.get('zencontrol')[0])
        tpi.set_controllers([ctrl])
        
        print("Testing LED control queries...")
        print("=" * 50)
        
        try:
            address = ZenAddress(ctrl, ZenAddressType.ECD, 4) # Office

            instances = await tpi.query_instances_by_address(address)
            print(f"    instances: {instances}")

            for instance in instances:
                
                instance_label = await tpi.query_dali_instance_label(instance, generic_if_none=True)
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

