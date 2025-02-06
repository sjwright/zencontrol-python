from zen import ZenProtocol, ZenController, ZenAddress, ZenInstance, ZenColour, ZenColourType, ZenAddressType, ZenMotionSensor, ZenLight, ZenSystemVariable, ZenProfile, ZenGroup, ZenButton
from typing import Optional
import yaml
import time

config = yaml.safe_load(open("test-config.yaml"))
tpi = ZenProtocol(narration=True, unicast=False)
ctrl = ZenController(protocol=tpi, **config.get('zencontrol')[0])
tpi.set_controllers([ctrl])

# Handlers
def button_press_event(instance: ZenInstance, event_data: dict) -> None:
    print(f"Button Press Event - address {instance.address.number} instance {instance.number}")
def button_hold_event(instance: ZenInstance, event_data: dict) -> None:
    print(f"Button Hold Event - address {instance.address.number} instance {instance.number}")
def absolute_input_event(instance: ZenInstance, event_data: dict) -> None:
    print(f"Absolute Input Event - address {instance.address.number} instance {instance.number}")
def level_change_event(address: ZenAddress, arc_level: int, event_data: dict) -> None:
    print(f"Level Change Event - address {address.number} arc_level {arc_level}")
def group_level_change_event(address: ZenAddress, arc_level: int, event_data: dict) -> None:
    print(f"Group Level Change Event - address {address.number} arc_level {arc_level}")
def scene_change_event(address: ZenAddress, scene: int, event_data: dict) -> None:
    print(f"Scene Change Event - address {address.number} scene {scene}")
def is_occupied_event(instance: ZenInstance, event_data: dict) -> None:
    print(f"Is Occupied Event - address {instance.address.number} instance {instance.number}")
def colour_change_event(address: ZenAddress, colour: bytes, event_data: dict) -> None:
    print(f"Colour Change Event - address {address.number} colour {colour}")
def profile_change_event(controller: ZenController, profile: int, event_data: dict) -> None:
    print(f"Profile Change Event - controller {controller.name} profile {profile}")

def profile_event(profile: ZenProfile) -> None:
    print(f"Profile Event - profile {profile}")
def motion_event(sensor: ZenMotionSensor, occupied: bool) -> None:
    print(f"Motion Event - sensor {sensor} occupied {occupied}")
def light_event(light: ZenLight, level: int, colour: Optional[ZenColour], scene: Optional[int]) -> None:
    print(f"Light Event - light {light} level {level} colour {colour} scene {scene}")
def group_event(group: ZenGroup, scene: Optional[int]) -> None:
    print(f"Group Event - group {group} scene {scene}")
def button_event(button: ZenButton, held: bool = False) -> None:
    print(f"Button Event - button {button} held={held}")
def sysvar_event(system_variable: ZenSystemVariable, value: int, from_controller: bool) -> None:
    print(f"System Variable Event - system_variable {system_variable} value {value} from_controller {from_controller}")
    
# Start event monitoring
tpi.set_callbacks(
    button_press_callback=button_press_event,
    button_hold_callback=button_hold_event,
    absolute_input_callback=absolute_input_event,
    level_change_callback=level_change_event,
    group_level_change_callback=group_level_change_event,
    scene_change_callback=scene_change_event,
    is_occupied_callback=is_occupied_event,
    colour_change_callback=colour_change_event,
    profile_change_callback=profile_change_event
)
tpi.set_convenience_callbacks(
    profile_callback=profile_event,
    motion_callback=motion_event,
    light_callback=light_event,
    group_callback=group_event,
    button_callback=button_event,
    sysvar_callback=sysvar_event
)
tpi.start_event_monitoring()


time.sleep(1)

# Set light 3 to 100/254
light3 = ZenLight(tpi, ZenAddress(ctrl, ZenAddressType.ECG, 3))
light3.set(level=100, fade=False)



# Loop forever
while True:
    time.sleep(1)
