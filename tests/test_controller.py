import asyncio
import yaml
from zencontrol import ZenProtocol, ZenController, run_with_keyboard_interrupt

async def main():
    """Test the async ZenProtocol with controller queries"""
    # Load configuration
    config = yaml.safe_load(open("tests/config.yaml"))
    
    # Create protocol and controller
    async with ZenProtocol(print_traffic=True) as tpi:
        ctrl = ZenController(protocol=tpi, **config.get('zencontrol')[0])
        tpi.set_controllers([ctrl])
        
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

