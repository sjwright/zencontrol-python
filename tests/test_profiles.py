import asyncio
import yaml
from zencontrol import ZenProtocol, ZenController, run_with_keyboard_interrupt

async def main():
    """Test profile queries"""
    # Load configuration
    config = yaml.safe_load(open("tests/config.yaml"))
    
    # Create protocol and controller
    async with ZenProtocol(print_spam=False) as tpi:
        ctrl = ZenController(protocol=tpi, **config.get('zencontrol')[0])
        tpi.set_controllers([ctrl])
        
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


