import asyncio
import yaml
from zencontrol import ZenProtocol, ZenController, run_with_keyboard_interrupt

async def main():
    """Test system variable queries and sets"""
    # Load configuration
    config = yaml.safe_load(open("tests/config.yaml"))
    
    # Create protocol and controller
    async with ZenProtocol(print_spam=True) as tpi:
        ctrl = ZenController(protocol=tpi, **config.get('zencontrol')[0])
        tpi.set_controllers([ctrl])
        
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
