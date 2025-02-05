from zen import ZenProtocol, ZenController, ZenAddress, ZenInstance, ZenColour, ZenColourType, ZenAddressType, ZenMotionSensor, ZenLight, ZenSystemVariable, ZenProfile
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
# def group_event(group: ZenGroup) -> None:
#     print(f"Group Event - group {group}")
# def button_event(button: ZenButton) -> None:
#     print(f"Button Event - button {button}")
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
    # group_callback=group_event,
    # button_callback=button_event,
    sysvar_callback=sysvar_event
)
tpi.start_event_monitoring()


# time.sleep(1)


# q = tpi.query_tpi_event_emit_state(ctrl)
# print(f"    query state: {q}")


# q = tpi.query_tpi_event_unicast_address(ctrl)
# print(f"    query unicast address: {q}")


# time.sleep(1)


ecg3 = ZenAddress(controller=ctrl, type=ZenAddressType.ECG, number=3)
tpi.dali_colour(ecg3, ZenColour(type=ZenColourType.TC, kelvin=2000))

q = tpi.query_dali_colour(ecg3)
print(f"    query dali colour: {q}")


# Motion sensors
motion_sensors = tpi.get_motion_sensors()
for motion_sensor in motion_sensors:
    print(f"  - {motion_sensor}")
    motion_sensor.hold_time = 5


# Loop forever
while True:
    time.sleep(1)
