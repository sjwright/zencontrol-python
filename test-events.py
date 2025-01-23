from zen import ZenProtocol, ZenController, ZenAddress, ZenInstance
import yaml
import time

config = yaml.safe_load(open("config.yaml"))
ctrl = ZenController(**config.get('zencontrol')[0])
tpi = ZenProtocol(controllers=[ctrl], narration=True)

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
def system_variable_change_event(controller: ZenController, system_variable:int, value:int, event_data: dict) -> None:
    print(f"System Variaable Change Event - controller {controller.name} system_variable {system_variable} value {value}")
def colour_change_event(address: ZenAddress, colour: bytes, event_data: dict) -> None:
    print(f"Colour Change Event - address {address.number} colour {colour}")
def profile_change_event(controller: ZenController, profile: int, event_data: dict) -> None:
    print(f"Profile Change Event - controller {controller.name} profile {profile}")

# Start event monitoring
tpi.start_event_monitoring(
    button_press_callback=button_press_event,
    button_hold_callback=button_hold_event,
    absolute_input_callback=absolute_input_event,
    level_change_callback=level_change_event,
    group_level_change_callback=group_level_change_event,
    scene_change_callback=scene_change_event,
    is_occupied_callback=is_occupied_event,
    system_variable_change_callback=system_variable_change_event,
    colour_change_callback=colour_change_event,
    profile_change_callback=profile_change_event
)

# Loop forever
while True:
    time.sleep(1)
