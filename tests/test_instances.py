import asyncio
import yaml
from zencontrol import ZenProtocol, ZenController, run_with_keyboard_interrupt

async def main():
    """Test instance queries"""
    # Load configuration
    config = yaml.safe_load(open("tests/config.yaml"))
    
    # Create protocol and controller
    async with ZenProtocol(print_spam=False) as tpi:
        ctrl = ZenController(protocol=tpi, **config.get('zencontrol')[0])
        tpi.set_controllers([ctrl])
        
        print("Testing instance queries...")
        print("=" * 50)
        
        try:
            addresses = await tpi.query_dali_addresses_with_instances(ctrl, 0)
            print(f"Addresses with instances:")

            for address in addresses:
                print(f"  {address.number}")

                label = await tpi.query_dali_device_label(address)
                print(f"    label: {label}")

                operating_mode = await tpi.query_operating_mode_by_address(address)
                print(f"    operating mode: {operating_mode}")

                instances = await tpi.query_instances_by_address(address)
                # print(f"    instances: {instances}")

                for instance in instances:
                    
                    instance_label = await tpi.query_dali_instance_label(instance)
                    print(f"      {instance.number} - {instance_label}")

                    groups = await tpi.query_instance_groups(instance)
                    print(f"      groups: {groups}")

                    fitting = await tpi.query_dali_instance_fitting_number(instance)
                    print(f"      fitting: {fitting}")

                    occupancy_timers = await tpi.query_occupancy_instance_timers(instance)
                    print(f"      occupancy timers: {occupancy_timers}")

                    last_known_led_state = await tpi.query_last_known_dali_button_led_state(instance)
                    print(f"      last known led state: {last_known_led_state}")
                    
        except Exception as e:
            print(f"Error during testing: {e}")
        
        print("=" * 50)
        print("Test completed!")

if __name__ == "__main__":
    run_with_keyboard_interrupt(main)

