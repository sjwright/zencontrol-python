import asyncio
import yaml
from zencontrol import ZenProtocol, ZenController, ZenAddress, ZenInstance, ZenEventMode, run_with_keyboard_interrupt

async def main():
    """Test multicast event monitoring"""
    # Load configuration
    config = yaml.safe_load(open("tests/config.yaml"))
    
    # Create protocol and controller
    async with ZenProtocol(print_traffic=True, unicast=False) as tpi:
        ctrl = ZenController(protocol=tpi, **config.get('zencontrol')[0])
        tpi.set_controllers([ctrl])
        
        print("Testing multicast event monitoring...")
        print("=" * 50)
        
        try:
            y = await tpi.query_tpi_event_emit_state(ctrl)
            print(f"Initial event emit state: {y}")

            x = await tpi.tpi_event_emit(ctrl, ZenEventMode(enabled=False, filtering=ctrl.filtering, unicast=False, multicast=True))
            print(f"Set event emit state: {x}")

            y = await tpi.query_tpi_event_emit_state(ctrl)
            print(f"Event emit state after set: {y}")

            await tpi.start_event_monitoring()

            y = await tpi.query_tpi_event_emit_state(ctrl)
            print(f"Event emit state after start monitoring: {y}")

            print("\nEvent monitoring started. Press Ctrl+C to stop...")
            
            # Keep the event loop running
            try:
                while True:
                    await asyncio.sleep(1)
            except KeyboardInterrupt:
                print("\nStopping event monitoring...")
                await tpi.stop_event_monitoring()
                print("Event monitoring stopped.")
                
        except Exception as e:
            print(f"Error during testing: {e}")
        
        print("=" * 50)
        print("Test completed!")

if __name__ == "__main__":
    run_with_keyboard_interrupt(main)
