import asyncio
import yaml
from zencontrol import ZenProtocol, ZenController, run_with_keyboard_interrupt

async def main():
    """Test group queries"""
    # Load configuration
    config = yaml.safe_load(open("tests/config.yaml"))
    
    # Create protocol and controller
    async with ZenProtocol(print_traffic=True) as tpi:
        ctrl = ZenController(protocol=tpi, **config.get('zencontrol')[0])
        tpi.set_controllers([ctrl])
        
        print("Testing group queries...")
        print("=" * 50)
        
        try:
            addresses = await tpi.query_group_numbers(ctrl)
            print(f"Group")

            for address in addresses:
                print(f"  {address.number}")
                name = await tpi.query_group_label(address)
                print(f"    name: {name}")

                information = await tpi.query_group_by_number(address)
                print(f"    information: {information}")

                scenes = await tpi.query_scene_numbers_for_group(address)
                print(f"    scenes: {scenes}")

                for scene in scenes:
                    label = await tpi.query_scene_label_for_group(address, scene)
                    print(f"    Group {address.number} scene {scene} label: {label}")

            gear = await tpi.query_control_gear_dali_addresses(ctrl)
            print(f"Gear")
            for gear in gear:
                print(f"  {gear.number}")
                groups = await tpi.query_group_membership_by_address(gear)
                for group in groups:
                    print(f"    group: {group}")
                    
        except Exception as e:
            print(f"Error during testing: {e}")
        
        print("=" * 50)
        print("Test completed!")

if __name__ == "__main__":
    run_with_keyboard_interrupt(main)
