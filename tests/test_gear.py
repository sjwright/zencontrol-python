import asyncio
import yaml
from zencontrol import ZenProtocol, ZenController, run_with_keyboard_interrupt

async def main():
    """Test control gear queries"""
    # Load configuration
    config = yaml.safe_load(open("tests/config.yaml"))
    
    # Create protocol and controller
    async with ZenProtocol(print_spam=False) as tpi:
        ctrl = ZenController(protocol=tpi, **config.get('zencontrol')[0])
        tpi.set_controllers([ctrl])
        
        print("Testing control gear queries...")
        print("=" * 50)
        
        try:
            addresses = await tpi.query_control_gear_dali_addresses(ctrl)
            print(f"Control gears")    

            
            for address in addresses[:5]: # Only first 5 gears
                print(f"  {address.number}")

                level = await tpi.dali_query_level(address)
                print(f"    current level: {level}")

                label = await tpi.query_dali_device_label(address)
                print(f"    label: {label}")

                groups = await tpi.query_group_membership_by_address(address)
                print(f"    groups: {[group.number for group in groups]}")
                
                type = await tpi.dali_query_cg_type(address)
                print(f"    type: {type}")

                colour = await tpi.query_dali_colour(address)
                print(f"    colour: {colour}")
                
                cgtype = await tpi.query_dali_colour_features(address)
                print(f"    colour features: {cgtype}")

                colour_temp_limits = await tpi.query_dali_colour_temp_limits(address)
                print(f"    colour temp limits: {colour_temp_limits}")

                fitting = await tpi.query_dali_fitting_number(address)
                print(f"    fitting: {fitting}")

                ean = await tpi.query_dali_ean(address)
                print(f"    ean: {ean}")

                serial = await tpi.query_dali_serial(address)
                print(f"    serial: {serial}")

                last_scene = await tpi.dali_query_last_scene(address)
                print(f"    last scene: {last_scene}")

                last_scene_is_current = await tpi.dali_query_last_scene_is_current(address)
                print(f"    last scene is current: {last_scene_is_current}")

                status = await tpi.dali_query_control_gear_status(address)
                print(f"    status: {status}")

                scenes = await tpi.query_scene_numbers_by_address(address)
                print(f"    scenes with levels: {scenes}")

                scenes = await tpi.query_colour_scene_membership_by_address(address)
                print(f"    scenes with colours: {scenes}")

                levels = await tpi.query_scene_levels_by_address(address)
                print(f"    scene levels: {levels}")

                scene_data = await tpi.query_scene_colours_by_address(address)
                print(f"    scene colour data: {scene_data}")
                
        except Exception as e:
            print(f"Error during testing: {e}")
        
        print("=" * 50)
        print("Test completed!")

if __name__ == "__main__":
    run_with_keyboard_interrupt(main)

