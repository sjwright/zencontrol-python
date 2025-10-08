import asyncio
import yaml
import time
from zencontrol import ZenProtocol, ZenController, ZenAddress, ZenInstance, run_with_keyboard_interrupt
from typing import Optional

# Global timing variable for event timing
timevar = None

def ms():
    """Print time since last call in milliseconds"""
    global timevar
    timevar = timevar or time.time()
    msecs = (time.time() - timevar) * 1000
    timevar = time.time()
    print(f"{msecs:.1f} ms")

# Event handlers
def button_press_event(instance: ZenInstance, payload: bytes) -> None:
    ms()
    print(f"Button Press Event       - ECD {instance.address.number} instance {instance.number}")

def level_change_event(address: ZenAddress, arc_level: int, payload: bytes) -> None:
    ms()
    print(f"Level Change Event       - {address.type} {address.number} arc_level {arc_level}")

def group_level_change_event(address: ZenAddress, arc_level: int, payload: bytes) -> None:
    ms()
    print(f"Level Change Event Group - {address.type} {address.number} arc_level {arc_level}")

def scene_change_event(address: ZenAddress, scene: int, active: bool, payload: bytes) -> None:
    ms()
    print(f"Scene Change Event       - {address.type} {address.number} scene {scene}")

def colour_change_event(address: ZenAddress, colour: bytes, payload: bytes) -> None:
    ms()
    print(f"Colour Change Event      - {address.type} {address.number} colour {colour}")

async def main():
    """Test async ZenProtocol with event monitoring"""
    # Load configuration
    config = yaml.safe_load(open("tests/config.yaml"))
    
    # Create protocol and controller
    async with ZenProtocol(print_traffic=True, unicast=False) as tpi:
        ctrl = ZenController(protocol=tpi, **config.get('zencontrol')[0])
        tpi.set_controllers([ctrl])
        
        print("Testing ZenProtocol Event Monitoring...")
        print("=" * 60)
        
        # Set up event callbacks
        tpi.set_callbacks(
            button_press_callback=button_press_event,
            level_change_callback=level_change_event,
            group_level_change_callback=group_level_change_event,
            scene_change_callback=scene_change_event,
            colour_change_callback=colour_change_event
        )
        
        # Query event configuration
        try:
            print("Querying TPI event configuration...")
            unicast_config = await tpi.query_tpi_event_unicast_address(ctrl)
            print(f"TPI Event Unicast Address: {unicast_config}")
            
            emit_state = await tpi.query_tpi_event_emit_state(ctrl)
            print(f"TPI Event Emit State: {emit_state}")
            
        except Exception as e:
            print(f"Error querying event configuration: {e}")
        
        print("\nStarting event monitoring...")
        print("Press Ctrl+C to stop")
        print("=" * 60)
        
        # Start event monitoring
        await tpi.start_event_monitoring()
        
        try:
            # Keep the event loop running
            while True:
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            print("\nStopping event monitoring...")
            await tpi.stop_event_monitoring()
            print("Event monitoring stopped.")

if __name__ == "__main__":
    run_with_keyboard_interrupt(main)
